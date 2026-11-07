package fi.refineid.android.keychain

/** Public identity copied from one active card generation. */
internal class ExternalKeyIdentitySnapshot private constructor(
    val providerGeneration: ExternalKeyProviderGeneration,
    private var ownedLeafCertificate: ByteArray?,
    private var ownedCaCertificates: ByteArray?,
) : AutoCloseable {
    fun copyLeafCertificate(): ByteArray =
        synchronized(this) {
            requireNotNull(ownedLeafCertificate) {
                "external-key identity is closed"
            }.copyOf()
        }

    fun copyCaCertificates(): ByteArray? =
        synchronized(this) {
            check(ownedLeafCertificate != null) {
                "external-key identity is closed"
            }
            ownedCaCertificates?.copyOf()
        }

    override fun close() {
        synchronized(this) {
            ownedLeafCertificate?.fill(CLEARED_BYTE)
            ownedCaCertificates?.fill(CLEARED_BYTE)
            ownedLeafCertificate = null
            ownedCaCertificates = null
        }
    }

    override fun toString(): String =
        synchronized(this) {
            "ExternalKeyIdentitySnapshot(" +
                "generation=" + providerGeneration.value +
                ", leafLength=" + (ownedLeafCertificate?.size ?: NO_CERTIFICATE_BYTES) +
                ", chainLength=" + (ownedCaCertificates?.size ?: NO_CERTIFICATE_BYTES) +
                ", closed=" + (ownedLeafCertificate == null) +
                ")"
        }

    companion object {
        fun create(
            providerGeneration: ExternalKeyProviderGeneration,
            leafCertificate: ByteArray,
            caCertificates: ByteArray?,
        ): ExternalKeyIdentitySnapshot {
            require(leafCertificate.isNotEmpty()) {
                "external-key leaf certificate is empty"
            }
            require(caCertificates == null || caCertificates.isNotEmpty()) {
                "external-key CA certificate chain is empty"
            }
            return ExternalKeyIdentitySnapshot(
                providerGeneration = providerGeneration,
                ownedLeafCertificate = leafCertificate.copyOf(),
                ownedCaCertificates = caCertificates?.copyOf(),
            )
        }

        private const val NO_CERTIFICATE_BYTES = 0
        private const val CLEARED_BYTE: Byte = 0
    }
}

/** Process-owned provider boundary consumed only by the trusted Binder stub. */
internal interface ExternalKeyProviderBackend {
    fun copyActiveIdentity(): ExternalKeyIdentitySnapshot?

    fun sign(request: ExternalKeySignRequest): ExternalKeySignResult

    fun removeIdentity(providerGeneration: ExternalKeyProviderGeneration): Boolean
}
