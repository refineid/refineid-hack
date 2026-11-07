package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationSignature

internal fun interface ReplayLeaseCancellation {
    fun cancel()
}

internal fun interface ReplayLeaseScheduler {
    fun schedule(
        delayMilliseconds: Long,
        action: () -> Unit,
    ): ReplayLeaseCancellation
}

/**
 * Holds one additional retrieval of a locally verified external-key result.
 *
 * [retain] takes ownership of its byte array even when validation or scheduling
 * fails. A successful [take] transfers that ownership to the returned signature.
 */
internal class ExternalSignatureReplayLease(
    private val scheduler: ReplayLeaseScheduler,
    private val timeoutMilliseconds: Long = DEFAULT_TIMEOUT_MILLISECONDS,
) : AutoCloseable {
    private val lock = Any()
    private var isClosed = false
    private var nextToken = INITIAL_ENTRY_TOKEN
    private var entry: Entry? = null

    init {
        require(timeoutMilliseconds >= MINIMUM_TIMEOUT_MILLISECONDS) {
            "replay lease timeout must be positive"
        }
    }

    fun retain(
        request: ExternalKeySignRequest,
        ownedSignatureBytes: ByteArray,
    ) {
        if (ownedSignatureBytes.size != request.algorithm.signatureLength) {
            ownedSignatureBytes.fill(CLEARED_BYTE)
            throw IllegalArgumentException("replay signature length does not match the algorithm")
        }
        var digestWasCopied = false
        val digest =
            try {
                request.copyDigest().also {
                    digestWasCopied = true
                }
            } finally {
                if (!digestWasCopied) {
                    ownedSignatureBytes.fill(CLEARED_BYTE)
                }
            }
        val newEntry: Entry
        val replacedEntry: Entry?
        synchronized(lock) {
            if (isClosed) {
                digest.fill(CLEARED_BYTE)
                ownedSignatureBytes.fill(CLEARED_BYTE)
                throw IllegalStateException("replay lease is closed")
            }
            val token = nextToken
            nextToken += TOKEN_INCREMENT
            newEntry =
                Entry(
                    token = token,
                    callerUid = request.callerUid,
                    alias = request.alias,
                    providerGeneration = request.providerGeneration,
                    algorithm = request.algorithm,
                    digest = digest,
                    ownedSignatureBytes = ownedSignatureBytes,
                )
            replacedEntry = entry
            entry = newEntry
        }
        replacedEntry?.clear()

        var schedulingSucceeded = false
        val cancellation =
            try {
                scheduler
                    .schedule(timeoutMilliseconds) {
                        expire(newEntry.token)
                    }.also {
                        schedulingSucceeded = true
                    }
            } finally {
                if (!schedulingSucceeded) {
                    expire(newEntry.token)
                }
            }
        val retained =
            synchronized(lock) {
                if (entry === newEntry && !isClosed) {
                    newEntry.expiryCancellation = cancellation
                    true
                } else {
                    false
                }
            }
        if (!retained) {
            cancellation.cancelForCleanup()
        }
    }

    fun take(request: ExternalKeySignRequest): NativeAuthenticationSignature? {
        val matchedEntry =
            synchronized(lock) {
                val candidate = entry
                if (isClosed || candidate == null || !candidate.matches(request)) {
                    null
                } else {
                    entry = null
                    candidate
                }
            } ?: return null
        matchedEntry.expiryCancellation?.cancelForCleanup()
        matchedEntry.digest.fill(CLEARED_BYTE)
        return NativeAuthenticationSignature(
            algorithm = matchedEntry.algorithm,
            ownedBytes = matchedEntry.takeSignatureBytes(),
        )
    }

    /** Clears a pending replay while leaving the lease reusable for a new generation. */
    fun invalidate() {
        detachEntry()?.clear()
    }

    override fun close() {
        val detachedEntry =
            synchronized(lock) {
                if (isClosed) {
                    return
                }
                isClosed = true
                entry.also { entry = null }
            }
        detachedEntry?.clear()
    }

    override fun toString(): String =
        synchronized(lock) {
            "ExternalSignatureReplayLease(" +
                "occupied=" + (entry != null) +
                ", timeoutMilliseconds=" + timeoutMilliseconds +
                ", closed=" + isClosed +
                ")"
        }

    private fun expire(token: Long) {
        val expiredEntry =
            synchronized(lock) {
                entry?.takeIf { candidate -> candidate.token == token }?.also {
                    entry = null
                }
            }
        expiredEntry?.clear()
    }

    private fun detachEntry(): Entry? =
        synchronized(lock) {
            entry.also { entry = null }
        }

    private class Entry(
        val token: Long,
        val callerUid: ExternalKeyCallerUid,
        val alias: ExternalKeyAlias,
        val providerGeneration: ExternalKeyProviderGeneration,
        val algorithm: AuthenticationSigningAlgorithm,
        val digest: ByteArray,
        private var ownedSignatureBytes: ByteArray?,
    ) {
        var expiryCancellation: ReplayLeaseCancellation? = null

        fun matches(request: ExternalKeySignRequest): Boolean =
            request.matches(
                callerUid = callerUid,
                alias = alias,
                providerGeneration = providerGeneration,
                algorithm = algorithm,
                digest = digest,
            )

        fun takeSignatureBytes(): ByteArray =
            checkNotNull(ownedSignatureBytes) {
                "replay signature is no longer available"
            }.also {
                ownedSignatureBytes = null
            }

        fun clear() {
            expiryCancellation?.cancelForCleanup()
            expiryCancellation = null
            digest.fill(CLEARED_BYTE)
            ownedSignatureBytes?.fill(CLEARED_BYTE)
            ownedSignatureBytes = null
        }
    }

    private companion object {
        const val MILLISECONDS_PER_SECOND = 1_000L
        const val MINIMUM_TIMEOUT_MILLISECONDS = 1L
        const val DEFAULT_TIMEOUT_SECONDS = 5L
        const val DEFAULT_TIMEOUT_MILLISECONDS =
            DEFAULT_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND
        const val INITIAL_ENTRY_TOKEN = 1L
        const val TOKEN_INCREMENT = 1L
        const val CLEARED_BYTE: Byte = 0
    }
}

/** Timer cancellation is best effort; it must never prevent secret clearing. */
@Suppress("SwallowedException", "TooGenericExceptionCaught")
private fun ReplayLeaseCancellation.cancelForCleanup() {
    try {
        cancel()
    } catch (_: Exception) {
        // Cancellation is best-effort; an expired task is equivalent.
    }
}
