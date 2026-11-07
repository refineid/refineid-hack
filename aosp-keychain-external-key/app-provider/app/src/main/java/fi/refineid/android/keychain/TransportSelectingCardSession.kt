package fi.refineid.android.keychain

import fi.refineid.android.core.AuthenticationSignFailure
import fi.refineid.android.core.AuthenticationSignResult
import fi.refineid.android.core.AuthenticationSigningAlgorithm
import fi.refineid.android.core.Pin1Submission

/**
 * One provider-facing card session over several transports. Identity
 * queries prefer the first session with a card; signing routes by the
 * request's provider generation, so a credential is only ever submitted
 * to the transport that published the matching identity. Each transport
 * mints its generation from a shared positive 63-bit space, which keeps
 * generations distinct across transports.
 */
internal class TransportSelectingCardSession(
    private val sessions: List<ExternalKeyCardSession>,
) : ExternalKeyCardSession {
    override fun copyActiveIdentity(): ExternalKeyCardIdentity? {
        for (session in sessions) {
            val identity = session.copyActiveIdentity()
            if (identity != null) {
                return identity
            }
        }
        return null
    }

    /** The identity the transport holding [providerGeneration] publishes; null when none holds it. */
    fun copyIdentity(providerGeneration: ExternalKeyProviderGeneration): ExternalKeyCardIdentity? {
        for (session in sessions) {
            val identity = session.copyActiveIdentity() ?: continue
            if (identity.providerGeneration == providerGeneration) return identity
            identity.close()
        }
        return null
    }

    override fun signAuthenticationDigest(
        providerGeneration: ExternalKeyProviderGeneration,
        algorithm: AuthenticationSigningAlgorithm,
        pin1: Pin1Submission,
        digest: ByteArray,
    ): AuthenticationSignResult {
        val delegate =
            sessions.firstOrNull { session ->
                session.holdsGeneration(providerGeneration)
            }
        if (delegate == null) {
            pin1.close()
            return AuthenticationSignResult.Failure(AuthenticationSignFailure.CARD_UNAVAILABLE)
        }
        return delegate.signAuthenticationDigest(
            providerGeneration = providerGeneration,
            algorithm = algorithm,
            pin1 = pin1,
            digest = digest,
        )
    }

    override fun toString(): String = "TransportSelectingCardSession(sessions=" + sessions.size + ")"
}

private fun ExternalKeyCardSession.holdsGeneration(providerGeneration: ExternalKeyProviderGeneration): Boolean {
    val identity = copyActiveIdentity() ?: return false
    return identity.use { it.providerGeneration == providerGeneration }
}
