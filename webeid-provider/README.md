# RefineID Web eID provider

Answers the Web eID requests of the Estonian state authentication service
(TARA) with a card read through the RefineID PKCS#11 module. It is the
piece that makes `www.eesti.ee` login work in Firefox on FreeBSD, where
the official Web eID native application has no build. The observed TARA
flow, the message contract and the token fields are recorded in
[TARA-LOGIN.md](TARA-LOGIN.md).

## How a login flows

1. The TARA page posts `web-eid:status` and, after the ID-card
   "Continue", `web-eid:authenticate` with a challenge nonce to its own
   window.
2. `ext/content.js` acknowledges the message and asks the background
   script to authenticate.
3. `ext/background.js` forwards the request to the native messaging host
   `refineid_webeid`.
4. `host.py` builds the Web eID token: `SHA-384(origin)` followed by
   `SHA-384(challenge)`, signed with the card's authentication key
   through the OpenSSL pkcs11-provider and the RefineID module, and
   returns the certificate, the ES384 signature and the token fields.
5. The page posts the token to TARA, which validates the certificate and
   the signature and returns the session.

## Security

- The host signs only for `https://tara.ria.ee`; any other origin is
  refused.
- The content script matches only `https://tara.ria.ee/*`.
- The PIN never reaches the extension, the page or any network. The host
  reads it from a local file whose permissions must not grant group or
  other access (mode 600).
- No PIN, candidate length or card holder data is logged.

## Install

```
./install.sh
```

This copies the host to `~/refineid-webeid` and writes
`~/.mozilla/native-messaging-hosts/refineid_webeid.json`.

Load the extension in Firefox: `about:debugging#/runtime/this-firefox`,
"Load Temporary Add-on...", select `~/refineid-webeid/ext/manifest.json`.

## Limits

- The add-on is temporary: Firefox drops it when it restarts, so it is
  loaded again from about:debugging. A signed extension would make it
  permanent.
- `REFINEID_WEBEID_PIN_FILE` points at the PIN file (default
  `/tmp/temp-pin1.txt`). `REFINEID_OPENSSL_CONF` points at the
  pkcs11-provider configuration (default `~/refineid-openssl.cnf`).

## Verified

On FreeBSD 15.1 with a card in an ACS ACR39U reader, Firefox 160 loaded
the add-on, TARA issued a challenge, the host produced an ES384 token
that verified against the card certificate, and TARA returned the
eesti.ee self-service session.
