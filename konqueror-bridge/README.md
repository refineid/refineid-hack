<!--
Copyright 2026 Petri Koistinen

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Card login for Konqueror on FreeBSD

A hack, not supported and not part of any RefineID package. This is a local
man-in-the-middle proxy for five domains, and it needs a rebuilt Konqueror part
after every upgrade. It was tried on FreeBSD 15.1 with Konqueror 26.08.2. It has
never completed a login with a card.

QtWebEngine on FreeBSD is built without NSS client certificates, and its own
client certificate store needs an exportable private key, so it cannot log in
with a card. `refineid-mtls-proxy` is a loopback proxy that does the client
side of that TLS connection for Konqueror.

## What it does

- It listens on `127.0.0.1:18443` and answers only a client that presents the
  per-start secret from the user's bridge directory. The secret is regenerated
  each time the proxy starts. If another local process is listening on the port
  when the patched Konqueror part connects, the browser sends that secret to it;
  starting the proxy after such a capture invalidates the captured secret.
- CONNECT to port 443 of `card.refineid.fi`, `suomi.fi`, `fineid.fi`, `dvv.fi`,
  `vrk.fi` or a subdomain of one of them is terminated. The match is on whole
  name labels. All other traffic is relayed untouched.
- A terminated connection is verified against the system trust store and the
  server's name. A failure closes the connection.
- The card is read and PIN 1 asked only when a verified server asks for a
  client certificate. A cancelled PIN dialog never reaches the card.

Nothing is added to system trust. On first start the proxy creates a private
key and a self-signed certificate in `$XDG_DATA_HOME/refineid/bridge`
(`~/.local/share/refineid/bridge`), readable only by the user. The proxy
secret in that directory is replaced on every start. The patched Konqueror part
trusts that one public key through Chromium's
`--ignore-certificate-errors-spki-list`, and only in Konqueror.

Anything that runs as the user can read those files and use the proxy. It still
cannot get PIN 1 or the card's key out of it. If an attacker-controlled process
listens on `127.0.0.1:18443` before the proxy binds, the patched browser may
send the proxy secret to it; restart the proxy to rotate the secret. The
loopback proxy is a local man-in-the-middle for the listed domains by design.

## Known limits

- Plain `http://` pages fail: the proxy answers only CONNECT.
- A reader token whose key fails to load does not fall back to the phone token.
- The first certificate and first key of a token are paired without a check.
- A missing `cert.pem` beside an existing `key.pem` stops the proxy until the
  directory is removed.
- Connections and threads are unbounded.

## Install

```sh
pkg install qt6-base openssl-pkcs11provider p11-kit
make
sudo make install
```

The RefineID PKCS#11 module must be registered with p11-kit, which the
package does.

Apply `webenginepart-mtls-bridge.patch` to the Konqueror source of the installed
version, build the part and replace it:

```sh
cd konqueror-<version>
patch -p0 < .../konqueror-bridge/webenginepart-mtls-bridge.patch
cmake -B build -G Ninja -DCMAKE_INSTALL_PREFIX=/usr/local
ninja -C build webenginepart
sudo install build/bin/kf6/parts/webenginepart.so /usr/local/lib/qt6/plugins/kf6/parts/
```

A Konqueror package upgrade replaces the part, so repeat this after one. Log out
and in to start the proxy. Without the proxy running, the part uses the
configured proxy of the desktop as usual.
