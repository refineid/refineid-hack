package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.AuthenticationSigningInputMode
import fi.refineid.android.core.acceptsInputLength
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.atomic.AtomicBoolean

/** Closed alias vocabulary exported by the privileged KeyChain provider. */
internal enum class ExternalKeyAlias(
    val wireValue: String,
) {
    AUTHENTICATION("refineid-client-authentication"),
    ;

    companion object {
        fun fromWireValue(value: String): ExternalKeyAlias? = entries.firstOrNull { alias -> alias.wireValue == value }
    }
}

@JvmInline
internal value class ExternalKeyCallerUid(
    val value: Int,
) {
    init {
        require(value >= MINIMUM_CALLER_UID) {
            "external-key caller UID must not be negative"
        }
    }

    private companion object {
        const val MINIMUM_CALLER_UID = 0
    }
}

@JvmInline
internal value class ExternalKeyProviderGeneration(
    val value: Long,
) {
    init {
        require(value >= INITIAL_PROVIDER_GENERATION) {
            "external-key provider generation must be positive"
        }
    }

    private companion object {
        const val INITIAL_PROVIDER_GENERATION = 1L
    }
}

/** Fresh positive provider generation for one opened card session. */
internal fun SecureRandom.nextProviderGeneration(): Long {
    var generation: Long
    do {
        generation = nextLong() and PROVIDER_GENERATION_VALUE_MASK
    } while (generation < MINIMUM_PROVIDER_GENERATION)
    return generation
}

private const val MINIMUM_PROVIDER_GENERATION = 1L
private const val PROVIDER_GENERATION_VALUE_MASK = Long.MAX_VALUE

/** KeyChain-derived browser identity used for holder-facing attribution. */
internal class ExternalKeyCaller private constructor(
    val uid: ExternalKeyCallerUid,
    private val packageNames: List<String>,
) {
    val packageCount: Int
        get() = packageNames.size

    fun copyPackageNames(): List<String> = packageNames.toList()

    override fun toString(): String = "ExternalKeyCaller(packageCount=$packageCount)"

    companion object {
        fun create(
            uid: ExternalKeyCallerUid,
            packageNames: Array<out String>,
        ): ExternalKeyCaller {
            require(packageNames.size in MINIMUM_PACKAGE_COUNT..MAXIMUM_PACKAGE_COUNT) {
                "external-key caller package count is outside the supported range"
            }
            val sortedPackages = packageNames.sorted()
            require(
                sortedPackages.all { packageName ->
                    PACKAGE_NAME_PATTERN.matches(packageName) &&
                        packageName.length <= MAXIMUM_PACKAGE_NAME_LENGTH
                },
            ) {
                "external-key caller package is invalid"
            }
            require(sortedPackages.zipWithNext().all { (first, second) -> first != second }) {
                "external-key caller packages contain a duplicate"
            }
            return ExternalKeyCaller(
                uid = uid,
                packageNames = sortedPackages,
            )
        }

        private const val MINIMUM_PACKAGE_COUNT = 1
        private const val MAXIMUM_PACKAGE_COUNT = 16
        private const val MAXIMUM_PACKAGE_NAME_LENGTH = 255
        private val PACKAGE_NAME_PATTERN = Regex("[A-Za-z0-9_.]+")
    }
}

/** Android-free cancellation signal driven by the browser-owned Binder token. */
internal class ExternalKeyOperationCancellation {
    private val lock = Any()
    private val cancelled = AtomicBoolean(false)
    private var cancellationAction: (() -> Unit)? = null

    val isCancelled: Boolean
        get() = cancelled.get()

    fun cancel() {
        if (!cancelled.compareAndSet(false, true)) {
            return
        }
        val action = synchronized(lock) { cancellationAction }
        invokeForCancellation(action)
    }

    fun register(action: () -> Unit): AutoCloseable {
        val invokeImmediately =
            synchronized(lock) {
                check(cancellationAction == null) {
                    "external-key cancellation already has a listener"
                }
                if (isCancelled) {
                    true
                } else {
                    cancellationAction = action
                    false
                }
            }
        if (invokeImmediately) {
            invokeForCancellation(action)
        }
        return AutoCloseable {
            synchronized(lock) {
                if (cancellationAction === action) {
                    cancellationAction = null
                }
            }
        }
    }

    override fun toString(): String = "ExternalKeyOperationCancellation(cancelled=$isCancelled)"

    @Suppress("SwallowedException", "TooGenericExceptionCaught")
    private fun invokeForCancellation(action: (() -> Unit)?) {
        try {
            action?.invoke()
        } catch (_: Exception) {
            // Completion callbacks are best-effort on an abandoned request.
        }
    }
}

/** One validated exact-digest request received from the trusted KeyChain service. */
internal class ExternalKeySignRequest private constructor(
    val caller: ExternalKeyCaller,
    val alias: ExternalKeyAlias,
    val providerGeneration: ExternalKeyProviderGeneration,
    val algorithm: AuthenticationSigningAlgorithm,
    val cancellation: ExternalKeyOperationCancellation,
    private var ownedDigest: ByteArray?,
) : AutoCloseable {
    val callerUid: ExternalKeyCallerUid
        get() = caller.uid

    val digestLength: Int
        get() = algorithm.digestLength

    internal fun copyDigest(): ByteArray =
        synchronized(this) {
            requireDigest().copyOf()
        }

    internal fun matches(
        callerUid: ExternalKeyCallerUid,
        alias: ExternalKeyAlias,
        providerGeneration: ExternalKeyProviderGeneration,
        algorithm: AuthenticationSigningAlgorithm,
        digest: ByteArray,
    ): Boolean {
        if (
            this.callerUid != callerUid ||
            this.alias != alias ||
            this.providerGeneration != providerGeneration ||
            this.algorithm != algorithm
        ) {
            return false
        }
        return synchronized(this) {
            MessageDigest.isEqual(requireDigest(), digest)
        }
    }

    override fun close() {
        synchronized(this) {
            ownedDigest?.fill(CLEARED_BYTE)
            ownedDigest = null
        }
    }

    override fun toString(): String =
        synchronized(this) {
            "ExternalKeySignRequest(" +
                "alias=" + alias +
                ", generation=" + providerGeneration.value +
                ", algorithm=" + algorithm +
                ", digestLength=" + digestLength +
                ", closed=" + (ownedDigest == null) +
                ")"
        }

    private fun requireDigest(): ByteArray =
        checkNotNull(ownedDigest) {
            "external-key sign request is closed"
        }

    companion object {
        fun create(
            caller: ExternalKeyCaller,
            alias: ExternalKeyAlias,
            providerGeneration: ExternalKeyProviderGeneration,
            algorithm: AuthenticationSigningAlgorithm,
            digest: ByteArray,
            cancellation: ExternalKeyOperationCancellation,
        ): ExternalKeySignRequest {
            require(
                algorithm.acceptsInputLength(
                    AuthenticationSigningInputMode.PREHASHED,
                    digest.size,
                ),
            ) {
                "external-key digest length does not match the algorithm"
            }
            return ExternalKeySignRequest(
                caller = caller,
                alias = alias,
                providerGeneration = providerGeneration,
                algorithm = algorithm,
                cancellation = cancellation,
                ownedDigest = digest.copyOf(),
            )
        }

        private const val CLEARED_BYTE: Byte = 0
    }
}
