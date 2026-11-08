# refineid-hack
POSIX shell utilities for RefineID experiments. Everything here is a
hack: written to try something out, kept because it was useful once, and
not maintained to the standard of the RefineID applications. Read
`SECURITY.md` before taking anything here seriously.

`aosp-keychain-external-key/` is the parked AOSP KeyChain patch series moved
from refineid-android; its own README says what it is and what it needs.

`konqueror-bridge/` is a local proxy that does TLS client authentication with a
card for Konqueror on FreeBSD; see its README.

`fetch_suomi_occupation.sh` implements a self-contained suomi.fi login chain over an
SSHed host and fetches `value.profession` from the authenticated personal-data API.

## Rules of this repository

This is where the shortcuts go that the RefineID product repositories forbid: a
PIN taken from the command line or the environment, TLS without certificate
verification, and whatever else makes an experiment work on one machine on one
afternoon. The rules of those repositories (`refineid-core`, `refineid-unix`,
`refineid-windows`, `refineid-android` and `refineid-apple`) do not apply to the
code here, and code that breaks one of them is not a defect for that reason
alone.

One rule about secrets holds anyway: **nothing here may actively leak a PIN or
PUK, by any route.** That includes printing or logging one, sending it to
another machine or service, writing it to a file or to shell history, and
committing it. The only thing code here may hand a PIN or PUK to is the local
program that talks to the card.

Taking a PIN or PUK from the command line or the environment is accepted, at
its known cost: other processes on the same machine can read it while the
program runs.

How commits are made is not relaxed; the hooks in `githooks/` apply here as
everywhere.

## What it uses

- `openssl` for all HTTPS traffic and client TLS auth
- POSIX utilities only for parsing (`awk`, `sed`, `grep`, `tr`, `cut`, `wc`, `mktemp`, `date`)
- `lsusb` / `pcsc_scan` are optional for visibility only

## PKCS#11 and certificates

- Default mode is PEM-based client auth:
  - `OPENSSL_CLIENT_CERT` and `OPENSSL_CLIENT_KEY` must point to PEM files.
- Optional PKCS#11 engine mode:
  - set `ALLOW_PKCS11=1` and provide `PKCS11_CERT_URI` / `PKCS11_KEY_URI`,
    or keep defaults and let `pkcs11-tool` auto-discover cert/key objects.
- If the local OpenSSL build cannot load PKCS#11 URIs, the script exits with an explicit message
  and suggests using PEM mode instead.
- Raw APDU mode is implemented via `usb_ioctl_helper`:
  - `USB_RAW_APDU=1` requires helper support and visibility of the reader node.
  - helper path/operation is passed via `USB_IOCTL_ARGS`.

## Usage

```sh
./fetch_suomi_occupation.sh <PIN1> [lang=en|fi|sv] [ssh_host=kali]
```

Examples:

```sh
REFINEID_PIN1=1234 ./fetch_suomi_occupation.sh
./fetch_suomi_occupation.sh 1234 fi kali
```

### Example (POSIX shell only)

```sh
./fetch_suomi_occupation.sh 456789 fi kali
ALLOW_PKCS11=1 ./fetch_suomi_occupation.sh 456789 fi kali
```

On Kali with reader+card present, card metadata and object URIs are printed from
`/dev/bus/usb` and `pcsc_scan` before running the auth chain.

## Minimal ioctl helper (Linux /dev/bus/usb)

Build helper:

```sh
cc -O2 -Wall -Wextra -o usb_ioctl_helper usb_ioctl_helper.c
```

Build this on Kali/Linux (the helper uses `<linux/usbdevice_fs.h>` and USBFS ioctls).

Example from shell:

```sh
# send a USB control IN request
./usb_ioctl_helper --path /dev/bus/usb/002/005 --interface 0 \
  control-in 0xA1 0x00 0x0000 0x0000 18

# send one C-APDU-like payload and read a reply from bulk endpoints
./usb_ioctl_helper --path /dev/bus/usb/002/005 --interface 0 \
  bulk-xchange 0x01 0x81 A00000024720 256
```

This helper performs raw USBFS `ioctl()` calls (`USBDEVFS_CONTROL`, `USBDEVFS_BULK`,
`USBDEVFS_CLAIMINTERFACE`, `USBDEVFS_RELEASEINTERFACE`) and returns response bytes as hex.

Use from the script:

```sh
USB_IOCTL_HELPER=./usb_ioctl_helper \
USB_IOCTL_ARGS='--interface 0 control-in 0xA1 0x00 0x0000 0x0000 18' \
USB_RAW_APDU=1 \
./fetch_suomi_occupation.sh 456789 fi local
```

In this mode, the script calls the helper at startup to verify USB access before continuing
with authentication flow.
