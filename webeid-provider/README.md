# RefineID Web eID provider

Answers the Web eID requests of the Estonian state authentication service
(TARA) with a card read directly through the RefineID PKCS#11 module
(`refineid_pkcs11.dll` on Windows, `librefineid_pkcs11.so` on FreeBSD/Linux).
Makes `www.eesti.ee` login work in Microsoft Edge and Mozilla Firefox without
OpenSSL, `pkcs11-provider`, or the official Web eID native application. The
observed TARA flow, message contract and token fields are recorded in
[TARA-LOGIN.md](TARA-LOGIN.md).

## How a login flows

1. The TARA page posts `web-eid:status` and, after the ID-card "Continue",
   `web-eid:authenticate` with a challenge nonce to its own window.
2. `ext/content.js` acknowledges the message and asks the background script
   to call the native messaging host `refineid_webeid`.
3. `host.py` loads the RefineID PKCS#11 module via `ctypes`, selects the
   `ESTeID` token, reads the cardholder authentication certificate
   (`CK_CERTIFICATE_CATEGORY_TOKEN_USER`), prompts for PIN1 in a local dialog,
   computes `SHA-384(SHA-384(origin) || SHA-384(challenge))`, signs it with
   `CKM_ECDSA`, and returns the certificate, raw 96-byte ES384 `r||s`
   signature and token fields.
4. The page posts the token to TARA, which validates the certificate and the
   signature and returns the `www.eesti.ee` session.

## Security

- Both `ext/background.js` and `host.py` allow only `https://tara.ria.ee`;
  any other origin is refused.
- The content script matches only `https://tara.ria.ee/*` and posts replies
  only to `window.location.origin`.
- PIN1 never reaches the extension, the page or any network, and is never
  stored on disk on Windows. The host prompts locally in a masked dialog and
  zeroes its mutable PIN buffer immediately after `C_Login` (note that Tk's
  entry widget produces an immutable Python string during collection, and the
  short-lived host process exits right after answering). On POSIX, an optional
  mode-600 `REFINEID_WEBEID_PIN_FILE` may be set for headless runs.
- No PIN, candidate length, challenge or cardholder data is logged.

## Install

```sh
python3 install.py --pkcs11 /path/to/refineid_pkcs11.dll
```

On Windows this installs to `%LOCALAPPDATA%\refineid-webeid` and registers:

- `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\refineid_webeid`
- `HKCU\Software\Mozilla\NativeMessagingHosts\refineid_webeid`

On POSIX it installs to `~/refineid-webeid` and registers both the Firefox
(`~/.mozilla/native-messaging-hosts/refineid_webeid.json`, or macOS
`Application Support/Mozilla/NativeMessagingHosts`) and Microsoft Edge
(`~/.config/microsoft-edge/NativeMessagingHosts/refineid_webeid.json`, or
macOS `Application Support/Microsoft Edge/NativeMessagingHosts`) native
messaging manifests.

### Load in Microsoft Edge (Windows)

1. Open `edge://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked** and choose `%LOCALAPPDATA%\refineid-webeid\ext-edge`.
   (Unpacked extensions remain loaded across Edge restarts.)

### Load in Mozilla Firefox (Windows / FreeBSD / Linux)

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...** and select
   `%LOCALAPPDATA%\refineid-webeid\ext-firefox\manifest.json` (or
   `~/refineid-webeid/ext-firefox/manifest.json` on POSIX).
   (Temporary add-ons are dropped when Firefox restarts and must be loaded
   again unless signed.)

### Verify host and card detection (without PIN)

```sh
python host.py --probe
```

Run from the install directory; prints only token model, certificate byte
length and key type (no serials or personal data).

## Configuration

`install.py` writes `config.json` next to `host.py` with `pkcs11_module` and
`model` (default `ESTeID`). Environment variables override it:

- `REFINEID_WEBEID_PKCS11`: path to the RefineID PKCS#11 shared library.
- `REFINEID_WEBEID_MODEL`: PKCS#11 token model (default `ESTeID`).
- `REFINEID_WEBEID_PIN_FILE`: POSIX-only mode-600 PIN file for headless runs.
