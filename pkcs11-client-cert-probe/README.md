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

# PKCS#11 client-certificate probe

A hack, not supported. One command-line request against a TLS
client-certificate gate, with the certificate and key read from a
PKCS#11 token through an OpenSSL `pkcs11-provider`. Written against
RefineID's module but not tied to it: any module the provider can load
works.

```sh
probe.sh HOST PATH [CERT-URI] [KEY-URI] [PIN-SOURCE]
```

- `HOST` and `PATH` name the request, for example `example.test /auth/`.
- `CERT-URI` and `KEY-URI` are PKCS#11 URIs, by default `pkcs11:type=cert`
  and `pkcs11:type=private`, which select the only credential on the
  token.
- `PIN-SOURCE` is appended to both URIs when given, for example
  `pin-source=file:/run/user/1000/pin`. The PIN stays in that file; the
  command line, the URI and the output carry only its path. Omit it to
  answer the provider's prompt on the terminal.

The script prints the HTTP status, the page title and the first few text
fragments. It never prints the PIN, the certificate subject or the
response body, so its output is safe to paste into an issue.

## Provider configuration

The probe uses whatever `OPENSSL_CONF` names. A configuration that loads
the provider against a module in the usual FreeBSD location:

```ini
openssl_conf = init

[init]
providers = providers

[providers]
default = default_sect
pkcs11 = pkcs11_sect

[default_sect]
activate = 1

[pkcs11_sect]
module = /usr/local/lib/ossl-modules/pkcs11.so
pkcs11-module-path = /usr/local/lib/refineid/librefineid_pkcs11.so
activate = 1
```

## What the TLS details are for

- TLS 1.2 is requested because a server that authenticates by
  renegotiation after the first request can only do so there.
- `-alpn http/1.1` keeps the response parseable.
- `-ign_eof` is required. With a request on standard input and no
  `-ign_eof`, `s_client` half-closes the connection as soon as the input
  ends, and the client authentication never completes.
- Servers commonly redirect `/auth` to `/auth/`; pass the final path, or
  run the probe twice with the redirect target.

## FreeBSD

- `pcscd` must run, or the module enumerates no slots. `service pcscd
  status`.
- `usbconfig` needs root; as a user it answers "No device match or lack
  of permissions" for every device.
- There is no `/dev/ccid*`; libccid drives the reader as a `ugen` device
  through libusb.
- `pcsc_scan -r` is the reader check.
