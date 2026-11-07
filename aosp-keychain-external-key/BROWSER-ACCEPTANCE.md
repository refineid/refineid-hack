# System-browser acceptance

This gate proves normal-browser authentication on the patched Pixel. An
in-application WebView run is not accepted as evidence for any row below.

## Prerequisites

1. Build and boot the exact `android-13.0.0_r31` image from [`BUILD.md`](BUILD.md).
2. Pass `scripts/verify-aosp-flame-device.sh` from its x86_64 Linux checkout.
3. Install independently signed Chrome and Firefox packages on the AOSP image.
   Browser APKs and their licenses remain outside this repository. Preserve a
   local copy of any required stock-device split APKs before the bootloader
   wipe, or obtain the browser from its official distribution channel.
4. Connect the reader as the phone's USB host and power the phone separately.
5. Use a client-authentication endpoint whose TLS profile and privacy policy
   are known. It must record only a coarse handshake result, never a card
   certificate, subject, serial, identity code, signature, or network address.

Record the browser package, browser version, card key profile, TLS profile,
and pass/fail result locally. Do not record a device identifier or credential.
Do not capture the chooser, certificate details, secure PIN screen, browser
page, accessibility hierarchy, packet trace, APDU trace, or logcat.

## Platform-to-browser boundary

For each browser, begin without an alias grant:

1. Open the client-authentication origin in the browser process itself.
2. Confirm Android's KeyChain chooser offers the active external identity only
   when the card and verified issuing certificate are available.
3. Select the identity and grant it to that browser.
4. Confirm the unexported RefineID prompt identifies the requesting browser.
5. Enter PIN1 manually. Automation must not read, inject, paste, or submit it.
6. Confirm one locally verified card operation produces the browser signature.
7. Repeat after killing the browser during the prompt; the prompt and pending
   operation must cancel without a signature.
8. Repeat after detaching the reader and after replacing the active card; both
   stale-generation paths must fail closed.

The KeyChain chooser and RefineID prompt are expected holder interactions.
"Automatic browser support" means the unmodified browser uses Android's
normal KeyChain/JCA path; it does not mean silent alias selection or PIN entry.

## Chrome

The RSA card profile can exercise Chrome's named RSA PKCS#1 and RSA-PSS JCA
paths. A TLS 1.3 endpoint is suitable for the RSA-PSS row. The ECDSA card
profile exercises the named ECDSA paths. Passing requires a completed
CertificateVerify operation from the independent Chrome process and one
holder prompt per new card operation.

## Firefox

Firefox's Android bridge performs two identical calls for its supported
`NoneWithRSA` or `NoneWithECDSA` path. Passing requires one holder prompt and
one card operation, followed by the bounded one-result replay.

For an RSA card, use a TLS 1.2 test profile that selects RSA PKCS#1.
Unmodified Firefox's raw RSA-PSS path is intentionally unsupported because it
provides an already encoded randomized block instead of the digest the card
API requires. A P-384 ECDSA card does not have that limitation. Treat an
RSA-PSS-only Firefox failure as the documented algorithm boundary, not as a
passing browser test.

## Revoked development card

The older RSA development card is revoked. It can prove chooser discovery,
holder consent, browser-process cancellation, card signing, replay, and local
signature verification. A production relying party may and should reject its
certificate.

For this card, browser plumbing is proven only when the controlled diagnostic
endpoint distinguishes a valid CertificateVerify signature from the expected
revocation-policy result. Do not weaken revocation checks on a production or
public relying party to make this test pass.

## Service acceptance

Suomi.fi or another production relying party is the final acceptance gate. It
requires a valid, non-revoked card and the relying party's supported browser
algorithm profile. Passing requires successful holder authentication in both
target browsers for their supported matrix rows. A revoked-card result cannot
prove or disprove this row.

The Android port is not complete until the platform verifier and the physical
independent-browser rows have passed. Source replay, an app-owned WebView, a
chooser appearance without signing, or a synthetic JCA test is insufficient.
