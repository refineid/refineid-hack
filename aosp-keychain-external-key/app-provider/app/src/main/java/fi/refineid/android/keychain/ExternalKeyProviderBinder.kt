package fi.refineid.android.keychain

import android.os.Binder
import android.os.IBinder
import android.os.Process
import android.os.RemoteException
import com.android.keychain.external.ExternalKeyProviderIdentity
import com.android.keychain.external.ExternalKeyProviderResult
import com.android.keychain.external.IExternalKeyProviderService
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.P384EcdsaSignature
import java.util.concurrent.atomic.AtomicBoolean

internal fun interface KeyChainCallerVerifier {
    fun enforceTrustedCaller()
}

internal object AndroidKeyChainCallerVerifier : KeyChainCallerVerifier {
    override fun enforceTrustedCaller() {
        // AOSP UserHandle.PER_USER_RANGE is hidden from ordinary SDK clients.
        val callingAppId = Math.floorMod(Binder.getCallingUid(), ANDROID_UIDS_PER_USER)
        if (callingAppId != Process.SYSTEM_UID) {
            throw SecurityException("external-key provider caller is not KeyChain")
        }
    }

    private const val ANDROID_UIDS_PER_USER = 100_000
}

internal fun interface OperationLivenessFactory {
    fun attach(
        token: IBinder,
        cancellation: ExternalKeyOperationCancellation,
    ): AutoCloseable
}

internal object BinderOperationLivenessFactory : OperationLivenessFactory {
    override fun attach(
        token: IBinder,
        cancellation: ExternalKeyOperationCancellation,
    ): AutoCloseable = BinderOperationLiveness.attach(token, cancellation)
}

/** Links one browser-owned opaque token to an Android-free cancellation signal. */
private class BinderOperationLiveness private constructor(
    private val token: IBinder,
    private val deathRecipient: IBinder.DeathRecipient,
) : AutoCloseable {
    private val isClosed = AtomicBoolean(false)

    override fun close() {
        if (!isClosed.compareAndSet(false, true)) {
            return
        }
        try {
            token.unlinkToDeath(deathRecipient, NO_BINDER_FLAGS)
        } catch (_: RuntimeException) {
            // A dead binder has already unlinked itself.
        }
    }

    companion object {
        fun attach(
            token: IBinder,
            cancellation: ExternalKeyOperationCancellation,
        ): BinderOperationLiveness {
            val deathRecipient = IBinder.DeathRecipient(cancellation::cancel)
            val liveness = BinderOperationLiveness(token, deathRecipient)
            try {
                token.linkToDeath(deathRecipient, NO_BINDER_FLAGS)
            } catch (_: RemoteException) {
                cancellation.cancel()
                return liveness
            }
            if (!token.isBinderAlive) {
                cancellation.cancel()
            }
            return liveness
        }

        private const val NO_BINDER_FLAGS = 0
    }
}

/** Strict, zeroizing implementation of the private KeyChain provider protocol. */
internal class ExternalKeyProviderBinder(
    private val backend: ExternalKeyProviderBackend,
    private val callerVerifier: KeyChainCallerVerifier = AndroidKeyChainCallerVerifier,
    private val livenessFactory: OperationLivenessFactory = BinderOperationLivenessFactory,
) : IExternalKeyProviderService.Stub() {
    override fun getProtocolVersion(): Int {
        callerVerifier.enforceTrustedCaller()
        return IExternalKeyProviderService.PROTOCOL_VERSION
    }

    override fun getActiveIdentity(): ExternalKeyProviderIdentity? {
        callerVerifier.enforceTrustedCaller()
        return try {
            val identity = backend.copyActiveIdentity() ?: return null
            identity.use { ownedIdentity ->
                val leafCertificate = ownedIdentity.copyLeafCertificate()
                val caCertificates = ownedIdentity.copyCaCertificates()
                try {
                    ExternalKeyProviderIdentity.create(
                        leafCertificate,
                        caCertificates,
                        ownedIdentity.providerGeneration.value,
                    )
                } finally {
                    leafCertificate.fill(CLEARED_BYTE)
                    caCertificates?.fill(CLEARED_BYTE)
                }
            }
        } catch (_: RuntimeException) {
            null
        }
    }

    override fun sign(
        callerUid: Int,
        callerPackages: Array<out String>?,
        externalAlias: String?,
        providerGeneration: Long,
        algorithm: Int,
        digest: ByteArray?,
        operationToken: IBinder?,
    ): ExternalKeyProviderResult {
        callerVerifier.enforceTrustedCaller()
        if (
            callerPackages == null ||
            externalAlias == null ||
            digest == null ||
            operationToken == null
        ) {
            digest?.fill(CLEARED_BYTE)
            return failure(ExternalKeyProviderResult.FAILURE_INVALID_REQUEST)
        }

        val cancellation = ExternalKeyOperationCancellation()
        val liveness =
            try {
                livenessFactory.attach(operationToken, cancellation)
            } catch (_: RuntimeException) {
                digest.fill(CLEARED_BYTE)
                return failure(ExternalKeyProviderResult.FAILURE_INTERNAL_ERROR)
            }
        var request: ExternalKeySignRequest? = null
        return try {
            request =
                try {
                    val signingAlgorithm =
                        algorithm.toAuthenticationSigningAlgorithm()
                            ?: return failure(ExternalKeyProviderResult.FAILURE_INVALID_REQUEST)
                    val alias =
                        ExternalKeyAlias.fromWireValue(externalAlias)
                            ?: return failure(ExternalKeyProviderResult.FAILURE_INVALID_REQUEST)
                    ExternalKeySignRequest.create(
                        caller =
                            ExternalKeyCaller.create(
                                uid =
                                    ExternalKeyCallerUid(callerUid).also {
                                        require(Process.isApplicationUid(callerUid)) {
                                            "external-key caller is not an application UID"
                                        }
                                    },
                                packageNames = callerPackages,
                            ),
                        alias = alias,
                        providerGeneration = ExternalKeyProviderGeneration(providerGeneration),
                        algorithm = signingAlgorithm,
                        digest = digest,
                        cancellation = cancellation,
                    )
                } catch (_: IllegalArgumentException) {
                    return failure(ExternalKeyProviderResult.FAILURE_INVALID_REQUEST)
                }
            if (cancellation.isCancelled) {
                return failure(ExternalKeyProviderResult.FAILURE_CALLER_INTERRUPTED)
            }
            val result = backend.sign(request)
            result.toProviderResult(cancellation)
        } catch (_: RuntimeException) {
            failure(ExternalKeyProviderResult.FAILURE_INTERNAL_ERROR)
        } finally {
            request?.close()
            digest.fill(CLEARED_BYTE)
            liveness.close()
        }
    }

    override fun removeIdentity(providerGeneration: Long): Boolean {
        callerVerifier.enforceTrustedCaller()
        return try {
            backend.removeIdentity(ExternalKeyProviderGeneration(providerGeneration))
        } catch (_: RuntimeException) {
            false
        }
    }

    private fun ExternalKeySignResult.toProviderResult(
        cancellation: ExternalKeyOperationCancellation,
    ): ExternalKeyProviderResult =
        when (this) {
            is ExternalKeySignResult.Success -> {
                if (cancellation.isCancelled) {
                    signature.close()
                    failure(ExternalKeyProviderResult.FAILURE_CALLER_INTERRUPTED)
                } else {
                    val signatureBytes =
                        try {
                            when (signature.algorithm) {
                                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256,
                                AuthenticationSigningAlgorithm.RSA_PSS_SHA256,
                                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA384,
                                AuthenticationSigningAlgorithm.RSA_PSS_SHA384,
                                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA512,
                                AuthenticationSigningAlgorithm.RSA_PSS_SHA512,
                                -> signature.copyBytes()

                                AuthenticationSigningAlgorithm.ECDSA_P384_SHA256,
                                AuthenticationSigningAlgorithm.ECDSA_P384_SHA384,
                                -> signature.useBytes(P384EcdsaSignature::toDer)
                            }
                        } finally {
                            signature.close()
                        }
                    try {
                        ExternalKeyProviderResult.success(signatureBytes)
                    } finally {
                        signatureBytes.fill(CLEARED_BYTE)
                    }
                }
            }

            is ExternalKeySignResult.Failure -> {
                failure(kind.toProviderFailure())
            }
        }

    @ExternalKeyProviderResult.Failure
    private fun ExternalKeySignFailure.toProviderFailure(): Int =
        when (this) {
            ExternalKeySignFailure.INVALID_REQUEST -> {
                ExternalKeyProviderResult.FAILURE_INVALID_REQUEST
            }

            ExternalKeySignFailure.PROVIDER_UNAVAILABLE -> {
                ExternalKeyProviderResult.FAILURE_PROVIDER_UNAVAILABLE
            }

            ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED -> {
                ExternalKeyProviderResult.FAILURE_PROVIDER_GENERATION_CHANGED
            }

            ExternalKeySignFailure.USER_CANCELLED -> {
                ExternalKeyProviderResult.FAILURE_USER_CANCELLED
            }

            ExternalKeySignFailure.USER_TIMED_OUT -> {
                ExternalKeyProviderResult.FAILURE_USER_TIMED_OUT
            }

            ExternalKeySignFailure.CALLER_INTERRUPTED -> {
                ExternalKeyProviderResult.FAILURE_CALLER_INTERRUPTED
            }

            ExternalKeySignFailure.SIGNING_FAILED -> {
                ExternalKeyProviderResult.FAILURE_SIGNING_FAILED
            }

            ExternalKeySignFailure.INTERNAL_ERROR -> {
                ExternalKeyProviderResult.FAILURE_INTERNAL_ERROR
            }
        }

    private fun Int.toAuthenticationSigningAlgorithm(): AuthenticationSigningAlgorithm? =
        when (this) {
            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256 -> {
                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA256 -> {
                AuthenticationSigningAlgorithm.RSA_PSS_SHA256
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_ECDSA_P384_SHA256 -> {
                AuthenticationSigningAlgorithm.ECDSA_P384_SHA256
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_ECDSA_P384_SHA384 -> {
                AuthenticationSigningAlgorithm.ECDSA_P384_SHA384
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA384 -> {
                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA384
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA384 -> {
                AuthenticationSigningAlgorithm.RSA_PSS_SHA384
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA512 -> {
                AuthenticationSigningAlgorithm.RSA_PKCS1_SHA512
            }

            IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA512 -> {
                AuthenticationSigningAlgorithm.RSA_PSS_SHA512
            }

            else -> {
                null
            }
        }

    private fun failure(
        @ExternalKeyProviderResult.Failure failure: Int,
    ): ExternalKeyProviderResult = ExternalKeyProviderResult.failure(failure)

    private companion object {
        const val CLEARED_BYTE: Byte = 0
    }
}
