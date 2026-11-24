# RefineID Web eID native host (`eu.webeid`)

Implements the official `eu.webeid` native messaging host protocol for the
official store-signed **Web eID** browser extension in Microsoft Edge, Google
Chrome and Mozilla Firefox — no Developer Mode, no temporary add-ons, and no
OpenSSL or `pkcs11-provider`. The observed TARA flow and token fields are
recorded in [TARA-LOGIN.md](TARA-LOGIN.md).

- **On Windows 11:** signs through Windows CNG (`Microsoft Smart Card Key
  Storage Provider` / `NCryptSignHash`), so **Windows itself renders the
  native Windows Security Smart Card PIN prompt** (and native certificate
  picker if multiple live smart cards are inserted). PIN1 never enters this
  process.
- **On POSIX (FreeBSD / Linux / macOS):** talks directly to the RefineID
  PKCS#11 v2.40 module (`librefineid_pkcs11.so` / `.dylib`) via `ctypes`.

## How a login flows

1. The site (e.g. `www.eesti.ee` -> `https://tara.ria.ee`) uses `web-eid.js`
   to talk to the official **Web eID** browser extension.
2. The extension launches the registered `eu.webeid` native messaging host
   (`host.py`), which immediately announces `{"version": "2.5.0"}`.
3. For `status`, the extension sends `{"command": "quit", "arguments": {}}`
   and the host replies `{}` and exits.
4. For `authenticate`, the extension sends
   `{"command": "authenticate", "arguments": {"challengeNonce", "origin"}}`.
   - On Windows, `host.py` selects the live `ClientAuth` certificate from
     `CurrentUser\MY` under `Microsoft Smart Card Key Storage Provider`, sets
     `NCRYPT_USE_CONTEXT_PROPERTY` localized to the Windows UI language
     (Finnish, Swedish, Estonian, English), computes
     `H(H(origin) || H(challengeNonce))`, and calls `NCryptSignHash` — Windows
     prompts natively for PIN1 and returns the signature (`ES384`/`ES256`/`RS256`).
   - On POSIX, `host.py` loads the RefineID PKCS#11 module, selects `ESTeID`,
     reads the cardholder auth cert (`CK_CERTIFICATE_CATEGORY_TOKEN_USER`),
     prompts locally for PIN1, and signs via `CKM_ECDSA`.

## Security

- Only canonical `https://` origins (no path, query, fragment or userinfo) are
  accepted; the requesting origin is shown in the Windows Security prompt (via
  `NCRYPT_USE_CONTEXT_PROPERTY`) or POSIX dialog. Optional
  `REFINEID_WEBEID_ALLOWED_ORIGINS` (or `allowed_origins` in `config.json`)
  restricts allowed origins further.
- On Windows, PIN1 never enters `host.py` at all — Windows Security hands it
  directly to the card's registered Windows minidriver.
- On POSIX, PIN1 never reaches the extension, page or network; the host zeroes
  its mutable PIN buffer after `C_Login` (note that Tk's entry widget produces
  an immutable Python string during collection, and the short-lived host exits
  immediately after answering). An optional mode-600 `REFINEID_WEBEID_PIN_FILE`
  may be set for headless POSIX runs, which restricts origins to
  `https://tara.ria.ee` unless `REFINEID_WEBEID_ALLOWED_ORIGINS` is set.
- No PIN, candidate length, challenge or cardholder data is logged.

## Install

1. Install the official **Web eID** extension in your browser:
   - Microsoft Edge: [Edge Add-ons](https://microsoftedge.microsoft.com/addons/detail/gnmckgbandlkacikdndelhfghdejfido)
   - Mozilla Firefox: [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/web-eid-webextension/)
   - Google Chrome: [Chrome Web Store](https://chromewebstore.google.com/detail/web-eid/ncibgoaomkmdpilpocfeponihegamlic)
2. Register RefineID as the `eu.webeid` native messaging host:

On Windows (uses Windows CNG; no PKCS#11 path needed):

```powershell
python install.py
```

On POSIX:

```sh
python3 install.py --pkcs11 /path/to/librefineid_pkcs11.so
```

### Verify smart card detection (without PIN)

```sh
python host.py --probe
```

Run from the install directory; prints only backend, algorithm, key size and
certificate byte length (no serials or personal data, no PIN prompt).

## Configuration

`install.py` writes `config.json` next to `host.py`. Environment variables
override it:

- `REFINEID_WEBEID_ALLOWED_ORIGINS`: optional comma-separated HTTPS origin
  allowlist.
- `REFINEID_WEBEID_PKCS11`: POSIX PKCS#11 shared library path.
- `REFINEID_WEBEID_MODEL`: POSIX PKCS#11 token model (default `ESTeID`).
- `REFINEID_WEBEID_PIN_FILE`: POSIX-only mode-600 PIN file for headless runs.
