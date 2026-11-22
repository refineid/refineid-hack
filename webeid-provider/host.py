#!/usr/bin/env python3
"""Native messaging host answering Web eID authentication requests.

The content script sends one JSON message per request. The host builds the
Web eID authentication token: SHA-384(origin) concatenated with
SHA-384(challenge), signed by the card's authentication key through the
RefineID PKCS#11 module. The PIN stays on the machine; the host never prints
it or the challenge.
"""

import base64
import hashlib
import json
import os
import struct
import subprocess
import sys

EC_BYTES = 48
ALGORITHM = "ES384"
FORMAT = "web-eid:1.0"
APP_VERSION = "refineid/1.0.0"
EXTENSION_VERSION = "1.0.0"
ERROR_CODE = "ERR_WEBEID_NATIVE_FATAL"
MAX_MESSAGE_BYTES = 1 << 20

OPENSSL_CONF = os.environ.get(
    "REFINEID_OPENSSL_CONF",
    os.path.join(os.path.expanduser("~"), "refineid-openssl.cnf"),
)
PIN_FILE = os.environ.get(
    "REFINEID_WEBEID_PIN_FILE", "/tmp/temp-pin1.txt"
)
MODEL = os.environ.get("REFINEID_WEBEID_MODEL", "ESTeID")
ALLOWED_ORIGINS = frozenset({"https://tara.ria.ee"})


class HostError(Exception):
    """A request the host refuses to sign."""


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return None
    (length,) = struct.unpack("=I", header)
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise HostError("message length is out of range")
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload):
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(body)) + body)
    sys.stdout.buffer.flush()


def openssl(args, stdin=None):
    env = dict(os.environ)
    env["OPENSSL_CONF"] = OPENSSL_CONF
    return subprocess.run(
        args,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def der_length(data, position):
    first = data[position]
    if first < 0x80:
        return first, position + 1
    count = first & 0x7F
    return (
        int.from_bytes(data[position + 1 : position + 1 + count], "big"),
        position + 1 + count,
    )


def der_to_raw(der):
    """Convert a DER ECDSA signature to the fixed-width r||s form."""
    if not der or der[0] != 0x30:
        raise HostError("signature is not a DER sequence")
    length, position = der_length(der, 1)
    end = position + length
    parts = []
    while position < end:
        if der[position] != 0x02:
            raise HostError("signature component is not an integer")
        size, position = der_length(der, position + 1)
        value = der[position : position + size]
        position += size
        parts.append(value.lstrip(b"\x00").rjust(EC_BYTES, b"\x00"))
    if len(parts) != 2:
        raise HostError("signature does not have two components")
    return b"".join(parts)


def certificate_der():
    result = openssl(
        [
            "openssl",
            "x509",
            "-in",
            "pkcs11:model={0};type=cert".format(MODEL),
            "-outform",
            "DER",
        ]
    )
    if result.returncode != 0 or not result.stdout:
        raise HostError("authentication certificate is unavailable")
    return result.stdout


def pin_source():
    """Return the PIN file only when no one else can read it."""
    try:
        info = os.stat(PIN_FILE)
    except OSError:
        raise HostError("PIN file is unavailable")
    if not os.path.isfile(PIN_FILE):
        raise HostError("PIN file is unavailable")
    if info.st_mode & 0o077:
        raise HostError("PIN file permissions are too open")
    return PIN_FILE


def sign_digest(digest):
    uri = "pkcs11:model={0};type=private;pin-source=file:{1}".format(
        MODEL, pin_source()
    )
    result = openssl(["openssl", "dgst", "-sha384", "-sign", uri], stdin=digest)
    if result.returncode != 0 or not result.stdout:
        raise HostError("authentication signature failed")
    return der_to_raw(result.stdout)


def authenticate(message):
    origin = message.get("origin")
    challenge = message.get("challengeNonce")
    if not isinstance(origin, str) or origin not in ALLOWED_ORIGINS:
        raise HostError("origin is not allowed")
    if not isinstance(challenge, str) or len(challenge) < 32:
        raise HostError("challenge nonce is missing")
    digest = hashlib.sha384(origin.encode("utf-8")).digest()
    digest += hashlib.sha384(challenge.encode("utf-8")).digest()
    signature = sign_digest(digest)
    return {
        "unverifiedCertificate": base64.b64encode(certificate_der()).decode(
            "ascii"
        ),
        "algorithm": ALGORITHM,
        "signature": base64.b64encode(signature).decode("ascii"),
        "format": FORMAT,
        "appVersion": APP_VERSION,
    }


def status():
    return {"extension": EXTENSION_VERSION, "nativeApp": APP_VERSION}


def main():
    while True:
        message = read_message()
        if message is None:
            return 0
        try:
            action = message.get("action")
            if action == "authenticate":
                reply = authenticate(message)
            elif action == "status":
                reply = status()
            else:
                raise HostError("unknown action")
        except HostError as error:
            reply = {"error": {"code": ERROR_CODE, "message": str(error)}}
        write_message(reply)


if __name__ == "__main__":
    sys.exit(main())
