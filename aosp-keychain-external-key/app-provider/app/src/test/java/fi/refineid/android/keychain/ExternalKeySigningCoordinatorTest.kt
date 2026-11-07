package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSignFailure
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationSignature
import fi.refineid.android.core.Pin1Submission
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ExternalKeySigningCoordinatorTest {
    @Test
    fun firstCallUsesFreshConsentSecondReplaysAndThirdUsesFreshConsent() {
        val fixture = Fixture()
        val request = request()

        fixture.coordinator
            .sign(request)
            .requireSuccess()
            .useSignature()
        val replay = fixture.coordinator.sign(request).requireSuccess()
        assertTrue(replay.isReplay)
        replay.useSignature()
        fixture.coordinator
            .sign(request)
            .requireSuccess()
            .useSignature()

        assertEquals(EXPECTED_FRESH_OPERATION_COUNT, fixture.authorizer.authorizationCount)
        assertEquals(EXPECTED_FRESH_OPERATION_COUNT, fixture.cardService.signCount)
        assertArrayEquals(fixture.expectedDigest, fixture.cardService.lastDigest)
        request.close()
        fixture.close()
    }

    @Test
    fun callerAndDigestMismatchesNeverReceiveAnotherCallersReplay() {
        val fixture = Fixture()
        val firstRequest = request()
        fixture.coordinator
            .sign(firstRequest)
            .requireSuccess()
            .useSignature()
        val otherCaller = request(callerUid = ALTERNATE_CALLER_UID)
        fixture.coordinator
            .sign(otherCaller)
            .requireSuccess()
            .useSignature()
        val otherDigest = request(digestFill = ALTERNATE_DIGEST_FILL)
        fixture.coordinator
            .sign(otherDigest)
            .requireSuccess()
            .useSignature()

        assertEquals(EXPECTED_MISMATCH_OPERATION_COUNT, fixture.authorizer.authorizationCount)
        assertEquals(EXPECTED_MISMATCH_OPERATION_COUNT, fixture.cardService.signCount)
        firstRequest.close()
        otherCaller.close()
        otherDigest.close()
        fixture.close()
    }

    @Test
    fun staleAndUnavailableGenerationsFailBeforeConsent() {
        val fixture = Fixture()
        val stale = request(providerGeneration = ALTERNATE_PROVIDER_GENERATION)

        assertEquals(
            ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED,
            fixture.coordinator.sign(stale).requireFailure(),
        )
        fixture.coordinator.updateProviderGeneration(null)
        val current = request()
        assertEquals(
            ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            fixture.coordinator.sign(current).requireFailure(),
        )
        assertEquals(NO_OPERATIONS, fixture.authorizer.authorizationCount)
        assertEquals(NO_OPERATIONS, fixture.cardService.signCount)
        stale.close()
        current.close()
        fixture.close()
    }

    @Test
    fun aGenerationChangeDuringConsentClosesThePinWithoutCardUse() {
        val scheduler = ManualScheduler()
        val cardService = RecordingCardService()
        lateinit var coordinator: ExternalKeySigningCoordinator
        lateinit var submittedPin: Pin1Submission
        val authorizer =
            RecordingPinAuthorizer {
                coordinator.updateProviderGeneration(ALTERNATE_GENERATION)
                syntheticPin().also { pin1 -> submittedPin = pin1 }.let(
                    ExternalKeyPinAuthorization::Approved,
                )
            }
        coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardService,
                pinAuthorizer = authorizer,
                replayLease = ExternalSignatureReplayLease(scheduler),
                initialProviderGeneration = CURRENT_GENERATION,
            )

        val request = request()
        assertEquals(
            ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED,
            coordinator.sign(request).requireFailure(),
        )
        assertEquals(NO_OPERATIONS, cardService.signCount)
        assertThrows(IllegalStateException::class.java) {
            submittedPin.consume { _ -> }
        }
        request.close()
        coordinator.close()
    }

    @Test
    fun callerCancellationDuringConsentClosesThePinWithoutCardUse() {
        val scheduler = ManualScheduler()
        val cardService = RecordingCardService()
        val cancellation = ExternalKeyOperationCancellation()
        lateinit var submittedPin: Pin1Submission
        val authorizer =
            RecordingPinAuthorizer {
                cancellation.cancel()
                syntheticPin().also { pin1 -> submittedPin = pin1 }.let(
                    ExternalKeyPinAuthorization::Approved,
                )
            }
        val coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardService,
                pinAuthorizer = authorizer,
                replayLease = ExternalSignatureReplayLease(scheduler),
                initialProviderGeneration = CURRENT_GENERATION,
            )
        val request = request(cancellation = cancellation)

        assertEquals(
            ExternalKeySignFailure.CALLER_INTERRUPTED,
            coordinator.sign(request).requireFailure(),
        )
        assertEquals(NO_OPERATIONS, cardService.signCount)
        assertThrows(IllegalStateException::class.java) {
            submittedPin.consume { _ -> }
        }
        request.close()
        coordinator.close()
    }

    @Test
    fun callerCancellationDuringCardUseDiscardsTheCompletedSignature() {
        val cancellation = ExternalKeyOperationCancellation()
        val cardService = RecordingCardService(beforeResult = cancellation::cancel)
        val coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardService,
                pinAuthorizer =
                    RecordingPinAuthorizer {
                        ExternalKeyPinAuthorization.Approved(syntheticPin())
                    },
                replayLease = ExternalSignatureReplayLease(ManualScheduler()),
                initialProviderGeneration = CURRENT_GENERATION,
            )
        val request = request(cancellation = cancellation)

        assertEquals(
            ExternalKeySignFailure.CALLER_INTERRUPTED,
            coordinator.sign(request).requireFailure(),
        )
        assertEquals(SINGLE_OPERATION, cardService.signCount)
        assertTrue(
            requireNotNull(cardService.lastIssuedSignatureBytes).all { value ->
                value == CLEARED_BYTE
            },
        )
        request.close()
        coordinator.close()
    }

    @Test
    fun aGenerationChangeDuringCardUseDiscardsTheCompletedSignature() {
        val scheduler = ManualScheduler()
        lateinit var coordinator: ExternalKeySigningCoordinator
        val cardService =
            RecordingCardService(
                beforeResult = {
                    coordinator.updateProviderGeneration(ALTERNATE_GENERATION)
                },
            )
        val authorizer =
            RecordingPinAuthorizer {
                ExternalKeyPinAuthorization.Approved(syntheticPin())
            }
        coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardService,
                pinAuthorizer = authorizer,
                replayLease = ExternalSignatureReplayLease(scheduler),
                initialProviderGeneration = CURRENT_GENERATION,
            )
        val request = request()

        assertEquals(
            ExternalKeySignFailure.PROVIDER_GENERATION_CHANGED,
            coordinator.sign(request).requireFailure(),
        )
        assertEquals(SINGLE_OPERATION, cardService.signCount)
        assertTrue(
            requireNotNull(cardService.lastIssuedSignatureBytes).all { value ->
                value == CLEARED_BYTE
            },
        )
        request.close()
        coordinator.close()
    }

    @Test
    fun cancellationTimeoutAndUnavailableConsentNeverReachTheCard() {
        val outcomes =
            listOf(
                ExternalKeyPinAuthorization.Cancelled to ExternalKeySignFailure.USER_CANCELLED,
                ExternalKeyPinAuthorization.TimedOut to ExternalKeySignFailure.USER_TIMED_OUT,
                ExternalKeyPinAuthorization.Unavailable to ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            )
        for ((authorization, expectedFailure) in outcomes) {
            val fixture = Fixture(authorization = authorization)
            val request = request()
            assertEquals(expectedFailure, fixture.coordinator.sign(request).requireFailure())
            assertEquals(NO_OPERATIONS, fixture.cardService.signCount)
            request.close()
            fixture.close()
        }
    }

    @Test
    fun cancellationSignalDoesNotInventAWorkerThreadInterrupt() {
        assertFalse(Thread.currentThread().isInterrupted)
        val fixture = Fixture(authorization = ExternalKeyPinAuthorization.Interrupted)
        val request = request()

        assertEquals(
            ExternalKeySignFailure.CALLER_INTERRUPTED,
            fixture.coordinator.sign(request).requireFailure(),
        )
        assertFalse(Thread.currentThread().isInterrupted)

        request.close()
        fixture.close()
    }

    @Test
    fun cardFailuresAreCoarseAndEveryPinIsConsumed() {
        val fixture =
            Fixture(
                cardResult =
                    AuthenticationSignResult.Failure(
                        AuthenticationSignFailure.WRONG_PIN,
                    ),
            )
        val request = request()

        assertEquals(
            ExternalKeySignFailure.SIGNING_FAILED,
            fixture.coordinator.sign(request).requireFailure(),
        )
        assertTrue(fixture.cardService.lastPinBytes.all { value -> value == CLEARED_BYTE })
        request.close()
        fixture.close()
    }

    @Test
    fun malformedCardResultAndSchedulerFailureAreClosedAndFailInternal() {
        val malformedBytes = ByteArray(MALFORMED_SIGNATURE_LENGTH) { SIGNATURE_FILL }
        val malformedResult =
            AuthenticationSignResult.Success(
                NativeAuthenticationSignature(SYNTHETIC_ALGORITHM, malformedBytes),
            )
        val malformedFixture = Fixture(cardResult = malformedResult)
        val request = request()

        assertEquals(
            ExternalKeySignFailure.INTERNAL_ERROR,
            malformedFixture.coordinator.sign(request).requireFailure(),
        )
        assertTrue(malformedBytes.all { value -> value == CLEARED_BYTE })
        malformedFixture.close()

        val schedulerFixture =
            Fixture(
                scheduler =
                    ReplayLeaseScheduler { _, _ ->
                        throw IllegalStateException("synthetic scheduler failure")
                    },
            )
        assertEquals(
            ExternalKeySignFailure.INTERNAL_ERROR,
            schedulerFixture.coordinator.sign(request).requireFailure(),
        )
        val issuedSignature = requireNotNull(schedulerFixture.cardService.lastIssuedSignatureBytes)
        assertTrue(issuedSignature.all { value -> value == CLEARED_BYTE })
        request.close()
        schedulerFixture.close()
    }

    @Test
    fun closingCancelsConsentClearsReplayAndRejectsLaterCalls() {
        val fixture = Fixture()
        val request = request()
        fixture.coordinator
            .sign(request)
            .requireSuccess()
            .useSignature()

        fixture.coordinator.close()

        assertTrue(fixture.authorizer.wasCancelled)
        assertEquals(
            ExternalKeySignFailure.PROVIDER_UNAVAILABLE,
            fixture.coordinator.sign(request).requireFailure(),
        )
        request.close()
    }

    private class Fixture(
        authorization: ExternalKeyPinAuthorization? = null,
        cardResult: AuthenticationSignResult? = null,
        scheduler: ReplayLeaseScheduler = ManualScheduler(),
    ) : AutoCloseable {
        val authorizer =
            RecordingPinAuthorizer {
                authorization ?: ExternalKeyPinAuthorization.Approved(syntheticPin())
            }
        val cardService = RecordingCardService(cardResult)
        val coordinator =
            ExternalKeySigningCoordinator(
                cardSigner = cardService,
                pinAuthorizer = authorizer,
                replayLease = ExternalSignatureReplayLease(scheduler),
                initialProviderGeneration = CURRENT_GENERATION,
            )
        val expectedDigest = ByteArray(SYNTHETIC_ALGORITHM.digestLength) { DIGEST_FILL }

        override fun close() {
            coordinator.close()
        }
    }

    private class RecordingPinAuthorizer(
        private val outcome: () -> ExternalKeyPinAuthorization,
    ) : ExternalKeyPinAuthorizer {
        var authorizationCount = 0
        var wasCancelled = false

        override fun authorize(request: ExternalKeySignRequest): ExternalKeyPinAuthorization {
            authorizationCount += SINGLE_OPERATION
            return outcome()
        }

        override fun cancelPending() {
            wasCancelled = true
        }
    }

    private class RecordingCardService(
        private val configuredResult: AuthenticationSignResult? = null,
        private val beforeResult: () -> Unit = {},
    ) : ExternalKeyCardSigner {
        var signCount = 0
        var lastDigest = byteArrayOf()
        var lastPinBytes = byteArrayOf()
        var lastIssuedSignatureBytes: ByteArray? = null

        override fun signAuthenticationDigest(
            providerGeneration: ExternalKeyProviderGeneration,
            algorithm: AuthenticationSigningAlgorithm,
            pin1: Pin1Submission,
            digest: ByteArray,
        ): AuthenticationSignResult {
            assertEquals(SYNTHETIC_PROVIDER_GENERATION, providerGeneration.value)
            signCount += SINGLE_OPERATION
            lastDigest = digest.copyOf()
            pin1.consume { bytes ->
                lastPinBytes = bytes
            }
            beforeResult()
            return configuredResult
                ?: AuthenticationSignResult.Success(
                    NativeAuthenticationSignature(
                        algorithm = algorithm,
                        ownedBytes =
                            ByteArray(algorithm.signatureLength) {
                                SIGNATURE_FILL
                            }.also { bytes ->
                                lastIssuedSignatureBytes = bytes
                            },
                    ),
                )
        }
    }

    private class ManualScheduler : ReplayLeaseScheduler {
        override fun schedule(
            delayMilliseconds: Long,
            action: () -> Unit,
        ): ReplayLeaseCancellation {
            require(delayMilliseconds >= MINIMUM_SCHEDULE_DELAY_MILLISECONDS)
            return ReplayLeaseCancellation {}
        }
    }

    private fun request(
        callerUid: Int = SYNTHETIC_CALLER_UID,
        providerGeneration: Long = SYNTHETIC_PROVIDER_GENERATION,
        digestFill: Byte = DIGEST_FILL,
        cancellation: ExternalKeyOperationCancellation = ExternalKeyOperationCancellation(),
    ): ExternalKeySignRequest =
        ExternalKeySignRequest.create(
            caller =
                ExternalKeyCaller.create(
                    uid = ExternalKeyCallerUid(callerUid),
                    packageNames = arrayOf(SYNTHETIC_CALLER_PACKAGE),
                ),
            alias = ExternalKeyAlias.AUTHENTICATION,
            providerGeneration = ExternalKeyProviderGeneration(providerGeneration),
            algorithm = SYNTHETIC_ALGORITHM,
            digest = ByteArray(SYNTHETIC_ALGORITHM.digestLength) { digestFill },
            cancellation = cancellation,
        )

    private fun ExternalKeySignResult.requireSuccess(): ExternalKeySignResult.Success {
        assertTrue(this is ExternalKeySignResult.Success)
        return this as ExternalKeySignResult.Success
    }

    private fun ExternalKeySignResult.requireFailure(): ExternalKeySignFailure {
        assertTrue(this is ExternalKeySignResult.Failure)
        return (this as ExternalKeySignResult.Failure).kind
    }

    private fun ExternalKeySignResult.Success.useSignature() {
        signature.close()
    }

    private companion object {
        const val SYNTHETIC_CALLER_UID = 10_001
        const val ALTERNATE_CALLER_UID = 10_002
        const val SYNTHETIC_CALLER_PACKAGE = "com.example.browser"
        const val SYNTHETIC_PROVIDER_GENERATION = 7L
        const val ALTERNATE_PROVIDER_GENERATION = 8L
        const val MINIMUM_SCHEDULE_DELAY_MILLISECONDS = 1L
        const val SYNTHETIC_PIN_LENGTH = 4
        const val SINGLE_OPERATION = 1
        const val NO_OPERATIONS = 0
        const val EXPECTED_FRESH_OPERATION_COUNT = 2
        const val EXPECTED_MISMATCH_OPERATION_COUNT = 3
        const val MALFORMED_SIGNATURE_LENGTH = 1
        const val CLEARED_BYTE: Byte = 0
        const val PIN_FILL = '0'
        const val DIGEST_FILL: Byte = 0x31
        const val ALTERNATE_DIGEST_FILL: Byte = 0x32
        const val SIGNATURE_FILL: Byte = 0x5A
        val SYNTHETIC_ALGORITHM = AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256
        val CURRENT_GENERATION =
            ExternalKeyProviderGeneration(SYNTHETIC_PROVIDER_GENERATION)
        val ALTERNATE_GENERATION =
            ExternalKeyProviderGeneration(ALTERNATE_PROVIDER_GENERATION)

        fun syntheticPin(): Pin1Submission = Pin1Submission.from(PIN_FILL.toString().repeat(SYNTHETIC_PIN_LENGTH))
    }
}
