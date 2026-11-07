package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSignFailure
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationCertificate
import fi.refineid.android.core.NativeCardKeyProfile
import fi.refineid.android.core.Pin1Submission
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class TransportSelectingCardSessionTest {
    private class ScriptedSession(
        private val generation: Long?,
    ) : ExternalKeyCardSession {
        var signCount = 0
            private set

        override fun copyActiveIdentity(): ExternalKeyCardIdentity? {
            val currentGeneration = generation ?: return null
            return ExternalKeyCardIdentity(
                providerGeneration = ExternalKeyProviderGeneration(currentGeneration),
                certificate =
                    NativeAuthenticationCertificate(
                        keyProfile = NativeCardKeyProfile.RSA_3072,
                        ownedDer = SYNTHETIC_DER.copyOf(),
                    ),
            )
        }

        override fun signAuthenticationDigest(
            providerGeneration: ExternalKeyProviderGeneration,
            algorithm: AuthenticationSigningAlgorithm,
            pin1: Pin1Submission,
            digest: ByteArray,
        ): AuthenticationSignResult {
            signCount += 1
            pin1.close()
            return AuthenticationSignResult.Failure(AuthenticationSignFailure.SAFETY_REFUSED)
        }
    }

    @Test
    fun prefersTheFirstSessionWithACard() {
        val selector =
            TransportSelectingCardSession(
                listOf(
                    ScriptedSession(generation = null),
                    ScriptedSession(generation = SECONDARY_GENERATION),
                ),
            )

        val identity = selector.copyActiveIdentity()

        assertNotNull(identity)
        assertEquals(
            ExternalKeyProviderGeneration(SECONDARY_GENERATION),
            identity?.providerGeneration,
        )
        identity?.close()
    }

    @Test
    fun reportsNoIdentityWhenNoTransportHoldsACard() {
        val selector =
            TransportSelectingCardSession(
                listOf(
                    ScriptedSession(generation = null),
                    ScriptedSession(generation = null),
                ),
            )

        assertNull(selector.copyActiveIdentity())
    }

    @Test
    fun namesTheIdentityOfTheTransportHoldingAGeneration() {
        val selector =
            TransportSelectingCardSession(
                listOf(
                    ScriptedSession(generation = PRIMARY_GENERATION),
                    ScriptedSession(generation = SECONDARY_GENERATION),
                ),
            )

        val identity = selector.copyIdentity(ExternalKeyProviderGeneration(SECONDARY_GENERATION))

        assertEquals(ExternalKeyProviderGeneration(SECONDARY_GENERATION), identity?.providerGeneration)
        identity?.close()
        assertNull(selector.copyIdentity(ExternalKeyProviderGeneration(UNKNOWN_GENERATION)))
    }

    @Test
    fun routesSigningByProviderGeneration() {
        val primary = ScriptedSession(generation = PRIMARY_GENERATION)
        val secondary = ScriptedSession(generation = SECONDARY_GENERATION)
        val selector = TransportSelectingCardSession(listOf(primary, secondary))

        selector.signAuthenticationDigest(
            providerGeneration = ExternalKeyProviderGeneration(SECONDARY_GENERATION),
            algorithm = AuthenticationSigningAlgorithm.RSA_PSS_SHA256,
            pin1 = Pin1Submission.from(SYNTHETIC_PIN),
            digest = ByteArray(SHA256_DIGEST_LENGTH),
        )

        assertEquals(0, primary.signCount)
        assertEquals(1, secondary.signCount)
    }

    @Test
    fun anUnknownGenerationClosesThePinWithoutSigning() {
        val primary = ScriptedSession(generation = PRIMARY_GENERATION)
        val selector = TransportSelectingCardSession(listOf(primary))
        val pin = Pin1Submission.from(SYNTHETIC_PIN)

        val result =
            selector.signAuthenticationDigest(
                providerGeneration = ExternalKeyProviderGeneration(UNKNOWN_GENERATION),
                algorithm = AuthenticationSigningAlgorithm.RSA_PSS_SHA256,
                pin1 = pin,
                digest = ByteArray(SHA256_DIGEST_LENGTH),
            )

        assertEquals(
            AuthenticationSignResult.Failure(AuthenticationSignFailure.CARD_UNAVAILABLE),
            result,
        )
        assertEquals(0, primary.signCount)
        assertThrows(IllegalStateException::class.java) {
            pin.consume { _ -> }
        }
    }

    private companion object {
        const val PRIMARY_GENERATION = 11L
        const val SECONDARY_GENERATION = 22L
        const val UNKNOWN_GENERATION = 33L
        const val SHA256_DIGEST_LENGTH = 32

        // A syntactically valid but meaningless PIN for shape tests only.
        const val SYNTHETIC_PIN = "0000"
        val SYNTHETIC_DER = byteArrayOf(0x30, 0x00)
    }
}
