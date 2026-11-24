#!/usr/bin/env python3
"""Cross-platform installer for the RefineID Web eID provider.

Installs host.py, a platform launcher (host.cmd on Windows, host.sh on
POSIX), config.json pointing at the RefineID PKCS#11 module, ready-to-load
unpacked extensions for Firefox and Microsoft Edge, and registers the
native messaging manifests with Firefox and Edge.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

HOST_NAME = "refineid_webeid"
HOST_DESCRIPTION = "RefineID Web eID authentication host"
FIREFOX_EXTENSION_ID = "refineid-webeid@refineid.fi"
DEFAULT_MODEL = "ESTeID"
IS_WINDOWS = sys.platform == "win32"

# Standard NIST P-256 (FIPS 186-4) generator point G, encoded as a public
# SubjectPublicKeyInfo DER solely so Chromium/Edge computes a deterministic
# local extension ID for the unpacked extension folder. Never used as a
# cryptographic key.
ASN1_SEQUENCE = 0x30
ASN1_OID = 0x06
ASN1_BIT_STRING = 0x03
BIT_STRING_NO_UNUSED_BITS = 0x00
EC_UNCOMPRESSED_POINT_PREFIX = 0x04

OID_EC_PUBLIC_KEY = bytes([0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01])
OID_PRIME256V1 = bytes([0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07])
P256_GENERATOR_X = bytes.fromhex(
    "6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296"
)
P256_GENERATOR_Y = bytes.fromhex(
    "4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5"
)

CHROMIUM_ID_HASH_BYTES = 16
CHROMIUM_ID_ALPHABET_BASE = ord("a")
NIBBLE_MASK = 0x0F
HIGH_NIBBLE_SHIFT = 4

WINDOWS_REG_FIREFOX = rf"Software\Mozilla\NativeMessagingHosts\{HOST_NAME}"
WINDOWS_REG_EDGE = rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"


def _der_tlv(tag: int, body: bytes) -> bytes:
    if len(body) >= 0x80:
        raise ValueError("DER short-form length exceeded")
    return bytes([tag, len(body)]) + body


def build_edge_public_key_der() -> bytes:
    """Build a deterministic public-only SPKI DER for Edge's manifest 'key'."""
    alg_id = _der_tlv(
        ASN1_SEQUENCE,
        _der_tlv(ASN1_OID, OID_EC_PUBLIC_KEY) + _der_tlv(ASN1_OID, OID_PRIME256V1),
    )
    point = bytes([BIT_STRING_NO_UNUSED_BITS, EC_UNCOMPRESSED_POINT_PREFIX]) + (
        P256_GENERATOR_X + P256_GENERATOR_Y
    )
    return _der_tlv(ASN1_SEQUENCE, alg_id + _der_tlv(ASN1_BIT_STRING, point))


def chromium_extension_id(public_key_der: bytes) -> str:
    """Compute Chromium/Edge's 32-letter extension ID from a manifest key."""
    digest = hashlib.sha256(public_key_der).digest()[:CHROMIUM_ID_HASH_BYTES]
    chars: list[str] = []
    for byte in digest:
        chars.append(chr(CHROMIUM_ID_ALPHABET_BASE + (byte >> HIGH_NIBBLE_SHIFT)))
        chars.append(chr(CHROMIUM_ID_ALPHABET_BASE + (byte & NIBBLE_MASK)))
    return "".join(chars)


def default_destination() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "refineid-webeid"
    return Path.home() / "refineid-webeid"


def candidate_pkcs11_paths() -> list[Path]:
    home = Path.home()
    if IS_WINDOWS:
        return [
            home / "src" / "refineid-windows" / "target" / "release" / "refineid_pkcs11.dll",
            home / "src" / "refineid-windows" / "target" / "debug" / "refineid_pkcs11.dll",
            Path(r"C:\Program Files\RefineID\refineid_pkcs11.dll"),
        ]
    if sys.platform == "darwin":
        return [
            home / "src" / "refineid-core" / "target" / "release" / "librefineid_pkcs11.dylib",
            Path("/usr/local/lib/librefineid_pkcs11.dylib"),
        ]
    return [
        home / "src" / "refineid-core" / "target" / "release" / "librefineid_pkcs11.so",
        home / "src" / "refineid-unix" / "target" / "release" / "librefineid_pkcs11.so",
        Path("/usr/local/lib/librefineid_pkcs11.so"),
        Path("/usr/lib/librefineid_pkcs11.so"),
    ]


def resolve_pkcs11_module(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"PKCS#11 module not found: {path}")
        return path
    for candidate in candidate_pkcs11_paths():
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "RefineID PKCS#11 module not found; pass --pkcs11 <path-to-module>"
    )


def write_launcher(dest: Path, python_executable: str, windows: bool) -> Path:
    if windows:
        launcher = dest / "host.cmd"
        encoding = "mbcs" if IS_WINDOWS else "utf-8"
        launcher.write_text(
            f'@echo off\r\n"{python_executable}" -B "%~dp0host.py"\r\n',
            encoding=encoding,
            newline="",
        )
        return launcher
    launcher = dest / "host.sh"
    launcher.write_text(
        f'#!/bin/sh\nset -eu\nexec "{python_executable}" -B "$(dirname "$0")/host.py"\n',
        encoding="utf-8",
        newline="",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def install_extensions(source_dir: Path, dest: Path, edge_key_b64: str) -> tuple[Path, Path]:
    ext_src = source_dir / "ext"
    shared_files = ("background.js", "content.js")

    firefox_dir = dest / "ext-firefox"
    edge_dir = dest / "ext-edge"
    for target in (firefox_dir, edge_dir):
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for name in shared_files:
            shutil.copy2(ext_src / name, target / name)

    shutil.copy2(ext_src / "manifest.firefox.json", firefox_dir / "manifest.json")

    edge_manifest = json.loads(
        (ext_src / "manifest.edge.json").read_text(encoding="utf-8")
    )
    edge_manifest["key"] = edge_key_b64
    (edge_dir / "manifest.json").write_text(
        json.dumps(edge_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return firefox_dir, edge_dir


def write_native_manifests(
    dest: Path, launcher: Path, edge_extension_id: str
) -> tuple[Path, Path]:
    firefox_manifest: dict[str, Any] = {
        "name": HOST_NAME,
        "description": HOST_DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [FIREFOX_EXTENSION_ID],
    }
    edge_manifest: dict[str, Any] = {
        "name": HOST_NAME,
        "description": HOST_DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{edge_extension_id}/"],
    }

    firefox_path = dest / f"{HOST_NAME}_firefox.json"
    edge_path = dest / f"{HOST_NAME}_edge.json"
    firefox_path.write_text(
        json.dumps(firefox_manifest, indent=2) + "\n", encoding="utf-8"
    )
    edge_path.write_text(json.dumps(edge_manifest, indent=2) + "\n", encoding="utf-8")
    return firefox_path, edge_path


def register_native_hosts(firefox_manifest: Path, edge_manifest: Path) -> list[str]:
    registered: list[str] = []
    if IS_WINDOWS:
        import winreg

        for subkey, manifest_path in (
            (WINDOWS_REG_FIREFOX, firefox_manifest),
            (WINDOWS_REG_EDGE, edge_manifest),
        ):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
            registered.append(rf"HKCU\{subkey}")
        return registered

    if sys.platform == "darwin":
        targets = (
            (
                Path.home()
                / "Library"
                / "Application Support"
                / "Mozilla"
                / "NativeMessagingHosts",
                firefox_manifest,
            ),
            (
                Path.home()
                / "Library"
                / "Application Support"
                / "Microsoft Edge"
                / "NativeMessagingHosts",
                edge_manifest,
            ),
        )
    else:
        targets = (
            (Path.home() / ".mozilla" / "native-messaging-hosts", firefox_manifest),
            (
                Path.home()
                / ".config"
                / "microsoft-edge"
                / "NativeMessagingHosts",
                edge_manifest,
            ),
        )
    for host_dir, manifest_path in targets:
        host_dir.mkdir(parents=True, exist_ok=True)
        target = host_dir / f"{HOST_NAME}.json"
        shutil.copy2(manifest_path, target)
        registered.append(str(target))
    return registered


def install(
    *,
    source_dir: Path,
    dest: Path,
    pkcs11_module: Path,
    model: str,
    register: bool,
    python_executable: str = sys.executable,
    windows: bool = IS_WINDOWS,
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    host_dest = dest / "host.py"
    shutil.copy2(source_dir / "host.py", host_dest)
    if not windows:
        host_dest.chmod(
            host_dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    config_path = dest / "config.json"
    config_path.write_text(
        json.dumps(
            {"pkcs11_module": str(pkcs11_module), "model": model}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )

    launcher = write_launcher(dest, python_executable, windows=windows)
    edge_key_der = build_edge_public_key_der()
    edge_key_b64 = base64.b64encode(edge_key_der).decode("ascii")
    edge_id = chromium_extension_id(edge_key_der)

    firefox_ext_dir, edge_ext_dir = install_extensions(source_dir, dest, edge_key_b64)
    firefox_manifest, edge_manifest = write_native_manifests(dest, launcher, edge_id)
    registered = (
        register_native_hosts(firefox_manifest, edge_manifest) if register else []
    )
    return {
        "destination": str(dest),
        "launcher": str(launcher),
        "config": str(config_path),
        "pkcs11_module": str(pkcs11_module),
        "firefox_extension": str(firefox_ext_dir),
        "edge_extension": str(edge_ext_dir),
        "edge_extension_id": edge_id,
        "firefox_manifest": str(firefox_manifest),
        "edge_manifest": str(edge_manifest),
        "registered": registered,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the RefineID Web eID native host and browser extensions."
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=default_destination(),
        help="Installation directory.",
    )
    parser.add_argument(
        "--pkcs11",
        default=None,
        help="Path to the RefineID PKCS#11 shared library.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="PKCS#11 token model to select (default: ESTeID).",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Skip registering native messaging manifests with browsers.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir = Path(__file__).resolve().parent
    pkcs11_module = resolve_pkcs11_module(args.pkcs11)
    summary = install(
        source_dir=source_dir,
        dest=args.dest.expanduser().resolve(),
        pkcs11_module=pkcs11_module,
        model=args.model,
        register=not args.no_registry,
    )
    print(f"Host installed in: {summary['destination']}")
    print(f"PKCS#11 module:    {summary['pkcs11_module']}")
    print(f"Edge extension:    {summary['edge_extension']} (id: {summary['edge_extension_id']})")
    print(f"Firefox extension: {summary['firefox_extension']}")
    for entry in summary["registered"]:
        print(f"Registered:        {entry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
