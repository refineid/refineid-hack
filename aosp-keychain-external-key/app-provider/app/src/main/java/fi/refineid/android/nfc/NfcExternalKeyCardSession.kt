package fi.refineid.android.nfc

import android.os.Looper
import fi.refineid.android.core.AuthenticationSignFailure
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.AuthenticationSigningInputMode
import fi.refineid.android.core.Pin1Submission
import fi.refineid.android.keychain.ExternalKeyCardIdentity
import fi.refineid.android.keychain.ExternalKeyCardSession
import fi.refineid.android.keychain.ExternalKeyProviderGeneration
import fi.refineid.android.usb.awaitPreservingInterrupt
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutorService
import java.util.concurrent.RejectedExecutionException

/** Blocking provider view over the worker-confined contactless session. */
internal class NfcExternalKeyCardSession(
    private val probeExecutor: ExecutorService,
    private val isAvailable: () -> Boolean,
    private val activeSession: () -> ContactlessSession?,
    private val activeProviderGeneration: () -> Long?,
    private val onCardLost: () -> Unit,
) : ExternalKeyCardSession {
    override fun copyActiveIdentity(): ExternalKeyCardIdentity? {
        if (Looper.myLooper() == Looper.getMainLooper() || !isAvailable()) {
            return null
        }
        val completion = CompletableFuture<ExternalKeyCardIdentity?>()
        try {
            probeExecutor.execute {
                val session = activeSession()
                val generation = activeProviderGeneration()
                val identity =
                    if (session != null && generation != null) {
                        try {
                            ExternalKeyCardIdentity(
                                providerGeneration = ExternalKeyProviderGeneration(generation),
                                certificate = session.copyAuthenticationCertificate(),
                            )
                        } catch (_: RuntimeException) {
                            null
                        }
                    } else {
                        null
                    }
                completion.complete(identity)
            }
        } catch (_: RejectedExecutionException) {
            return null
        }
        return completion.awaitPreservingInterrupt()
    }

    override fun signAuthenticationDigest(
        providerGeneration: ExternalKeyProviderGeneration,
        algorithm: AuthenticationSigningAlgorithm,
        pin1: Pin1Submission,
        digest: ByteArray,
    ): AuthenticationSignResult {
        if (Looper.myLooper() == Looper.getMainLooper() || !isAvailable()) {
            pin1.close()
            return unavailable()
        }
        val completion = CompletableFuture<AuthenticationSignResult>()
        try {
            probeExecutor.execute {
                val session = activeSession()
                val generation = activeProviderGeneration()
                val result =
                    try {
                        if (session != null && generation == providerGeneration.value) {
                            session.authenticateAndSignInput(
                                algorithm = algorithm,
                                inputMode = AuthenticationSigningInputMode.PREHASHED,
                                pin1 = pin1,
                                input = digest,
                            )
                        } else {
                            pin1.close()
                            unavailable()
                        }
                    } catch (_: RuntimeException) {
                        pin1.close()
                        AuthenticationSignResult.Failure(
                            AuthenticationSignFailure.BRIDGE_ERROR,
                        )
                    }
                if (
                    result is AuthenticationSignResult.Failure &&
                    result.kind == AuthenticationSignFailure.CARD_UNAVAILABLE
                ) {
                    onCardLost()
                }
                completion.complete(result)
            }
        } catch (_: RejectedExecutionException) {
            pin1.close()
            return unavailable()
        }
        return completion.awaitPreservingInterrupt()
    }

    override fun toString(): String = "NfcExternalKeyCardSession(available=" + isAvailable() + ")"

    private fun unavailable(): AuthenticationSignResult =
        AuthenticationSignResult.Failure(AuthenticationSignFailure.CARD_UNAVAILABLE)
}
