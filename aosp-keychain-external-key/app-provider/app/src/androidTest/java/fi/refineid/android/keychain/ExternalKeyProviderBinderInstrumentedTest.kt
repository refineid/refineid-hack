package fi.refineid.android.keychain

import android.os.Binder
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.android.keychain.external.ExternalKeyProviderResult
import com.android.keychain.external.IExternalKeyProviderService
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationSignature
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExternalKeyProviderBinderInstrumentedTest {
    @Test
    fun trustedCallerReceivesVersionIdentityAndOneMappedSignature() {
        val backend = RecordingBackend()
        val binder = trustedBinder(backend)

        assertEquals(IExternalKeyProviderService.PROTOCOL_VERSION, binder.protocolVersion)
        binder.activeIdentity.use { identity ->
            assertNotNull(identity)
            identity ?: return@use
            assertArrayEquals(SYNTHETIC_LEAF_CERTIFICATE, identity.copyLeafCertificate())
            assertArrayEquals(SYNTHETIC_CA_CERTIFICATES, identity.copyCaCertificates())
            assertEquals(SYNTHETIC_PROVIDER_GENERATION, identity.providerGeneration)
        }

        val digest = syntheticDigest(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256)
        val expectedDigest = digest.copyOf()
        val result =
            binder.sign(
                SYNTHETIC_CALLER_UID,
                arrayOf(SYNTHETIC_CALLER_HELPER_PACKAGE, SYNTHETIC_CALLER_PACKAGE),
                ExternalKeyAlias.AUTHENTICATION.wireValue,
                SYNTHETIC_PROVIDER_GENERATION,
                IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                digest,
                Binder(),
            )

        result.use { ownedResult ->
            assertTrue(ownedResult.isSuccess)
            assertArrayEquals(SYNTHETIC_RSA_SIGNATURE, ownedResult.copySignature())
        }
        assertArrayEquals(expectedDigest, backend.observedDigest)
        assertEquals(
            listOf(SYNTHETIC_CALLER_PACKAGE, SYNTHETIC_CALLER_HELPER_PACKAGE),
            backend.observedCallerPackages,
        )
        assertTrue(digest.all { value -> value == CLEARED_BYTE })
        expectedDigest.fill(CLEARED_BYTE)
    }

    @Test
    fun everyAlgorithmAndFailureHasAnExplicitWireMapping() {
        val cases =
            listOf(
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA256,
                    AuthenticationSigningAlgorithm.RSA_PSS_SHA256,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_ECDSA_P384_SHA256,
                    AuthenticationSigningAlgorithm.ECDSA_P384_SHA256,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_ECDSA_P384_SHA384,
                    AuthenticationSigningAlgorithm.ECDSA_P384_SHA384,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA384,
                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA384,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA384,
                    AuthenticationSigningAlgorithm.RSA_PSS_SHA384,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA512,
                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA512,
                ),
                AlgorithmCase(
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PSS_SHA512,
                    AuthenticationSigningAlgorithm.RSA_PSS_SHA512,
                ),
            )
        cases.forEach { case ->
            val backend = RecordingBackend()
            val binder = trustedBinder(backend)
            val digest = syntheticDigest(case.expectedAlgorithm)
            binder
                .sign(
                    SYNTHETIC_CALLER_UID,
                    arrayOf(SYNTHETIC_CALLER_PACKAGE),
                    ExternalKeyAlias.AUTHENTICATION.wireValue,
                    SYNTHETIC_PROVIDER_GENERATION,
                    case.wireAlgorithm,
                    digest,
                    Binder(),
                ).close()
            assertEquals(case.expectedAlgorithm, backend.observedAlgorithm)
            assertTrue(digest.all { value -> value == CLEARED_BYTE })
        }

        FAILURE_CASES.forEach { case ->
            val backend = RecordingBackend(result = ExternalKeySignResult.Failure(case.failure))
            val binder = trustedBinder(backend)
            val digest = syntheticDigest(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256)
            val result =
                binder.sign(
                    SYNTHETIC_CALLER_UID,
                    arrayOf(SYNTHETIC_CALLER_PACKAGE),
                    ExternalKeyAlias.AUTHENTICATION.wireValue,
                    SYNTHETIC_PROVIDER_GENERATION,
                    IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                    digest,
                    Binder(),
                )
            assertFalse(result.isSuccess)
            assertEquals(case.wireFailure, result.failure)
            result.close()
            assertTrue(digest.all { value -> value == CLEARED_BYTE })
        }
    }

    @Test
    fun malformedOrDeadCallerRequestsFailBeforeBackendUseAndClearDigest() {
        val backend = RecordingBackend()
        val binder = trustedBinder(backend)
        val malformedDigest =
            ByteArray(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256.digestLength - MISSING_BYTE_COUNT) {
                DIGEST_FILL
            }
        val malformed =
            binder.sign(
                SYNTHETIC_CALLER_UID,
                arrayOf(SYNTHETIC_CALLER_PACKAGE),
                UNKNOWN_ALIAS,
                SYNTHETIC_PROVIDER_GENERATION,
                IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                malformedDigest,
                Binder(),
            )
        assertEquals(ExternalKeyProviderResult.FAILURE_INVALID_REQUEST, malformed.failure)
        malformed.close()
        assertEquals(NO_BACKEND_SIGN_CALLS, backend.signCount)
        assertTrue(malformedDigest.all { value -> value == CLEARED_BYTE })

        val cancelledBackend = RecordingBackend()
        val cancelledBinder =
            ExternalKeyProviderBinder(
                backend = cancelledBackend,
                callerVerifier = KeyChainCallerVerifier {},
                livenessFactory =
                    OperationLivenessFactory { _, cancellation ->
                        cancellation.cancel()
                        AutoCloseable {}
                    },
            )
        val cancelledDigest = syntheticDigest(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256)
        val cancelled =
            cancelledBinder.sign(
                SYNTHETIC_CALLER_UID,
                arrayOf(SYNTHETIC_CALLER_PACKAGE),
                ExternalKeyAlias.AUTHENTICATION.wireValue,
                SYNTHETIC_PROVIDER_GENERATION,
                IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                cancelledDigest,
                Binder(),
            )
        assertEquals(ExternalKeyProviderResult.FAILURE_CALLER_INTERRUPTED, cancelled.failure)
        cancelled.close()
        assertEquals(NO_BACKEND_SIGN_CALLS, cancelledBackend.signCount)
        assertTrue(cancelledDigest.all { value -> value == CLEARED_BYTE })
    }

    @Test
    fun everyMethodRejectsAnUntrustedBinderCaller() {
        val verifier =
            KeyChainCallerVerifier {
                throw SecurityException("synthetic untrusted caller")
            }
        val binder =
            ExternalKeyProviderBinder(
                backend = RecordingBackend(),
                callerVerifier = verifier,
                livenessFactory = INERT_LIVENESS_FACTORY,
            )
        val digest = syntheticDigest(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256)

        assertThrows(SecurityException::class.java, binder::getProtocolVersion)
        assertThrows(SecurityException::class.java, binder::getActiveIdentity)
        assertThrows(SecurityException::class.java) {
            binder.sign(
                SYNTHETIC_CALLER_UID,
                arrayOf(SYNTHETIC_CALLER_PACKAGE),
                ExternalKeyAlias.AUTHENTICATION.wireValue,
                SYNTHETIC_PROVIDER_GENERATION,
                IExternalKeyProviderService.SIGNATURE_ALGORITHM_RSA_PKCS1_SHA256,
                digest,
                Binder(),
            )
        }
        assertThrows(SecurityException::class.java) {
            binder.removeIdentity(SYNTHETIC_PROVIDER_GENERATION)
        }
        assertFalse(digest.all { value -> value == CLEARED_BYTE })
        digest.fill(CLEARED_BYTE)
    }

    @Test
    fun removalIsGenerationBound() {
        val backend = RecordingBackend()
        val binder = trustedBinder(backend)

        assertTrue(binder.removeIdentity(SYNTHETIC_PROVIDER_GENERATION))
        assertEquals(SYNTHETIC_PROVIDER_GENERATION, backend.removedGeneration)
    }

    private fun trustedBinder(backend: RecordingBackend): ExternalKeyProviderBinder =
        ExternalKeyProviderBinder(
            backend = backend,
            callerVerifier = KeyChainCallerVerifier {},
            livenessFactory = INERT_LIVENESS_FACTORY,
        )

    private fun syntheticDigest(algorithm: AuthenticationSigningAlgorithm): ByteArray =
        ByteArray(algorithm.digestLength) { DIGEST_FILL }

    private class RecordingBackend(
        private val result: ExternalKeySignResult? = null,
    ) : ExternalKeyProviderBackend {
        var signCount = NO_BACKEND_SIGN_CALLS
        var observedDigest = byteArrayOf()
        var observedCallerPackages = emptyList<String>()
        var observedAlgorithm: AuthenticationSigningAlgorithm? = null
        var removedGeneration: Long? = null

        override fun copyActiveIdentity(): ExternalKeyIdentitySnapshot =
            ExternalKeyIdentitySnapshot.create(
                providerGeneration = ExternalKeyProviderGeneration(SYNTHETIC_PROVIDER_GENERATION),
                leafCertificate = SYNTHETIC_LEAF_CERTIFICATE,
                caCertificates = SYNTHETIC_CA_CERTIFICATES,
            )

        override fun sign(request: ExternalKeySignRequest): ExternalKeySignResult {
            signCount += SINGLE_BACKEND_SIGN_CALL
            observedDigest = request.copyDigest()
            observedCallerPackages = request.caller.copyPackageNames()
            observedAlgorithm = request.algorithm
            return result
                ?: ExternalKeySignResult.Success(
                    signature =
                        NativeAuthenticationSignature(
                            algorithm = request.algorithm,
                            ownedBytes =
                                when (request.algorithm) {
                                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256,
                                    AuthenticationSigningAlgorithm.RSA_PSS_SHA256,
                                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA384,
                                    AuthenticationSigningAlgorithm.RSA_PSS_SHA384,
                                    AuthenticationSigningAlgorithm.RSA_PKCS1_SHA512,
                                    AuthenticationSigningAlgorithm.RSA_PSS_SHA512,
                                    -> SYNTHETIC_RSA_SIGNATURE.copyOf()

                                    AuthenticationSigningAlgorithm.ECDSA_P384_SHA256,
                                    AuthenticationSigningAlgorithm.ECDSA_P384_SHA384,
                                    -> ByteArray(request.algorithm.signatureLength) { SIGNATURE_FILL }
                                },
                        ),
                    isReplay = false,
                )
        }

        override fun removeIdentity(providerGeneration: ExternalKeyProviderGeneration): Boolean {
            removedGeneration = providerGeneration.value
            return providerGeneration.value == SYNTHETIC_PROVIDER_GENERATION
        }
    }

    private data class AlgorithmCase(
        val wireAlgorithm: Int,
        val expectedAlgorithm: AuthenticationSigningAlgorithm,
    )

    private data class FailureCase(
        val failure: ExternalKeySignFailure,
        @param:ExternalKeyProviderResult.Failure val wireFailure: Int,
    )

    private companion object {
        const val SYNTHETIC_CALLER_UID = 10_001
        const val SYNTHETIC_CALLER_PACKAGE = "com.example.browser"
        const val SYNTHETIC_CALLER_HELPER_PACKAGE = "com.example.browser.helper"
        const val UNKNOWN_ALIAS = "unknown"
        const val SYNTHETIC_PROVIDER_GENERATION = 7L
        const val MISSING_BYTE_COUNT = 1
        const val NO_BACKEND_SIGN_CALLS = 0
        const val SINGLE_BACKEND_SIGN_CALL = 1
        const val CLEARED_BYTE: Byte = 0
        const val DIGEST_FILL: Byte = 0x31
        const val SIGNATURE_FILL: Byte = 0x35
        val SYNTHETIC_LEAF_CERTIFICATE = "synthetic-leaf".encodeToByteArray()
        val SYNTHETIC_CA_CERTIFICATES = "synthetic-ca-certificates".encodeToByteArray()
        val SYNTHETIC_RSA_SIGNATURE =
            ByteArray(AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256.signatureLength) {
                SIGNATURE_FILL
            }
        val INERT_LIVENESS_FACTORY =
            OperationLivenessFactory { _, _ ->
                AutoCloseable {}
            }
        val FAILURE_CASES =
            listOf(
                FailureCase(
                    ExternalKeySignFailure.INVALID_REQUEST,
                    ExternalKeyProviderResult.FAILURE_INVALID_REQUEST,
                ),
                FailureCase(
                    ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
                    ExternalKeyProviderResult.FAILURE_PROVIDER_UNAVAILABLE,
                ),
                FailureCase(
                    ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED,
                    ExternalKeyProviderResult.FAILURE_PROVIDER_GENERATION_CHANGED,
                ),
                FailureCase(
                    ExternalKeySignFailure.USER_CANCELLED,
                    ExternalKeyProviderResult.FAILURE_USER_CANCELLED,
                ),
                FailureCase(
                    ExternalKeySignFailure.USER_TIMED_OUT,
                    ExternalKeyProviderResult.FAILURE_USER_TIMED_OUT,
                ),
                FailureCase(
                    ExternalKeySignFailure.CALLER_INTERRUPTED,
                    ExternalKeyProviderResult.FAILURE_CALLER_INTERRUPTED,
                ),
                FailureCase(
                    ExternalKeySignFailure.SIGNING_FAILED,
                    ExternalKeyProviderResult.FAILURE_SIGNING_FAILED,
                ),
                FailureCase(
                    ExternalKeySignFailure.INTERNAL_ERROR,
                    ExternalKeyProviderResult.FAILURE_INTERNAL_ERROR,
                ),
            )
    }
}
