package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationIssuerCertificateSource
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationCertificate
import fi.refineid.android.core.NativeAuthenticationSignature
import fi.refineid.android.core.NativeCardKeyProfile
import fi.refineid.android.core.Pin1Submission
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RefineIdExternalKeyProviderBackendTest {
    @Test
    fun publishesSupportedIdentityAndSignsOnlyItsGenerationAndProfile() {
        val fixture = Fixture()

        fixture.backend.copyActiveIdentity().use { identity ->
            checkNotNull(identity)
            assertEquals(CURRENT_PROVIDER_GENERATION, identity.providerGeneration.value)
            assertArrayEquals(SYNTHETIC_CERTIFICATE, identity.copyLeafCertificate())
            assertArrayEquals(SYNTHETIC_ISSUER_CERTIFICATE, identity.copyCaCertificates())
        }

        val currentRequest = request()
        fixture.backend.sign(currentRequest).useSuccess()
        assertEquals(SINGLE_OPERATION, fixture.authorizer.authorizationCount)
        assertEquals(SINGLE_OPERATION, fixture.cardSession.signCount)
        assertEquals(CURRENT_PROVIDER_GENERATION, fixture.cardSession.signedGeneration)

        val staleRequest = request(providerGeneration = STALE_PROVIDER_GENERATION)
        assertEquals(
            ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED,
            fixture.backend.sign(staleRequest).failureKind(),
        )
        val wrongProfileRequest = request(algorithm = AuthenticationSigningAlgorithm.ECDSA_P384_SHA256)
        assertEquals(
            ExternalKeySignFailure.INVALID_REQUEST,
            fixture.backend.sign(wrongProfileRequest).failureKind(),
        )
        assertEquals(SINGLE_OPERATION, fixture.authorizer.authorizationCount)
        assertEquals(SINGLE_OPERATION, fixture.cardSession.signCount)

        currentRequest.close()
        staleRequest.close()
        wrongProfileRequest.close()
        fixture.close()
    }

    @Test
    fun removalSuppressesOnlyTheMatchingPhysicalGeneration() {
        val fixture = Fixture()

        assertTrue(fixture.backend.removeIdentity(ExternalKeyProviderGeneration(CURRENT_PROVIDER_GENERATION)))
        assertNull(fixture.backend.copyActiveIdentity())
        val suppressedRequest = request()
        assertEquals(
            ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            fixture.backend.sign(suppressedRequest).failureKind(),
        )

        fixture.cardSession.providerGeneration = NEXT_PROVIDER_GENERATION
        fixture.backend.copyActiveIdentity().use { identity ->
            checkNotNull(identity)
            assertEquals(NEXT_PROVIDER_GENERATION, identity.providerGeneration.value)
        }
        assertFalse(
            fixture.backend.removeIdentity(
                ExternalKeyProviderGeneration(CURRENT_PROVIDER_GENERATION),
            ),
        )
        fixture.backend.copyActiveIdentity().use { identity ->
            checkNotNull(identity)
            assertEquals(NEXT_PROVIDER_GENERATION, identity.providerGeneration.value)
        }

        suppressedRequest.close()
        fixture.close()
    }

    @Test
    fun unsupportedAndAbsentCardsAreNeverPublished() {
        val fixture = Fixture(keyProfile = NativeCardKeyProfile.RSA_2048)

        assertNull(fixture.backend.copyActiveIdentity())
        fixture.cardSession.isPresent = false
        assertFalse(
            fixture.backend.removeIdentity(
                ExternalKeyProviderGeneration(CURRENT_PROVIDER_GENERATION),
            ),
        )

        fixture.close()
    }

    @Test
    fun missingIssuerPreventsPublicationRemovalAndSigning() {
        val fixture =
            Fixture(
                issuerCertificateSource = AuthenticationIssuerCertificateSource { null },
            )
        val request = request()

        assertNull(fixture.backend.copyActiveIdentity())
        assertFalse(
            fixture.backend.removeIdentity(
                ExternalKeyProviderGeneration(CURRENT_PROVIDER_GENERATION),
            ),
        )
        assertEquals(
            ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            fixture.backend.sign(request).failureKind(),
        )
        assertEquals(NO_OPERATIONS, fixture.authorizer.authorizationCount)
        assertEquals(NO_OPERATIONS, fixture.cardSession.signCount)

        request.close()
        fixture.close()
    }

    @Test
    fun generationChangeAtWorkerBoundaryFailsWithoutUsingThePin() {
        val fixture = Fixture()
        val request = request()
        fixture.cardSession.generationBeforeSign = NEXT_PROVIDER_GENERATION

        assertEquals(
            ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            fixture.backend.sign(request).failureKind(),
        )
        assertEquals(SINGLE_OPERATION, fixture.authorizer.authorizationCount)
        assertEquals(NO_OPERATIONS, fixture.cardSession.signCount)
        assertTrue(fixture.cardSession.pinWasClosed)

        request.close()
        fixture.close()
    }

    private class Fixture(
        keyProfile: NativeCardKeyProfile = NativeCardKeyProfile.RSA_3072,
        issuerCertificateSource: AuthenticationIssuerCertificateSource = SYNTHETIC_ISSUER_SOURCE,
    ) : AutoCloseable {
        val cardSession = RecordingCardSession(keyProfile)
        val authorizer = RecordingAuthorizer()
        private val coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardSession,
                pinAuthorizer = authorizer,
                replayLease = ExternalSignatureReplayLease(InertReplayScheduler),
            )
        val backend =
            RefineIdExternalKeyProviderBackend(
                cardSession = cardSession,
                signingCoordinator = coordinator,
                issuerCertificateSource = issuerCertificateSource,
            )

        override fun close() {
            backend.close()
        }
    }

    private class RecordingCardSession(
        var keyProfile: NativeCardKeyProfile,
    ) : ExternalKeyCardSession {
        var isPresent = true
        var providerGeneration = CURRENT_PROVIDER_GENERATION
        var generationBeforeSign: Long? = null
        var signCount = NO_OPERATIONS
        var signedGeneration: Long? = null
        var pinWasClosed = false

        override fun copyActiveIdentity(): ExternalKeyCardIdentity? =
            if (isPresent) {
                ExternalKeyCardIdentity(
                    providerGeneration = ExternalKeyProviderGeneration(providerGeneration),
                    certificate =
                        NativeAuthenticationCertificate(
                            keyProfile = keyProfile,
                            ownedDer = SYNTHETIC_CERTIFICATE.copyOf(),
                        ),
                )
            } else {
                null
            }

        override fun signAuthenticationDigest(
            providerGeneration: ExternalKeyProviderGeneration,
            algorithm: AuthenticationSigningAlgorithm,
            pin1: Pin1Submission,
            digest: ByteArray,
        ): AuthenticationSignResult {
            generationBeforeSign?.let { changedGeneration ->
                this.providerGeneration = changedGeneration
            }
            if (this.providerGeneration != providerGeneration.value) {
                pin1.close()
                pinWasClosed = true
                return AuthenticationSignResult.Failure(
                    fi.refineid.android.core.AuthenticationSignFailure.CARD_UNAVAILABLE,
                )
            }
            signCount += SINGLE_OPERATION
            signedGeneration = providerGeneration.value
            pin1.consume { Unit }
            return AuthenticationSignResult.Success(
                NativeAuthenticationSignature(
                    algorithm = algorithm,
                    ownedBytes = ByteArray(algorithm.signatureLength) { SIGNATURE_FILL },
                ),
            )
        }
    }

    private class RecordingAuthorizer : ExternalKeyPinAuthorizer {
        var authorizationCount = NO_OPERATIONS

        override fun authorize(request: ExternalKeySignRequest): ExternalKeyPinAuthorization {
            authorizationCount += SINGLE_OPERATION
            return ExternalKeyPinAuthorization.Approved(
                Pin1Submission.from(SYNTHETIC_PIN1),
            )
        }

        override fun cancelPending() = Unit
    }

    private data object InertReplayScheduler : ReplayLeaseScheduler {
        override fun schedule(
            delayMilliseconds: Long,
            action: () -> Unit,
        ): ReplayLeaseCancellation = ReplayLeaseCancellation {}
    }

    private fun request(
        providerGeneration: Long = CURRENT_PROVIDER_GENERATION,
        algorithm: AuthenticationSigningAlgorithm = AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256,
    ): ExternalKeySignRequest =
        ExternalKeySignRequest.create(
            caller =
                ExternalKeyCaller.create(
                    uid = ExternalKeyCallerUid(SYNTHETIC_CALLER_UID),
                    packageNames = arrayOf(SYNTHETIC_CALLER_PACKAGE),
                ),
            alias = ExternalKeyAlias.AUTHENTICATION,
            providerGeneration = ExternalKeyProviderGeneration(providerGeneration),
            algorithm = algorithm,
            digest = ByteArray(algorithm.digestLength) { DIGEST_FILL },
            cancellation = ExternalKeyOperationCancellation(),
        )

    private fun ExternalKeySignResult.useSuccess() {
        assertTrue(this is ExternalKeySignResult.Success)
        (this as ExternalKeySignResult.Success).signature.close()
    }

    private fun ExternalKeySignResult.failureKind(): ExternalKeySignFailure {
        assertTrue(this is ExternalKeySignResult.Failure)
        return (this as ExternalKeySignResult.Failure).kind
    }

    private companion object {
        const val CURRENT_PROVIDER_GENERATION = 7L
        const val NEXT_PROVIDER_GENERATION = 8L
        const val STALE_PROVIDER_GENERATION = 6L
        const val SYNTHETIC_CALLER_UID = 10_001
        const val SYNTHETIC_CALLER_PACKAGE = "com.example.browser"
        const val SYNTHETIC_PIN1 = "1357"
        const val NO_OPERATIONS = 0
        const val SINGLE_OPERATION = 1
        const val DIGEST_FILL: Byte = 0x31
        const val SIGNATURE_FILL: Byte = 0x35
        val SYNTHETIC_CERTIFICATE = "synthetic-certificate".encodeToByteArray()
        val SYNTHETIC_ISSUER_CERTIFICATE = "synthetic-issuer-certificate".encodeToByteArray()
        val SYNTHETIC_ISSUER_SOURCE =
            AuthenticationIssuerCertificateSource {
                SYNTHETIC_ISSUER_CERTIFICATE.copyOf()
            }
    }
}
