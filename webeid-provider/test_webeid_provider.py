#!/usr/bin/env python3
"""Hermetic unit tests for the eu.webeid native host and installer."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import host  # noqa: E402
import install  # noqa: E402

SYNTHETIC_CHALLENGE = "A" * 44
SYNTHETIC_CERT_DER = b"\x30\x82\x01\x00" + (b"\x11" * 16)
SYNTHETIC_SIGNATURE = bytes(range(host.EC_P384_SIG_BYTES))
SYNTHETIC_PIN = b"1234"


class FakePkcs11Provider:
    def __init__(
        self,
        *,
        cert_der: bytes = SYNTHETIC_CERT_DER,
        signature: bytes = SYNTHETIC_SIGNATURE,
        flags: int = 0,
        error: host.HostError | None = None,
    ) -> None:
        self.cert_der = cert_der
        self.signature = signature
        self.flags = flags
        self.error = error
        self.calls: list[tuple[str, str, bytes]] = []

    def sign_authentication(
        self,
        model: str,
        origin: str,
        tbs_hash: bytes,
        pin_getter,
    ) -> tuple[bytes, bytes]:
        self.calls.append((model, origin, tbs_hash))
        if self.error is not None:
            raise self.error
        pin = pin_getter(origin, self.flags)
        host.zero_bytearray(pin)
        return self.cert_der, self.signature


class FakeCngProvider:
    def __init__(
        self,
        *,
        cert_der: bytes = SYNTHETIC_CERT_DER,
        algorithm: str = host.ALGORITHM_ES384,
        signature: bytes = SYNTHETIC_SIGNATURE,
        error: host.HostError | None = None,
    ) -> None:
        self.cert_der = cert_der
        self.algorithm = algorithm
        self.signature = signature
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def authenticate(self, origin: str, challenge: str) -> tuple[bytes, str, bytes]:
        self.calls.append((origin, challenge))
        if self.error is not None:
            raise self.error
        return self.cert_der, self.algorithm, self.signature


class HostFramingTests(unittest.TestCase):
    def test_round_trip_message(self) -> None:
        buf = io.BytesIO()
        host.write_message({"command": "quit", "arguments": {}}, stream=buf)
        buf.seek(0)
        self.assertEqual(
            host.read_message(stream=buf), {"command": "quit", "arguments": {}}
        )

    def test_eof_returns_none(self) -> None:
        self.assertIsNone(host.read_message(stream=io.BytesIO(b"")))

    def test_oversized_length_rejected(self) -> None:
        raw = struct.pack("=I", host.MAX_MESSAGE_BYTES + 1)
        with self.assertRaises(host.HostError):
            host.read_message(stream=io.BytesIO(raw))

    def test_zero_length_rejected(self) -> None:
        raw = struct.pack("=I", 0)
        with self.assertRaises(host.HostError):
            host.read_message(stream=io.BytesIO(raw))


class HostProtocolTests(unittest.TestCase):
    def test_quit_command_replies_empty_object_and_exits(self) -> None:
        reply, should_exit = host.handle_command({"command": "quit", "arguments": {}})
        self.assertEqual(reply, {})
        self.assertTrue(should_exit)

    def test_unknown_command_returns_invalid_argument(self) -> None:
        reply, should_exit = host.handle_command({"command": "sign", "arguments": {}})
        self.assertFalse(should_exit)
        self.assertEqual(reply["error"]["code"], host.ERROR_CODE_INVALID_ARGUMENT)

    def test_rejects_non_https_and_non_canonical_origins(self) -> None:
        for bad_origin in (
            "http://tara.ria.ee",
            "https://tara.ria.ee/path",
            "https://tara.ria.ee?q=1",
            "https://user@tara.ria.ee",
            "https://tara.ria.ee/",
            "https://tara.ria.ee\n",
            "",
        ):
            with self.assertRaises(host.HostError) as ctx:
                host.validate_https_origin(bad_origin)
            self.assertEqual(ctx.exception.code, host.ERROR_CODE_INVALID_ARGUMENT)

    def test_accepts_canonical_https_origin_and_port(self) -> None:
        self.assertEqual(
            host.validate_https_origin("https://tara.ria.ee"),
            "https://tara.ria.ee",
        )
        self.assertEqual(
            host.validate_https_origin("https://example.ee:8443"),
            "https://example.ee:8443",
        )

    def test_select_web_eid_algorithm_handles_ecdh_ecdsa_and_rsa(self) -> None:
        self.assertEqual(
            host.select_web_eid_algorithm("ECDH_P384", 384),
            ("ES384", "sha384", 96),
        )
        self.assertEqual(
            host.select_web_eid_algorithm("ECDSA_P384", 384),
            ("ES384", "sha384", 96),
        )
        self.assertEqual(
            host.select_web_eid_algorithm("ECDSA_P256", 256),
            ("ES256", "sha256", 64),
        )
        self.assertEqual(
            host.select_web_eid_algorithm("RSA", 3072),
            ("RS256", "sha256", None),
        )
        with self.assertRaises(host.HostError):
            host.select_web_eid_algorithm("DSA", 1024)

    def test_classify_windows_cng_status_maps_cancel_and_lock(self) -> None:
        for cancelled in (
            host.WIN_NTE_USER_CANCELLED,
            host.WIN_SCARD_W_CANCELLED_BY_USER,
            host.WIN_HRESULT_ERROR_CANCELLED,
            host.WIN_NTE_USER_CANCELLED - (1 << 32),
        ):
            err = host.classify_windows_cng_status(cancelled)
            self.assertEqual(err.code, host.ERROR_CODE_USER_CANCELLED)
        locked = host.classify_windows_cng_status(host.WIN_SCARD_W_CHV_BLOCKED)
        self.assertEqual(locked.code, host.ERROR_CODE_FATAL)
        self.assertIn("locked", str(locked))

    def test_windows_cng_path_returns_web_eid_token(self) -> None:
        fake = FakeCngProvider()
        reply, should_exit = host.handle_command(
            {
                "command": "authenticate",
                "arguments": {
                    "origin": "https://tara.ria.ee",
                    "challengeNonce": SYNTHETIC_CHALLENGE,
                },
            },
            cng_factory=lambda: fake,
            config={},
            use_windows_cng=True,
        )
        self.assertFalse(should_exit)
        self.assertNotIn("error", reply)
        self.assertEqual(reply["algorithm"], "ES384")
        self.assertEqual(reply["format"], "web-eid:1.0")
        self.assertEqual(
            base64.b64decode(reply["unverifiedCertificate"]), SYNTHETIC_CERT_DER
        )
        self.assertEqual(base64.b64decode(reply["signature"]), SYNTHETIC_SIGNATURE)
        self.assertEqual(fake.calls, [("https://tara.ria.ee", SYNTHETIC_CHALLENGE)])

    def test_windows_cng_cancel_propagates_user_cancelled(self) -> None:
        fake = FakeCngProvider(
            error=host.HostError(
                "PIN entry cancelled", code=host.ERROR_CODE_USER_CANCELLED
            )
        )
        reply, _ = host.handle_command(
            {
                "command": "authenticate",
                "arguments": {
                    "origin": "https://tara.ria.ee",
                    "challengeNonce": SYNTHETIC_CHALLENGE,
                },
            },
            cng_factory=lambda: fake,
            config={},
            use_windows_cng=True,
        )
        self.assertEqual(reply["error"]["code"], host.ERROR_CODE_USER_CANCELLED)

    def test_posix_pkcs11_path_produces_expected_web_eid_token_and_digest(self) -> None:
        fake = FakePkcs11Provider()
        captured_pin_buf: list[bytearray] = []

        def fake_pin_getter(_origin: str, _flags: int) -> bytearray:
            buf = bytearray(SYNTHETIC_PIN)
            captured_pin_buf.append(buf)
            return buf

        reply, should_exit = host.handle_command(
            {
                "command": "authenticate",
                "arguments": {
                    "origin": "https://tara.ria.ee",
                    "challengeNonce": SYNTHETIC_CHALLENGE,
                    "lang": "en",
                },
            },
            pkcs11_factory=lambda _path: fake,
            pin_getter=fake_pin_getter,
            config={"pkcs11_module": "/fake/pkcs11.so", "model": "ESTeID"},
            use_windows_cng=False,
        )

        self.assertFalse(should_exit)
        self.assertNotIn("error", reply)
        self.assertEqual(reply["algorithm"], "ES384")
        self.assertEqual(reply["format"], "web-eid:1.0")
        self.assertEqual(reply["appVersion"], host.APP_VERSION)
        self.assertEqual(
            base64.b64decode(reply["unverifiedCertificate"]), SYNTHETIC_CERT_DER
        )
        self.assertEqual(base64.b64decode(reply["signature"]), SYNTHETIC_SIGNATURE)

        expected_payload = (
            hashlib.sha384(b"https://tara.ria.ee").digest()
            + hashlib.sha384(SYNTHETIC_CHALLENGE.encode("utf-8")).digest()
        )
        expected_hash = hashlib.sha384(expected_payload).digest()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(
            fake.calls[0], ("ESTeID", "https://tara.ria.ee", expected_hash)
        )
        self.assertEqual(captured_pin_buf[0], bytearray(len(SYNTHETIC_PIN)))

    def test_optional_origin_allowlist_enforced(self) -> None:
        reply, _ = host.handle_command(
            {
                "command": "authenticate",
                "arguments": {
                    "origin": "https://other.example",
                    "challengeNonce": SYNTHETIC_CHALLENGE,
                },
            },
            config={"allowed_origins": ["https://tara.ria.ee"]},
            use_windows_cng=True,
        )
        self.assertEqual(reply["error"]["code"], host.ERROR_CODE_INVALID_ARGUMENT)

    def test_protected_path_skips_pin_prompt(self) -> None:
        self.assertIsNone(
            host.obtain_pin(
                "https://tara.ria.ee", host.CKF_PROTECTED_AUTHENTICATION_PATH
            )
        )

    @unittest.skipIf(host.IS_WINDOWS, "POSIX mode-600 test")
    def test_posix_pin_file_rejects_open_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pin_path = Path(tmp) / "pin.txt"
            pin_path.write_bytes(SYNTHETIC_PIN + b"\n")
            pin_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaises(host.HostError):
                host.read_pin_file_posix(str(pin_path))

            pin_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            pin = host.read_pin_file_posix(str(pin_path))
            try:
                self.assertEqual(bytes(pin), SYNTHETIC_PIN)
            finally:
                host.zero_bytearray(pin)

    def test_function_list_field_count_matches_pkcs11_v240(self) -> None:
        self.assertEqual(len(host.CkFunctionList._fields_), 69)
        if host.IS_WINDOWS:
            expected_size = 2 + 68 * ctypes.sizeof(ctypes.c_void_p)
            self.assertEqual(ctypes.sizeof(host.CkFunctionList), expected_size)


class InstallerTests(unittest.TestCase):
    def test_install_windows_cng_layout_requires_no_pkcs11(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"

            summary = install.install(
                source_dir=HERE,
                dest=dest,
                pkcs11_module=None,
                model="ESTeID",
                register=False,
                python_executable=r"C:\Python315\python.exe",
                windows=True,
            )

            self.assertEqual(summary["backend"], "windows-cng")
            self.assertIsNone(summary["pkcs11_module"])
            self.assertTrue((dest / "host.py").is_file())
            cmd_bytes = (dest / "host.cmd").read_bytes()
            self.assertNotIn(b"\r\r\n", cmd_bytes)
            self.assertIn(
                r'"C:\Python315\python.exe" -B "%~dp0host.py"',
                cmd_bytes.decode("ascii"),
            )

            config = json.loads((dest / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config, {"backend": "windows-cng"})

            chromium = json.loads(
                (dest / "eu.webeid.chromium.json").read_text(encoding="utf-8")
            )
            self.assertEqual(chromium["name"], "eu.webeid")
            self.assertEqual(chromium["path"], str(dest / "host.cmd"))
            self.assertIn(
                f"chrome-extension://{install.EDGE_EXTENSION_ID}/",
                chromium["allowed_origins"],
            )
            self.assertIn(
                f"chrome-extension://{install.CHROME_EXTENSION_ID}/",
                chromium["allowed_origins"],
            )

            firefox = json.loads(
                (dest / "eu.webeid.firefox.json").read_text(encoding="utf-8")
            )
            self.assertEqual(firefox["name"], "eu.webeid")
            self.assertEqual(
                firefox["allowed_extensions"], [install.FIREFOX_EXTENSION_ID]
            )

    def test_install_windows_layout_with_non_ascii_python_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out"
            py_path = r"C:\Users\Jürgen\Python315\python.exe"

            install.install(
                source_dir=HERE,
                dest=dest,
                pkcs11_module=None,
                model="ESTeID",
                register=False,
                python_executable=py_path,
                windows=True,
            )
            encoding = "mbcs" if install.IS_WINDOWS else "utf-8"
            self.assertIn(py_path, (dest / "host.cmd").read_text(encoding=encoding))

    def test_install_posix_layout_without_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_so = tmp_path / "librefineid_pkcs11.so"
            fake_so.write_bytes(b"\x7fELF")
            dest = tmp_path / "out"

            summary = install.install(
                source_dir=HERE,
                dest=dest,
                pkcs11_module=fake_so,
                model="ESTeID",
                register=False,
                python_executable="/usr/bin/python3",
                windows=False,
            )
            self.assertEqual(summary["backend"], "pkcs11")
            self.assertTrue((dest / "host.sh").is_file())
            self.assertTrue(os.access(dest / "host.sh", os.X_OK))
            self.assertTrue((dest / "eu.webeid.firefox.json").is_file())
            self.assertTrue((dest / "eu.webeid.chromium.json").is_file())


if __name__ == "__main__":
    unittest.main()
