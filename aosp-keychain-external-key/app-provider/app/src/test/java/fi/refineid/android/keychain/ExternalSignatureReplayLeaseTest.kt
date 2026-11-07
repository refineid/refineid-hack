package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSigningAlgorithm
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ExternalSignatureReplayLeaseTest {
    @Test
    fun acceptsOnlyTheClosedAliasAndExactDigestShape() {
        assertEquals(
            ExternalKeyAlias.AUTHENTICATION,
            ExternalKeyAlias.fromWireValue(ExternalKeyAlias.AUTHENTICATION.wireValue),
        )
        assertNull(ExternalKeyAlias.fromWireValue(UNKNOWN_ALIAS))
        assertThrows(IllegalArgumentException::class.java) {
            ExternalKeyCallerUid(INVALID_CALLER_UID)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ExternalKeyProviderGeneration(INVALID_PROVIDER_GENERATION)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ExternalKeyCaller.create(
                uid = ExternalKeyCallerUid(SYNTHETIC_CALLER_UID),
                packageNames = emptyArray(),
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ExternalKeyCaller.create(
                uid = ExternalKeyCallerUid(SYNTHETIC_CALLER_UID),
                packageNames = arrayOf(SYNTHETIC_CALLER_PACKAGE, SYNTHETIC_CALLER_PACKAGE),
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            request(digestLengthAdjustment = MISSING_BYTE_ADJUSTMENT)
        }
    }

    @Test
    fun cancellationIsStickyAndInvokesAtMostOneRegisteredListener() {
        val cancellation = ExternalKeyOperationCancellation()
        var callbackCount = NO_CALLBACKS
        val registration =
            cancellation.register {
                callbackCount += SINGLE_CALLBACK
            }

        cancellation.cancel()
        cancellation.cancel()
        registration.close()

        assertTrue(cancellation.isCancelled)
        assertEquals(SINGLE_CALLBACK, callbackCount)

        var lateCallbackCount = NO_CALLBACKS
        cancellation
            .register {
                lateCallbackCount += SINGLE_CALLBACK
            }.close()
        assertEquals(SINGLE_CALLBACK, lateCallbackCount)
    }

    @Test
    fun replaysAnExactResultOnceAndTransfersItsOwnership() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val request = request()
        val ownedSignature = syntheticSignature(request.algorithm)
        lease.retain(request, ownedSignature)

        val replay = requireNotNull(lease.take(request))

        assertArrayEquals(
            ByteArray(request.algorithm.signatureLength) { SIGNATURE_FILL },
            replay.copyBytes(),
        )
        assertNull(lease.take(request))
        replay.close()
        assertTrue(ownedSignature.all { value -> value == CLEARED_BYTE })
        request.close()
        lease.close()
    }

    @Test
    fun callerGenerationAlgorithmAndDigestMustAllMatch() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val expected = request()
        val ownedSignature = syntheticSignature(expected.algorithm)
        lease.retain(expected, ownedSignature)

        request(callerUid = ALTERNATE_CALLER_UID).use { mismatch ->
            assertNull(lease.take(mismatch))
        }
        request(providerGeneration = ALTERNATE_PROVIDER_GENERATION).use { mismatch ->
            assertNull(lease.take(mismatch))
        }
        request(algorithm = ALTERNATE_ALGORITHM).use { mismatch ->
            assertNull(lease.take(mismatch))
        }
        request(digestFill = ALTERNATE_DIGEST_FILL).use { mismatch ->
            assertNull(lease.take(mismatch))
        }

        requireNotNull(lease.take(expected)).close()
        assertTrue(ownedSignature.all { value -> value == CLEARED_BYTE })
        expected.close()
        lease.close()
    }

    @Test
    fun replacementClearsThePreviousResult() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val firstRequest = request()
        val firstSignature = syntheticSignature(firstRequest.algorithm)
        lease.retain(firstRequest, firstSignature)
        val secondRequest = request(digestFill = ALTERNATE_DIGEST_FILL)
        val secondSignature = syntheticSignature(secondRequest.algorithm)

        lease.retain(secondRequest, secondSignature)

        assertTrue(firstSignature.all { value -> value == CLEARED_BYTE })
        requireNotNull(lease.take(secondRequest)).close()
        assertTrue(secondSignature.all { value -> value == CLEARED_BYTE })
        firstRequest.close()
        secondRequest.close()
        lease.close()
    }

    @Test
    fun expiryAndExplicitInvalidationClearPendingResults() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val request = request()
        val expiredSignature = syntheticSignature(request.algorithm)
        lease.retain(request, expiredSignature)

        scheduler.fire(taskIndex = FIRST_TASK_INDEX)

        assertTrue(expiredSignature.all { value -> value == CLEARED_BYTE })
        assertNull(lease.take(request))

        val invalidatedSignature = syntheticSignature(request.algorithm)
        lease.retain(request, invalidatedSignature)
        lease.invalidate()

        assertTrue(invalidatedSignature.all { value -> value == CLEARED_BYTE })
        assertNull(lease.take(request))
        request.close()
        lease.close()
    }

    @Test
    fun aStaleExpiryCannotClearItsReplacement() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val firstRequest = request()
        lease.retain(firstRequest, syntheticSignature(firstRequest.algorithm))
        val secondRequest = request(digestFill = ALTERNATE_DIGEST_FILL)
        val secondSignature = syntheticSignature(secondRequest.algorithm)
        lease.retain(secondRequest, secondSignature)

        scheduler.fire(
            taskIndex = FIRST_TASK_INDEX,
            includeCancelled = true,
        )

        requireNotNull(lease.take(secondRequest)).close()
        assertTrue(secondSignature.all { value -> value == CLEARED_BYTE })
        firstRequest.close()
        secondRequest.close()
        lease.close()
    }

    @Test
    fun invalidInputAndSchedulerFailureClearTransferredBytes() {
        val request = request()
        val lease = ExternalSignatureReplayLease(ManualReplayLeaseScheduler())
        val malformedSignature =
            ByteArray(request.algorithm.signatureLength - SINGLE_MISSING_BYTE_COUNT) {
                SIGNATURE_FILL
            }

        assertThrows(IllegalArgumentException::class.java) {
            lease.retain(request, malformedSignature)
        }
        assertTrue(malformedSignature.all { value -> value == CLEARED_BYTE })

        val schedulerFailureSignature = syntheticSignature(request.algorithm)
        val failingLease =
            ExternalSignatureReplayLease(
                ReplayLeaseScheduler { _, _ ->
                    throw IllegalStateException("synthetic scheduler failure")
                },
            )
        assertThrows(IllegalStateException::class.java) {
            failingLease.retain(request, schedulerFailureSignature)
        }
        assertTrue(schedulerFailureSignature.all { value -> value == CLEARED_BYTE })
        request.close()
        lease.close()
        failingLease.close()
    }

    @Test
    fun closingClearsTheResultAndRejectsFutureOwnership() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val request = request()
        val retainedSignature = syntheticSignature(request.algorithm)
        lease.retain(request, retainedSignature)

        lease.close()

        assertTrue(retainedSignature.all { value -> value == CLEARED_BYTE })
        val rejectedSignature = syntheticSignature(request.algorithm)
        assertThrows(IllegalStateException::class.java) {
            lease.retain(request, rejectedSignature)
        }
        assertTrue(rejectedSignature.all { value -> value == CLEARED_BYTE })
        request.close()
    }

    @Test
    fun cancellationFailureCannotPreventClearing() {
        val lease =
            ExternalSignatureReplayLease(
                ReplayLeaseScheduler { _, _ ->
                    ReplayLeaseCancellation {
                        throw IllegalStateException("synthetic cancellation failure")
                    }
                },
            )
        val request = request()
        val retainedSignature = syntheticSignature(request.algorithm)
        lease.retain(request, retainedSignature)

        lease.invalidate()

        assertTrue(retainedSignature.all { value -> value == CLEARED_BYTE })
        assertNull(lease.take(request))
        request.close()
        lease.close()
    }

    @Test
    fun stringFormsNeverContainCallerAliasOrDigest() {
        val scheduler = ManualReplayLeaseScheduler()
        val lease = ExternalSignatureReplayLease(scheduler)
        val request = request()
        val digestCopy = request.copyDigest()
        try {
            val digestText = digestCopy.joinToString(separator = "")
            assertFalse(request.toString().contains(SYNTHETIC_CALLER_UID.toString()))
            assertFalse(request.toString().contains(SYNTHETIC_CALLER_PACKAGE))
            assertFalse(request.toString().contains(digestText))
            assertFalse(lease.toString().contains(ExternalKeyAlias.AUTHENTICATION.wireValue))
        } finally {
            digestCopy.fill(CLEARED_BYTE)
        }
        request.close()
        lease.close()
    }

    private fun request(
        callerUid: Int = SYNTHETIC_CALLER_UID,
        providerGeneration: Long = SYNTHETIC_PROVIDER_GENERATION,
        algorithm: AuthenticationSigningAlgorithm = SYNTHETIC_ALGORITHM,
        digestFill: Byte = DIGEST_FILL,
        digestLengthAdjustment: Int = NO_LENGTH_ADJUSTMENT,
    ): ExternalKeySignRequest =
        ExternalKeySignRequest.create(
            caller =
                ExternalKeyCaller.create(
                    uid = ExternalKeyCallerUid(callerUid),
                    packageNames = arrayOf(SYNTHETIC_CALLER_PACKAGE),
                ),
            alias = ExternalKeyAlias.AUTHENTICATION,
            providerGeneration = ExternalKeyProviderGeneration(providerGeneration),
            algorithm = algorithm,
            digest =
                ByteArray(algorithm.digestLength + digestLengthAdjustment) {
                    digestFill
                },
            cancellation = ExternalKeyOperationCancellation(),
        )

    private fun syntheticSignature(algorithm: AuthenticationSigningAlgorithm): ByteArray =
        ByteArray(algorithm.signatureLength) { SIGNATURE_FILL }

    private class ManualReplayLeaseScheduler : ReplayLeaseScheduler {
        private val tasks = mutableListOf<Task>()

        override fun schedule(
            delayMilliseconds: Long,
            action: () -> Unit,
        ): ReplayLeaseCancellation {
            require(delayMilliseconds >= MINIMUM_SCHEDULE_DELAY_MILLISECONDS)
            val task = Task(action)
            tasks += task
            return ReplayLeaseCancellation {
                task.isCancelled = true
            }
        }

        fun fire(
            taskIndex: Int,
            includeCancelled: Boolean = false,
        ) {
            val task = tasks[taskIndex]
            if (includeCancelled || !task.isCancelled) {
                task.action()
            }
        }

        private class Task(
            val action: () -> Unit,
            var isCancelled: Boolean = false,
        )
    }

    private companion object {
        const val UNKNOWN_ALIAS = "unknown"
        const val INVALID_CALLER_UID = -1
        const val SYNTHETIC_CALLER_UID = 10_001
        const val ALTERNATE_CALLER_UID = 10_002
        const val SYNTHETIC_CALLER_PACKAGE = "com.example.browser"
        const val INVALID_PROVIDER_GENERATION = 0L
        const val SYNTHETIC_PROVIDER_GENERATION = 7L
        const val ALTERNATE_PROVIDER_GENERATION = 8L
        const val MINIMUM_SCHEDULE_DELAY_MILLISECONDS = 1L
        const val NO_LENGTH_ADJUSTMENT = 0
        const val MISSING_BYTE_ADJUSTMENT = -1
        const val SINGLE_MISSING_BYTE_COUNT = 1
        const val NO_CALLBACKS = 0
        const val SINGLE_CALLBACK = 1
        const val FIRST_TASK_INDEX = 0
        const val CLEARED_BYTE: Byte = 0
        const val DIGEST_FILL: Byte = 0x31
        const val ALTERNATE_DIGEST_FILL: Byte = 0x32
        const val SIGNATURE_FILL: Byte = 0x5A
        val SYNTHETIC_ALGORITHM = AuthenticationSigningAlgorithm.RSA_PKCS1_SHA256
        val ALTERNATE_ALGORITHM = AuthenticationSigningAlgorithm.ECDSA_P384_SHA384
    }
}
