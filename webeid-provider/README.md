# RefineID Web eID native host (`eu.webeid`)

Implements the official `eu.webeid` native messaging host protocol backed by
the RefineID PKCS#11 module (`refineid_pkcs11.dll` on Windows,
`librefineid_pkcs11.so` on FreeBSD/Linux). Works directly with the official
store-signed **Web eID** browser extension in Microsoft Edge, Google Chrome
and Mozilla Firefox — no Developer Mode, no temporary add-ons, and no OpenSSL
or `pkcs11-provider`. The observed TARA flow and token fields are recorded in
[TARA-LOGIN.md](TARA-LOGIN.md).

## How a login flows

1. The site (e.g. `www.eesti.ee` -> `https://tara.ria.ee`) uses `web-eid.js`
   to talk to the official **Web eID** browser extension.
2. The extension launches the registered `eu.webeid` native messaging host
   (`host.py`), which immediately announces `{"version": "2.5.0"}`.
3. For `status`, the extension sends `{"command": "quit", "arguments": {}}`
   and the host replies `{}` and exits.
4. For `authenticate`, the extension sends
   `{"command": "authenticate", "arguments": {"challengeNonce", "origin"}}`.
   `host.py` loads the RefineID PKCS#11 module via `ctypes`, selects the
   `ESTeID` token, reads the cardholder authentication certificate
   (`CK_CERTIFICATE_CATEGORY_TOKEN_USER`), prompts for PIN1 in a topmost local
   dialog displaying the exact origin, computes
   `SHA-384(SHA-384(origin) || SHA-384(challengeNonce))`, signs it with
   `CKM_ECDSA`, and returns the Web eID 1.0 authentication token.

## Security

- Only canonical `https://` origins (no path, query, fragment or userinfo) are
  accepted; interactive prompts always display the exact requesting origin.
  Optional `REFINEID_WEBEID_ALLOWED_ORIGINS` (or `allowed_origins` in
  `config.json`) restricts allowed origins further.
- PIN1 never reaches the extension, the page or any network, and is never
  stored on disk on Windows. The host prompts locally in a masked dialog and
  zeroes its mutable PIN buffer immediately after `C_Login` (note that Tk's
  entry widget produces an immutable Python string during collection, and the
  short-lived host process exits right after answering).
- On POSIX, an optional mode-600 `REFINEID_WEBEID_PIN_FILE` may be set for
  headless runs; in that mode origins default to `https://tara.ria.ee` unless
  `REFINEID_WEBEID_ALLOWED_ORIGINS` is explicitly set.
- No PIN, candidate length, challenge or cardholder data is logged.

## Install

1. Install the official **Web eID** extension in your browser:
   - Microsoft Edge: [Edge Add-ons](https://microsoftedge.microsoft.com/addons/detail/gnmckgbandlkacikdndelhfghdejfido)
   - Mozilla Firefox: [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/web-eid-webextension/)
   - Google Chrome: [Chrome Web Store](https://chromewebstore.google.com/detail/web-eid/ncibgoaomkmdpilpocfeponihegamlic)
2. Register RefineID as the `eu.webeid` native messaging host:

```sh
python3 install.py --pkcs11 /path/to/refineid_pkcs11.dll
```

On Windows this installs to `%LOCALAPPDATA%\refineid-webeid` and registers
`eu.webeid` under `HKCU\Software\{Microsoft\Edge,Google\Chrome,Mozilla}\NativeMessagingHosts\eu.webeid`.

On POSIX it installs to `~/refineid-webeid` and registers `eu.webeid.json` in
the Mozilla, Microsoft Edge and Google Chrome native-messaging-host
directories.

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
- `REFINEID_WEBEID_ALLOWED_ORIGINS`: optional comma-separated HTTPS origin
  allowlist.
- `REFINEID_WEBEID_PIN_FILE`: POSIX-only mode-600 PIN file for headless runs.
