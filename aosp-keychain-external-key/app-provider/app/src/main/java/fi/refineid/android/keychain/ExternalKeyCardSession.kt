package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.NativeAuthenticationCertificate
import fi.refineid.android.core.Pin1Submission

/** One copied public identity owned by the caller. */
internal class ExternalKeyCardIdentity(
    val providerGeneration: ExternalKeyProviderGeneration,
    val certificate: NativeAuthenticationCertificate,
) : AutoCloseable {
    override fun close() {
        certificate.close()
    }

    override fun toString(): String =
        "ExternalKeyCardIdentity(" +
            "profile=" + certificate.keyProfile +
            ", certificateLength=" + certificate.derLength +
            ")"
}

internal fun interface ExternalKeyCardIdentitySource {
    fun copyActiveIdentity(): ExternalKeyCardIdentity?
}

/** Generation-bound card operation used only by the privileged provider. */
internal fun interface ExternalKeyCardSigner {
    fun signAuthenticationDigest(
        providerGeneration: ExternalKeyProviderGeneration,
        algorithm: AuthenticationSigningAlgorithm,
        pin1: Pin1Submission,
        digest: ByteArray,
    ): AuthenticationSignResult
}

internal interface ExternalKeyCardSession :
    ExternalKeyCardIdentitySource,
    ExternalKeyCardSigner
