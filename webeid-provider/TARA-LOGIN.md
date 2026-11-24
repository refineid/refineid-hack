# TARA login flow and token contract

Observed on `www.eesti.ee` -> `tara.ria.ee` with an Estonian eID card,
an ACS ACR39U reader and the RefineID PKCS#11 module. The provider in
this directory is what answers the page.

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

## Messages

The page bundles web-eid.js 2.0.2 and posts to its own window. A content
script or extension answers:

| Request | Ack | Success | Failure |
| --- | --- | --- | --- |
| `web-eid:status` | `status-ack` | `status-success` `{extension, nativeApp}` | `status-failure` |
| `web-eid:authenticate` | `authenticate-ack` | `authenticate-success` `{unverifiedCertificate, algorithm, signature, format, appVersion}` | `authenticate-failure` `{error:{code,message}}` |

`authenticate` carries `libraryVersion`, `challengeNonce` and `options`.
Timeouts: ack 1 s, native handshake 5 s, user interaction 120 s.

## Token

- `unverifiedCertificate`: base64 DER authentication certificate.
- `algorithm`: `ES384`.
- `signature`: base64 raw 96-byte ECDSA `r||s`.
- Signed data: `SHA-384(origin) + SHA-384(challenge)`, signed as ES384
  (the host hashes the 96-byte value with SHA-384 and passes the 48-byte
  digest to `CKM_ECDSA`). Origin is `https://tara.ria.ee`.
- `format`: `web-eid:1.0`.
- `appVersion`: informative.

The card signs with PIN1 and `CKM_ECDSA` through the RefineID PKCS#11
module, which returns the raw 96-byte `r||s` directly.

## Reproduce

```sh
python3 install.py --pkcs11 /path/to/refineid_pkcs11.dll
```

Load `ext-edge` in Edge (`edge://extensions` -> Load unpacked) or
`ext-firefox/manifest.json` in Firefox (`about:debugging#/runtime/this-firefox`),
open `www.eesti.ee`, choose self-service, keep the ID-card tab and press
Continue.

The host logs nothing, refuses origins other than `https://tara.ria.ee`,
prompts locally for PIN1 in a masked dialog (zeroing its mutable PIN buffer
right after `C_Login`), and on POSIX refuses any `REFINEID_WEBEID_PIN_FILE`
readable by group or others.

## Evidence

On FreeBSD 15.1 with an ACS ACR39U reader and Firefox 160, the token
signature verified against the card certificate and TARA returned the
eesti.ee self-service dashboard. The page contract above is read from
`/scripts/main.js`; a TARA change can move it.

Sources: TARA page and script, observed; Web eID authentication token
format specification, <https://web-eid.github.io/web-eid-system-architecture-doc/>;
id.ee, "Web eID", <https://www.id.ee/en/article/web-eid/>.
