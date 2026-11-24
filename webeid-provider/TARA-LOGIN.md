# TARA login flow and token contract

Observed on `www.eesti.ee` -> `tara.ria.ee` with an Estonian eID card and
an ACS ACR39U reader. The `eu.webeid` native host in this directory answers
the official Web eID browser extension using Windows CNG (native Windows
Security Smart Card PIN prompt) on Windows and RefineID's PKCS#11 module on
POSIX.

## Flow

The self-service login opens:

    https://tara.ria.ee/auth/init?login_challenge=<challenge>

The ID-card tab's "Continue" is a JavaScript click on
`#idCardForm button`, not a form submit. TARA's `/scripts/main.js`:

1. POSTs `/auth/id/init` with the page CSRF token and reads `nonce`.
2. Calls `webeid.status()`.
3. Calls `webeid.authenticate(nonce, {lang})`.
4. POSTs `/auth/id/login` with `{authToken, statusDurationMs,
   extensionVersion, nativeAppVersion}`.
5. Submits `#idCardForm` to `/auth/accept`.

`/auth/init` sends no TLS `CertificateRequest`. Without the ID-software
the page answers: "Logging in with ID-card without latest ID-software is
not supported in the State authentication service." The login is Web
eID, not TLS client certificate authentication.

## Native messaging protocol (`eu.webeid`, Web eID 2.x)

The official Web eID browser extension connects to native host `eu.webeid`:

1. On startup the native host immediately sends `{"version": "2.5.0"}`
   (bare SemVer; major version must be at least the extension and library
   major version).
2. For `status`, the extension sends `{"command": "quit", "arguments": {}}`;
   the host replies `{}` and exits.
3. For `authenticate`, the extension sends
   `{"command": "authenticate", "arguments": {"challengeNonce", "origin", "lang?"}}`;
   the host replies with the authentication token below (or
   `{"error": {"code": "ERR_WEBEID_...", "message": "..."}}`).

## Token

- `unverifiedCertificate`: base64 DER authentication certificate.
- `algorithm`: `ES384` (or `ES256`/`ES512`/`RS256` matching the card key).
- `signature`: base64 raw ECDSA `r||s` (96 bytes for P-384) or RSA PKCS#1 v1.5.
- Signed data: `H(origin) + H(challenge)`, hashed again with `H` before
  signing (`SHA-384` for `ES384`). Origin is `https://tara.ria.ee`.
- `format`: `web-eid:1.0`.
- `appVersion`: informative.

Both Windows CNG `NCryptSignHash` and RefineID PKCS#11 `CKM_ECDSA` return raw
`r||s` directly for ECDSA keys.

## Reproduce

1. Install the official **Web eID** extension in Edge or Firefox.
2. Run `python install.py` on Windows (or `python3 install.py --pkcs11 ...` on POSIX).
3. Open `www.eesti.ee`, choose self-service, keep the ID-card tab, press
   Continue, and enter PIN1 in the native Windows Security prompt.

Sources: TARA page and script, observed; official `web-eid-webextension` 2.5.0;
Web eID authentication token format specification,
<https://web-eid.github.io/web-eid-system-architecture-doc/>;
id.ee, "Web eID", <https://www.id.ee/en/article/web-eid/>.
