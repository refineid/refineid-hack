package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationIssuerCertificateSource
import fi.refineid.android.core.NativeCardKeyProfile
import fi.refineid.android.diagnostics.AppTrace
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadFactory
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** Card-backed provider state with generation-bound removal suppression. */
internal class RefineIdExternalKeyProviderBackend(
    private val cardSession: ExternalKeyCardSession,
    private val signingCoordinator: ExternalKeySigningCoordinator,
    private val issuerCertificateSource: AuthenticationIssuerCertificateSource,
) : ExternalKeyProviderBackend,
    AutoCloseable {
    private val identityLock = ReentrantLock(FAIR_IDENTITY_LOCK)
    private var isClosed = false
    private var suppressedGeneration: ExternalKeyProviderGeneration? = null

    override fun copyActiveIdentity(): ExternalKeyIdentitySnapshot? {
        val snapshot =
            identityLock.withLock {
                val identity = refreshCurrentIdentityLocked() ?: return@withLock null
                identity.use { currentIdentity ->
                    val leafCertificate = currentIdentity.certificate.copyDer()
                    val issuerCertificate = copyIssuerCertificate(leafCertificate)
                    try {
                        if (issuerCertificate == null) {
                            signingCoordinator.updateProviderGeneration(null)
                            return@withLock null
                        }
                        signingCoordinator.updateProviderGeneration(currentIdentity.providerGeneration)
                        ExternalKeyIdentitySnapshot.create(
                            providerGeneration = currentIdentity.providerGeneration,
                            leafCertificate = leafCertificate,
                            caCertificates = issuerCertificate,
                        )
                    } finally {
                        leafCertificate.fill(CLEARED_BYTE)
                        issuerCertificate?.fill(CLEARED_BYTE)
                    }
                }
            }
        AppTrace.externalKeyIdentityQueried(snapshot != null)
        return snapshot
    }

    override fun sign(request: ExternalKeySignRequest): ExternalKeySignResult {
        AppTrace.externalKeySignStarted(request.algorithm)
        val result = signCurrent(request)
        AppTrace.externalKeySignCompleted(result)
        return result
    }

    private fun signCurrent(request: ExternalKeySignRequest): ExternalKeySignResult {
        val metadata =
            identityLock.withLock {
                val identity = refreshCurrentIdentityLocked() ?: return@withLock null
                identity.use { currentIdentity ->
                    if (!hasCompleteCertificateChain(currentIdentity)) {
                        signingCoordinator.updateProviderGeneration(null)
                        return@withLock null
                    }
                    signingCoordinator.updateProviderGeneration(currentIdentity.providerGeneration)
                    ActiveIdentityMetadata(
                        providerGeneration = currentIdentity.providerGeneration,
                        keyProfile = currentIdentity.certificate.keyProfile,
                    )
                }
            } ?: return failure(ExternalKeySignFailure.PROVIDER_UNAVAILABLE)
        if (metadata.providerGeneration != request.providerGeneration) {
            return failure(ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED)
        }
        if (metadata.keyProfile != request.algorithm.keyProfile) {
            return failure(ExternalKeySignFailure.INVALID_REQUEST)
        }
        return signingCoordinator.sign(request)
    }

    override fun removeIdentity(providerGeneration: ExternalKeyProviderGeneration): Boolean {
        val isRemoved =
            identityLock.withLock {
                if (isClosed) {
                    return@withLock false
                }
                val identity = copyRawIdentityLocked() ?: return@withLock false
                identity.use { currentIdentity ->
                    val currentGeneration = currentIdentity.providerGeneration
                    clearSuppressionForNewGenerationLocked(currentGeneration)
                    val isPublishable =
                        currentIdentity.certificate.keyProfile.isSupportedExternalKeyProfile() &&
                            hasCompleteCertificateChain(currentIdentity)
                    if (
                        !isPublishable ||
                        currentGeneration != providerGeneration
                    ) {
                        updateCoordinatorForIdentityLocked(currentIdentity, isPublishable)
                        return@withLock false
                    }
                    suppressedGeneration = currentGeneration
                    signingCoordinator.updateProviderGeneration(null)
                    true
                }
            }
        AppTrace.externalKeyIdentityRemovalCompleted(isRemoved)
        return isRemoved
    }

    override fun close() {
        val shouldClose =
            identityLock.withLock {
                if (isClosed) {
                    false
                } else {
                    isClosed = true
                    suppressedGeneration = null
                    true
                }
            }
        if (shouldClose) {
            signingCoordinator.close()
        }
    }

    override fun toString(): String =
        identityLock.withLock {
            "RefineIdExternalKeyProviderBackend(" +
                "suppressed=" + (suppressedGeneration != null) +
                ", closed=" + isClosed +
                ")"
        }

    private fun refreshCurrentIdentityLocked(): ExternalKeyCardIdentity? {
        if (isClosed) {
            return null
        }
        val identity = copyRawIdentityLocked() ?: return null
        clearSuppressionForNewGenerationLocked(identity.providerGeneration)
        if (
            suppressedGeneration == identity.providerGeneration ||
            !identity.certificate.keyProfile.isSupportedExternalKeyProfile()
        ) {
            signingCoordinator.updateProviderGeneration(null)
            identity.close()
            return null
        }
        return identity
    }

    @Suppress("TooGenericExceptionCaught")
    private fun copyRawIdentityLocked(): ExternalKeyCardIdentity? =
        try {
            cardSession.copyActiveIdentity().also { identity ->
                if (identity == null) {
                    signingCoordinator.updateProviderGeneration(null)
                }
            }
        } catch (exception: RuntimeException) {
            signingCoordinator.updateProviderGeneration(null)
            throw exception
        }

    private fun clearSuppressionForNewGenerationLocked(currentGeneration: ExternalKeyProviderGeneration) {
        if (suppressedGeneration != null && suppressedGeneration != currentGeneration) {
            suppressedGeneration = null
        }
    }

    private fun updateCoordinatorForIdentityLocked(
        identity: ExternalKeyCardIdentity,
        isPublishable: Boolean,
    ) {
        val availableGeneration =
            identity.providerGeneration.takeIf {
                isPublishable &&
                    suppressedGeneration != identity.providerGeneration &&
                    identity.certificate.keyProfile.isSupportedExternalKeyProfile()
            }
        signingCoordinator.updateProviderGeneration(availableGeneration)
    }

    private fun hasCompleteCertificateChain(identity: ExternalKeyCardIdentity): Boolean {
        val leafCertificate = identity.certificate.copyDer()
        val issuerCertificate = copyIssuerCertificate(leafCertificate)
        return try {
            issuerCertificate != null
        } finally {
            leafCertificate.fill(CLEARED_BYTE)
            issuerCertificate?.fill(CLEARED_BYTE)
        }
    }

    private fun copyIssuerCertificate(leafCertificate: ByteArray): ByteArray? {
        val issuerCertificate =
            try {
                issuerCertificateSource.copyIssuerCertificate(leafCertificate)
            } catch (_: RuntimeException) {
                null
            } ?: return null
        if (issuerCertificate.isEmpty()) {
            issuerCertificate.fill(CLEARED_BYTE)
            return null
        }
        return issuerCertificate
    }

    private data class ActiveIdentityMetadata(
        val providerGeneration: ExternalKeyProviderGeneration,
        val keyProfile: NativeCardKeyProfile,
    )

    private companion object {
        const val FAIR_IDENTITY_LOCK = true
        const val CLEARED_BYTE: Byte = 0
    }
}

/** Owns the coordinator and short replay timer for the application process. */
internal class ExternalKeyProviderRuntime(
    cardSession: ExternalKeyCardSession,
    pinAuthorizer: ExternalKeyPinAuthorizer,
    issuerCertificateSource: AuthenticationIssuerCertificateSource,
    private val replayExecutor: ScheduledExecutorService = createReplayExecutor(),
) : AutoCloseable {
    private val isClosed = AtomicBoolean(false)
    private val replayLease =
        ExternalSignatureReplayLease(
            ScheduledExecutorReplayLeaseScheduler(replayExecutor),
        )
    private val signingCoordinator =
        ExternalKeySigningCoordinator(
            cardSigner = cardSession,
            pinAuthorizer = pinAuthorizer,
            replayLease = replayLease,
        )

    private val ownedBackend =
        RefineIdExternalKeyProviderBackend(
            cardSession = cardSession,
            signingCoordinator = signingCoordinator,
            issuerCertificateSource = issuerCertificateSource,
        )

    val backend: ExternalKeyProviderBackend
        get() = ownedBackend

    override fun close() {
        if (!isClosed.compareAndSet(false, true)) {
            return
        }
        ownedBackend.close()
        replayExecutor.shutdownNow()
    }

    override fun toString(): String = "ExternalKeyProviderRuntime(closed=" + isClosed.get() + ")"

    private companion object {
        const val REPLAY_THREAD_COUNT = 1
        const val REPLAY_THREAD_NAME = "refineid-external-key-replay"

        fun createReplayExecutor(): ScheduledExecutorService =
            ScheduledThreadPoolExecutor(
                REPLAY_THREAD_COUNT,
                ThreadFactory { runnable ->
                    Thread(runnable, REPLAY_THREAD_NAME).apply {
                        isDaemon = true
                    }
                },
            ).apply {
                removeOnCancelPolicy = true
                executeExistingDelayedTasksAfterShutdownPolicy = false
            }
    }
}

private fun NativeCardKeyProfile.isSupportedExternalKeyProfile(): Boolean =
    when (this) {
        NativeCardKeyProfile.RSA_3072,
        NativeCardKeyProfile.ECDSA_P384,
        -> true

        NativeCardKeyProfile.RSA_2048,
        NativeCardKeyProfile.ECDSA_P256,
        -> false
    }

private fun failure(kind: ExternalKeySignFailure): ExternalKeySignResult = ExternalKeySignResult.Failure(kind)
