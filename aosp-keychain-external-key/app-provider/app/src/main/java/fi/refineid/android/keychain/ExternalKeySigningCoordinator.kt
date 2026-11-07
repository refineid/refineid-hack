package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSignFailure
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.NativeAuthenticationSignature
import fi.refineid.android.core.Pin1Submission
import java.util.concurrent.locks.ReentrantLock

internal sealed interface ExternalKeyPinAuthorization {
    class Approved(
        val pin1: Pin1Submission,
    ) : ExternalKeyPinAuthorization {
        override fun toString(): String = "Approved([redacted])"
    }

    data object Cancelled : ExternalKeyPinAuthorization

    data object TimedOut : ExternalKeyPinAuthorization

    data object Interrupted : ExternalKeyPinAuthorization

    data object Unavailable : ExternalKeyPinAuthorization
}

/** Secure-UI boundary. Implementations resolve the caller UID to holder-facing app text. */
internal interface ExternalKeyPinAuthorizer : AutoCloseable {
    fun authorize(request: ExternalKeySignRequest): ExternalKeyPinAuthorization

    fun cancelPending()

    override fun close() {
        cancelPending()
    }
}

internal enum class ExternalKeySignFailure {
    INVALID_REQUEST,
    PROVIDER_UNAVAILABLE,
    PROVIDER_GENERATION_CHANGED,
    USER_CANCELLED,
    USER_TIMED_OUT,
    CALLER_INTERRUPTED,
    SIGNING_FAILED,
    INTERNAL_ERROR,
}

internal sealed interface ExternalKeySignResult {
    class Success(
        val signature: NativeAuthenticationSignature,
        val isReplay: Boolean,
    ) : ExternalKeySignResult {
        override fun toString(): String =
            "Success(algorithm=" + signature.algorithm +
                ", length=" + signature.length +
                ", replay=" + isReplay + ")"
    }

    data class Failure(
        val kind: ExternalKeySignFailure,
    ) : ExternalKeySignResult
}

/**
 * Serializes trusted KeyChain requests, obtains fresh holder consent, and
 * grants at most one exact replay of a locally verified digest signature.
 */
internal class ExternalKeySigningCoordinator(
    private val cardSigner: ExternalKeyCardSigner,
    private val pinAuthorizer: ExternalKeyPinAuthorizer,
    private val replayLease: ExternalSignatureReplayLease,
    initialProviderGeneration: ExternalKeyProviderGeneration? = null,
) : AutoCloseable {
    private val operationLock = ReentrantLock(FAIR_OPERATION_LOCK)
    private val stateLock = Any()
    private var isClosed = false
    private var providerGeneration = initialProviderGeneration

    fun updateProviderGeneration(generation: ExternalKeyProviderGeneration?) {
        val changed =
            synchronized(stateLock) {
                if (isClosed || providerGeneration == generation) {
                    false
                } else {
                    providerGeneration = generation
                    replayLease.invalidate()
                    true
                }
            }
        if (changed) {
            pinAuthorizer.cancelPending()
        }
    }

    fun sign(request: ExternalKeySignRequest): ExternalKeySignResult {
        if (request.cancellation.isCancelled) {
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        try {
            operationLock.lockInterruptibly()
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        return try {
            signLocked(request)
        } finally {
            operationLock.unlock()
        }
    }

    override fun close() {
        val changed =
            synchronized(stateLock) {
                if (isClosed) {
                    false
                } else {
                    isClosed = true
                    providerGeneration = null
                    replayLease.close()
                    true
                }
            }
        if (changed) {
            pinAuthorizer.close()
        }
    }

    override fun toString(): String =
        synchronized(stateLock) {
            "ExternalKeySigningCoordinator(" +
                "available=" + (providerGeneration != null) +
                ", closed=" + isClosed +
                ")"
        }

    @Suppress("TooGenericExceptionCaught")
    private fun signLocked(request: ExternalKeySignRequest): ExternalKeySignResult {
        if (request.cancellation.isCancelled) {
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        when (availability(request)) {
            ProviderAvailability.CURRENT -> {
                // The current provider proceeds.
            }

            ProviderAvailability.UNAVAILABLE -> {
                return failure(ExternalKeySignFailure.PROVIDER_UNAVAILABLE)
            }

            ProviderAvailability.GENERATION_CHANGED -> {
                return failure(ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED)
            }
        }

        val replay =
            try {
                replayLease.take(request)
            } catch (_: Exception) {
                return failure(ExternalKeySignFailure.INVALID_REQUEST)
            }
        if (replay != null) {
            if (request.cancellation.isCancelled) {
                replay.close()
                return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
            }
            return ExternalKeySignResult.Success(
                signature = replay,
                isReplay = true,
            )
        }

        val authorization =
            try {
                pinAuthorizer.authorize(request)
            } catch (_: Exception) {
                return failure(ExternalKeySignFailure.INTERNAL_ERROR)
            }
        when (availability(request)) {
            ProviderAvailability.CURRENT -> {
                // The current provider proceeds.
            }

            ProviderAvailability.UNAVAILABLE -> {
                if (authorization is ExternalKeyPinAuthorization.Approved) {
                    authorization.pin1.close()
                }
                return failure(ExternalKeySignFailure.PROVIDER_UNAVAILABLE)
            }

            ProviderAvailability.GENERATION_CHANGED -> {
                if (authorization is ExternalKeyPinAuthorization.Approved) {
                    authorization.pin1.close()
                }
                return failure(ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED)
            }
        }
        if (request.cancellation.isCancelled) {
            if (authorization is ExternalKeyPinAuthorization.Approved) {
                authorization.pin1.close()
            }
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        return when (authorization) {
            is ExternalKeyPinAuthorization.Approved -> {
                signApproved(request, authorization.pin1)
            }

            ExternalKeyPinAuthorization.Cancelled -> {
                failure(ExternalKeySignFailure.USER_CANCELLED)
            }

            ExternalKeyPinAuthorization.TimedOut -> {
                failure(ExternalKeySignFailure.USER_TIMED_OUT)
            }

            ExternalKeyPinAuthorization.Interrupted -> {
                failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
            }

            ExternalKeyPinAuthorization.Unavailable -> {
                failure(ExternalKeySignFailure.PROVIDER_UNAVAILABLE)
            }
        }
    }

    @Suppress("TooGenericExceptionCaught")
    private fun signApproved(
        request: ExternalKeySignRequest,
        pin1: Pin1Submission,
    ): ExternalKeySignResult {
        val preconditionFailure = request.approvedSigningPreconditionFailure()
        if (preconditionFailure != null) {
            pin1.close()
            return failure(preconditionFailure)
        }
        val digest =
            try {
                request.copyDigest()
            } catch (_: Exception) {
                pin1.close()
                return failure(ExternalKeySignFailure.INVALID_REQUEST)
            }
        val cardResult =
            try {
                cardSigner.signAuthenticationDigest(
                    providerGeneration = request.providerGeneration,
                    algorithm = request.algorithm,
                    pin1 = pin1,
                    digest = digest,
                )
            } catch (_: Exception) {
                return failure(ExternalKeySignFailure.INTERNAL_ERROR)
            } finally {
                digest.fill(CLEARED_BYTE)
                pin1.close()
            }
        return completeCardResult(request, cardResult)
    }

    @Suppress("TooGenericExceptionCaught")
    private fun completeCardResult(
        request: ExternalKeySignRequest,
        cardResult: AuthenticationSignResult,
    ): ExternalKeySignResult {
        if (request.cancellation.isCancelled) {
            if (cardResult is AuthenticationSignResult.Success) {
                cardResult.signature.close()
            }
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        if (cardResult is AuthenticationSignResult.Failure) {
            return failure(cardResult.kind.toExternalFailure())
        }
        val signature = (cardResult as AuthenticationSignResult.Success).signature
        if (
            signature.algorithm != request.algorithm ||
            signature.length != request.algorithm.signatureLength
        ) {
            signature.close()
            return failure(ExternalKeySignFailure.INTERNAL_ERROR)
        }
        val replayBytes = signature.copyBytes()
        val retainedAvailability =
            try {
                retainReplayIfCurrent(request, replayBytes)
            } catch (_: Exception) {
                signature.close()
                return failure(ExternalKeySignFailure.INTERNAL_ERROR)
            }
        when (retainedAvailability) {
            ProviderAvailability.CURRENT -> {
                // The current provider proceeds.
            }

            ProviderAvailability.UNAVAILABLE -> {
                signature.close()
                return failure(ExternalKeySignFailure.PROVIDER_UNAVAILABLE)
            }

            ProviderAvailability.GENERATION_CHANGED -> {
                signature.close()
                return failure(ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED)
            }
        }
        if (request.cancellation.isCancelled) {
            replayLease.invalidate()
            signature.close()
            return failure(ExternalKeySignFailure.CALLER_INTERRUPTED)
        }
        return ExternalKeySignResult.Success(
            signature = signature,
            isReplay = false,
        )
    }

    private fun ExternalKeySignRequest.approvedSigningPreconditionFailure(): ExternalKeySignFailure? =
        when {
            cancellation.isCancelled -> {
                ExternalKeySignFailure.CALLER_INTERRUPTED
            }

            else -> {
                when (availability(this)) {
                    ProviderAvailability.CURRENT -> {
                        null
                    }

                    ProviderAvailability.UNAVAILABLE -> {
                        ExternalKeySignFailure.PROVIDER_UNAVAILABLE
                    }

                    ProviderAvailability.GENERATION_CHANGED -> {
                        ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED
                    }
                }
            }
        }

    private fun availability(request: ExternalKeySignRequest): ProviderAvailability =
        synchronized(stateLock) {
            availabilityLocked(request)
        }

    /**
     * Commits a replay at the same linearization point used for provider state.
     * Generation changes either precede this commit or invalidate it afterwards.
     */
    private fun retainReplayIfCurrent(
        request: ExternalKeySignRequest,
        ownedSignatureBytes: ByteArray,
    ): ProviderAvailability =
        synchronized(stateLock) {
            val availability = availabilityLocked(request)
            if (availability == ProviderAvailability.CURRENT) {
                replayLease.retain(request, ownedSignatureBytes)
            } else {
                ownedSignatureBytes.fill(CLEARED_BYTE)
            }
            availability
        }

    private fun availabilityLocked(request: ExternalKeySignRequest): ProviderAvailability =
        when {
            isClosed || providerGeneration == null -> {
                ProviderAvailability.UNAVAILABLE
            }

            providerGeneration != request.providerGeneration -> {
                ProviderAvailability.GENERATION_CHANGED
            }

            else -> {
                ProviderAvailability.CURRENT
            }
        }

    private fun AuthenticationSignFailure.toExternalFailure(): ExternalKeySignFailure =
        when (this) {
            AuthenticationSignFailure.CARD_UNAVAILABLE,
            AuthenticationSignFailure.TRANSPORT_ERROR,
            -> ExternalKeySignFailure.PROVIDER_UNAVAILABLE

            AuthenticationSignFailure.INVALID_PIN,
            AuthenticationSignFailure.SAFETY_REFUSED,
            AuthenticationSignFailure.PIN_LOCKED,
            AuthenticationSignFailure.WRONG_PIN,
            AuthenticationSignFailure.VERIFICATION_REJECTED,
            AuthenticationSignFailure.SIGNING_REJECTED,
            AuthenticationSignFailure.KEY_PROFILE_MISMATCH,
            AuthenticationSignFailure.LOCAL_VERIFICATION_FAILED,
            AuthenticationSignFailure.BRIDGE_ERROR,
            -> ExternalKeySignFailure.SIGNING_FAILED
        }

    private fun failure(kind: ExternalKeySignFailure): ExternalKeySignResult.Failure =
        ExternalKeySignResult.Failure(kind)

    private enum class ProviderAvailability {
        CURRENT,
        UNAVAILABLE,
        GENERATION_CHANGED,
    }

    private companion object {
        const val FAIR_OPERATION_LOCK = true
        const val CLEARED_BYTE: Byte = 0
    }
}
