#!/usr/bin/env python3
"""Cross-platform installer registering RefineID as the `eu.webeid` native host.

Installs host.py, a platform launcher (host.cmd on Windows, host.sh on POSIX),
config.json pointing at the RefineID PKCS#11 module, and native messaging
manifests allowing the official store-signed Web eID browser extensions in
Microsoft Edge, Google Chrome and Mozilla Firefox.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

HOST_NAME = "eu.webeid"
HOST_DESCRIPTION = "RefineID Web eID native application"
DEFAULT_MODEL = "ESTeID"
IS_WINDOWS = sys.platform == "win32"

# Official store-signed Web eID extension identifiers (RIA).
EDGE_EXTENSION_ID = "gnmckgbandlkacikdndelhfghdejfido"
CHROME_EXTENSION_ID = "ncibgoaomkmdpilpocfeponihegamlic"
FIREFOX_EXTENSION_ID = "{e68418bc-f2b0-4459-a9ea-3e72b6751b07}"

CHROMIUM_ALLOWED_ORIGINS = [
    f"chrome-extension://{EDGE_EXTENSION_ID}/",
    f"chrome-extension://{CHROME_EXTENSION_ID}/",
]

WINDOWS_REG_EDGE = rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"
WINDOWS_REG_CHROME = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
WINDOWS_REG_FIREFOX = rf"Software\Mozilla\NativeMessagingHosts\{HOST_NAME}"


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


def write_native_manifests(dest: Path, launcher: Path) -> tuple[Path, Path]:
    firefox_manifest: dict[str, Any] = {
        "name": HOST_NAME,
        "description": HOST_DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [FIREFOX_EXTENSION_ID],
    }
    chromium_manifest: dict[str, Any] = {
        "name": HOST_NAME,
        "description": HOST_DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": list(CHROMIUM_ALLOWED_ORIGINS),
    }

    firefox_path = dest / f"{HOST_NAME}.firefox.json"
    chromium_path = dest / f"{HOST_NAME}.chromium.json"
    firefox_path.write_text(
        json.dumps(firefox_manifest, indent=2) + "\n", encoding="utf-8"
    )
    chromium_path.write_text(
        json.dumps(chromium_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return firefox_path, chromium_path


def register_native_hosts(firefox_manifest: Path, chromium_manifest: Path) -> list[str]:
    registered: list[str] = []
    if IS_WINDOWS:
        import winreg

        for subkey, manifest_path in (
            (WINDOWS_REG_EDGE, chromium_manifest),
            (WINDOWS_REG_CHROME, chromium_manifest),
            (WINDOWS_REG_FIREFOX, firefox_manifest),
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
                chromium_manifest,
            ),
            (
                Path.home()
                / "Library"
                / "Application Support"
                / "Google"
                / "Chrome"
                / "NativeMessagingHosts",
                chromium_manifest,
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
                chromium_manifest,
            ),
            (
                Path.home()
                / ".config"
                / "google-chrome"
                / "NativeMessagingHosts",
                chromium_manifest,
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
        json.dumps({"pkcs11_module": str(pkcs11_module), "model": model}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    launcher = write_launcher(dest, python_executable, windows=windows)
    firefox_manifest, chromium_manifest = write_native_manifests(dest, launcher)
    registered = (
        register_native_hosts(firefox_manifest, chromium_manifest) if register else []
    )
    return {
        "destination": str(dest),
        "launcher": str(launcher),
        "config": str(config_path),
        "pkcs11_module": str(pkcs11_module),
        "firefox_manifest": str(firefox_manifest),
        "chromium_manifest": str(chromium_manifest),
        "registered": registered,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install RefineID as the eu.webeid native messaging host."
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
    for entry in summary["registered"]:
        print(f"Registered:        {entry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
