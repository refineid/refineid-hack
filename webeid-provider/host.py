#!/usr/bin/env python3
"""Official Web eID (`eu.webeid`) native messaging host.

Implements the native messaging protocol spoken by the official store-signed
Web eID browser extension (`web-eid-webextension` 2.x) for Microsoft Edge,
Google Chrome and Mozilla Firefox:

1. Immediately writes `{"version": "2.5.0"}` on startup.
2. Handles `{"command": "quit", "arguments": {}}` by replying `{}` and exiting.
3. Handles `{"command": "authenticate", "arguments": {"challengeNonce", "origin"}}`:
   - On Windows, signs via Windows CNG (`Microsoft Smart Card Key Storage
     Provider` / `NCryptSignHash`), so Windows 11 renders the native Windows
     Security Smart Card PIN dialog and PIN1 never enters this process.
   - On POSIX (FreeBSD/Linux/macOS), talks directly to the RefineID PKCS#11
     v2.40 module via ctypes.

The host never logs or returns PIN data, cardholder data or challenges.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import stat
import struct
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

# ---- Web eID protocol constants ----

EC_P256_BITS = 256
EC_P384_BITS = 384
EC_P521_BITS = 521
EC_P256_SIG_BYTES = 64
EC_P384_SIG_BYTES = 96
EC_P521_SIG_BYTES = 132
SHA384_DIGEST_BYTES = 48
MIN_CHALLENGE_LENGTH = 32
ALGORITHM_ES256 = "ES256"
ALGORITHM_ES384 = "ES384"
ALGORITHM_ES512 = "ES512"
ALGORITHM_RS256 = "RS256"
TOKEN_FORMAT = "web-eid:1.0"
NATIVE_VERSION = "2.5.0"
APP_VERSION = f"https://refineid.fi/webeid/v{NATIVE_VERSION}"
ERROR_CODE_FATAL = "ERR_WEBEID_NATIVE_FATAL"
ERROR_CODE_INVALID_ARGUMENT = "ERR_WEBEID_NATIVE_INVALID_ARGUMENT"
ERROR_CODE_USER_CANCELLED = "ERR_WEBEID_USER_CANCELLED"
MAX_MESSAGE_BYTES = 8192
MESSAGE_HEADER_BYTES = 4
MESSAGE_HEADER_FORMAT = "=I"

DEFAULT_MODEL = "ESTeID"
DEFAULT_HEADLESS_ORIGINS = frozenset({"https://tara.ria.ee"})
CONFIG_FILENAME = "config.json"

ENV_PKCS11_MODULE = "REFINEID_WEBEID_PKCS11"
ENV_MODEL = "REFINEID_WEBEID_MODEL"
ENV_PIN_FILE = "REFINEID_WEBEID_PIN_FILE"
ENV_ALLOWED_ORIGINS = "REFINEID_WEBEID_ALLOWED_ORIGINS"
POSIX_GROUP_OTHER_MASK = 0o077
ASCII_LF = 0x0A
ASCII_CR = 0x0D
NEWLINE_BYTES = (ASCII_LF, ASCII_CR)
ASCII_PRINTABLE_MIN = 0x21
ASCII_PRINTABLE_MAX = 0x7E

IS_WINDOWS = sys.platform == "win32"

# ---- Windows CNG / CryptoAPI constants (wincrypt.h / ncrypt.h / winerror.h) ----

CERT_STORE_PROV_MEMORY = 2
CERT_STORE_PROV_SYSTEM_W = 10
CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
CERT_STORE_READONLY_FLAG = 0x00008000
CERT_STORE_ADD_USE_EXISTING = 2
CERT_KEY_PROV_INFO_PROP_ID = 2
CRYPT_ACQUIRE_SILENT_FLAG = 0x00000040
CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG = 0x00040000
NCRYPT_SILENT_FLAG = 0x00000040
BCRYPT_PAD_PKCS1 = 0x00000002
SMART_CARD_KSP = "Microsoft Smart Card Key Storage Provider"
OID_CLIENT_AUTH = b"1.3.6.1.5.5.7.3.2"

NCRYPT_PROP_ALGORITHM_NAME = "Algorithm Name"
NCRYPT_PROP_LENGTH = "Length"
NCRYPT_PROP_USE_CONTEXT = "Use Context"
NCRYPT_PROP_WINDOW_HANDLE = "HWND Handle"
BCRYPT_SHA256_ALGORITHM = "SHA256"

WIN_STATUS_MASK = 0xFFFFFFFF
WIN_ERROR_SUCCESS = 0x00000000
WIN_NTE_USER_CANCELLED = 0x80090036
WIN_SCARD_W_CANCELLED_BY_USER = 0x8010006E
WIN_HRESULT_ERROR_CANCELLED = 0x800704C7
WIN_SCARD_W_CHV_BLOCKED = 0x8010006C
WIN_SCARD_W_WRONG_CHV = 0x8010006B
WIN_SCARD_W_REMOVED_CARD = 0x80100069
WIN_SCARD_E_NO_SMARTCARD = 0x8010000C
WIN_SCARD_E_NO_READERS_AVAILABLE = 0x8010002E

WIN_CANCELLED_STATUSES = frozenset(
    {
        WIN_NTE_USER_CANCELLED,
        WIN_SCARD_W_CANCELLED_BY_USER,
        WIN_HRESULT_ERROR_CANCELLED,
    }
)

# ---- PKCS#11 v2.40 constants (OASIS pkcs11t.h, used on POSIX) ----

PKCS11_VERSION_MAJOR = 2
PKCS11_VERSION_MINOR = 40

CK_FALSE = 0
CK_TRUE = 1

CKR_OK = 0x00000000
CKR_PIN_INCORRECT = 0x000000A0
CKR_PIN_INVALID = 0x000000A1
CKR_PIN_LEN_RANGE = 0x000000A2
CKR_PIN_LOCKED = 0x000000A4
CKR_TOKEN_NOT_PRESENT = 0x000000E0
CKR_USER_ALREADY_LOGGED_IN = 0x00000100
CKR_CRYPTOKI_ALREADY_INITIALIZED = 0x00000191

CKU_USER = 0x00000001

CKF_PROTECTED_AUTHENTICATION_PATH = 0x00000100
CKF_USER_PIN_COUNT_LOW = 0x00010000
CKF_USER_PIN_FINAL_TRY = 0x00020000
CKF_USER_PIN_LOCKED = 0x00040000
CKF_SERIAL_SESSION = 0x00000004

CKO_CERTIFICATE = 0x00000001
CKO_PRIVATE_KEY = 0x00000003
CK_CERTIFICATE_CATEGORY_TOKEN_USER = 0x00000001
CKK_EC = 0x00000003

CKA_CLASS = 0x00000000
CKA_VALUE = 0x00000011
CKA_CERTIFICATE_CATEGORY = 0x00000087
CKA_KEY_TYPE = 0x00000100
CKA_ID = 0x00000102
CKA_SIGN = 0x00000108

CKM_ECDSA = 0x00001041

TOKEN_LABEL_LEN = 32
TOKEN_MANUFACTURER_LEN = 32
TOKEN_MODEL_LEN = 16
TOKEN_SERIAL_LEN = 16
TOKEN_UTC_TIME_LEN = 16

# ---- PKCS#11 v2.40 C ABI types ----

CkByte = ctypes.c_ubyte
CkBbool = ctypes.c_ubyte
CkUlong = ctypes.c_ulong
CkFlags = CkUlong
CkRv = CkUlong
CkSlotId = CkUlong
CkSessionHandle = CkUlong
CkObjectHandle = CkUlong
CkUserType = CkUlong
CkAttributeType = CkUlong
CkMechanismType = CkUlong
CkVoidPtr = ctypes.c_void_p


class _PackedStructure(ctypes.Structure):
    """Base for PKCS#11 structures: 1-byte packed on Windows, natural on Unix."""

    if IS_WINDOWS:
        _pack_ = 1


class CkVersion(_PackedStructure):
    _fields_ = [
        ("major", CkByte),
        ("minor", CkByte),
    ]


class CkTokenInfo(_PackedStructure):
    _fields_ = [
        ("label", CkByte * TOKEN_LABEL_LEN),
        ("manufacturerID", CkByte * TOKEN_MANUFACTURER_LEN),
        ("model", CkByte * TOKEN_MODEL_LEN),
        ("serialNumber", CkByte * TOKEN_SERIAL_LEN),
        ("flags", CkFlags),
        ("ulMaxSessionCount", CkUlong),
        ("ulSessionCount", CkUlong),
        ("ulMaxRwSessionCount", CkUlong),
        ("ulRwSessionCount", CkUlong),
        ("ulMaxPinLen", CkUlong),
        ("ulMinPinLen", CkUlong),
        ("ulTotalPublicMemory", CkUlong),
        ("ulFreePublicMemory", CkUlong),
        ("ulTotalPrivateMemory", CkUlong),
        ("ulFreePrivateMemory", CkUlong),
        ("hardwareVersion", CkVersion),
        ("firmwareVersion", CkVersion),
        ("utcTime", CkByte * TOKEN_UTC_TIME_LEN),
    ]


class CkAttribute(_PackedStructure):
    _fields_ = [
        ("type", CkAttributeType),
        ("pValue", CkVoidPtr),
        ("ulValueLen", CkUlong),
    ]


class CkMechanism(_PackedStructure):
    _fields_ = [
        ("mechanism", CkMechanismType),
        ("pParameter", CkVoidPtr),
        ("ulParameterLen", CkUlong),
    ]


_FnInitialize = ctypes.CFUNCTYPE(CkRv, CkVoidPtr)
_FnFinalize = ctypes.CFUNCTYPE(CkRv, CkVoidPtr)
_FnGetSlotList = ctypes.CFUNCTYPE(
    CkRv, CkBbool, ctypes.POINTER(CkSlotId), ctypes.POINTER(CkUlong)
)
_FnGetTokenInfo = ctypes.CFUNCTYPE(CkRv, CkSlotId, ctypes.POINTER(CkTokenInfo))
_FnOpenSession = ctypes.CFUNCTYPE(
    CkRv,
    CkSlotId,
    CkFlags,
    CkVoidPtr,
    CkVoidPtr,
    ctypes.POINTER(CkSessionHandle),
)
_FnCloseSession = ctypes.CFUNCTYPE(CkRv, CkSessionHandle)
_FnLogin = ctypes.CFUNCTYPE(
    CkRv, CkSessionHandle, CkUserType, ctypes.POINTER(CkByte), CkUlong
)
_FnLogout = ctypes.CFUNCTYPE(CkRv, CkSessionHandle)
_FnGetAttributeValue = ctypes.CFUNCTYPE(
    CkRv, CkSessionHandle, CkObjectHandle, ctypes.POINTER(CkAttribute), CkUlong
)
_FnFindObjectsInit = ctypes.CFUNCTYPE(
    CkRv, CkSessionHandle, ctypes.POINTER(CkAttribute), CkUlong
)
_FnFindObjects = ctypes.CFUNCTYPE(
    CkRv,
    CkSessionHandle,
    ctypes.POINTER(CkObjectHandle),
    CkUlong,
    ctypes.POINTER(CkUlong),
)
_FnFindObjectsFinal = ctypes.CFUNCTYPE(CkRv, CkSessionHandle)
_FnSignInit = ctypes.CFUNCTYPE(
    CkRv, CkSessionHandle, ctypes.POINTER(CkMechanism), CkObjectHandle
)
_FnSign = ctypes.CFUNCTYPE(
    CkRv,
    CkSessionHandle,
    ctypes.POINTER(CkByte),
    CkUlong,
    ctypes.POINTER(CkByte),
    ctypes.POINTER(CkUlong),
)
_FnOpaque = CkVoidPtr


class CkFunctionList(_PackedStructure):
    """Canonical PKCS#11 v2.40 function list in pkcs11f.h positional order."""

    _fields_ = [
        ("version", CkVersion),
        ("C_Initialize", _FnInitialize),
        ("C_Finalize", _FnFinalize),
        ("C_GetInfo", _FnOpaque),
        ("C_GetFunctionList", _FnOpaque),
        ("C_GetSlotList", _FnGetSlotList),
        ("C_GetSlotInfo", _FnOpaque),
        ("C_GetTokenInfo", _FnGetTokenInfo),
        ("C_GetMechanismList", _FnOpaque),
        ("C_GetMechanismInfo", _FnOpaque),
        ("C_InitToken", _FnOpaque),
        ("C_InitPIN", _FnOpaque),
        ("C_SetPIN", _FnOpaque),
        ("C_OpenSession", _FnOpenSession),
        ("C_CloseSession", _FnCloseSession),
        ("C_CloseAllSessions", _FnOpaque),
        ("C_GetSessionInfo", _FnOpaque),
        ("C_GetOperationState", _FnOpaque),
        ("C_SetOperationState", _FnOpaque),
        ("C_Login", _FnLogin),
        ("C_Logout", _FnLogout),
        ("C_CreateObject", _FnOpaque),
        ("C_CopyObject", _FnOpaque),
        ("C_DestroyObject", _FnOpaque),
        ("C_GetObjectSize", _FnOpaque),
        ("C_GetAttributeValue", _FnGetAttributeValue),
        ("C_SetAttributeValue", _FnOpaque),
        ("C_FindObjectsInit", _FnFindObjectsInit),
        ("C_FindObjects", _FnFindObjects),
        ("C_FindObjectsFinal", _FnFindObjectsFinal),
        ("C_EncryptInit", _FnOpaque),
        ("C_Encrypt", _FnOpaque),
        ("C_EncryptUpdate", _FnOpaque),
        ("C_EncryptFinal", _FnOpaque),
        ("C_DecryptInit", _FnOpaque),
        ("C_Decrypt", _FnOpaque),
        ("C_DecryptUpdate", _FnOpaque),
        ("C_DecryptFinal", _FnOpaque),
        ("C_DigestInit", _FnOpaque),
        ("C_Digest", _FnOpaque),
        ("C_DigestUpdate", _FnOpaque),
        ("C_DigestKey", _FnOpaque),
        ("C_DigestFinal", _FnOpaque),
        ("C_SignInit", _FnSignInit),
        ("C_Sign", _FnSign),
        ("C_SignUpdate", _FnOpaque),
        ("C_SignFinal", _FnOpaque),
        ("C_SignRecoverInit", _FnOpaque),
        ("C_SignRecover", _FnOpaque),
        ("C_VerifyInit", _FnOpaque),
        ("C_Verify", _FnOpaque),
        ("C_VerifyUpdate", _FnOpaque),
        ("C_VerifyFinal", _FnOpaque),
        ("C_VerifyRecoverInit", _FnOpaque),
        ("C_VerifyRecover", _FnOpaque),
        ("C_DigestEncryptUpdate", _FnOpaque),
        ("C_DecryptDigestUpdate", _FnOpaque),
        ("C_SignEncryptUpdate", _FnOpaque),
        ("C_DecryptVerifyUpdate", _FnOpaque),
        ("C_GenerateKey", _FnOpaque),
        ("C_GenerateKeyPair", _FnOpaque),
        ("C_WrapKey", _FnOpaque),
        ("C_UnwrapKey", _FnOpaque),
        ("C_DeriveKey", _FnOpaque),
        ("C_SeedRandom", _FnOpaque),
        ("C_GenerateRandom", _FnOpaque),
        ("C_GetFunctionStatus", _FnOpaque),
        ("C_CancelFunction", _FnOpaque),
        ("C_WaitForSlotEvent", _FnOpaque),
    ]


class HostError(Exception):
    """A request the host refuses or cannot complete."""

    def __init__(self, message: str, code: str = ERROR_CODE_FATAL) -> None:
        super().__init__(message)
        self.code = code


def configure_binary_stdio() -> None:
    """Prevent CRLF translation on Windows native-messaging pipes."""
    if IS_WINDOWS:
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load optional JSON configuration next to host.py."""
    path = config_path or (Path(__file__).resolve().parent / CONFIG_FILENAME)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HostError("host configuration is unreadable") from exc
    if not isinstance(data, dict):
        raise HostError("host configuration is invalid")
    return data


def resolve_pkcs11_path(config: dict[str, Any]) -> str:
    """Resolve the PKCS#11 shared library path from env or config."""
    configured = os.environ.get(ENV_PKCS11_MODULE) or config.get("pkcs11_module")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    raise HostError("PKCS#11 module path is not configured")


def resolve_model(config: dict[str, Any]) -> str:
    """Resolve the target PKCS#11 token model from env or config."""
    configured = os.environ.get(ENV_MODEL) or config.get("model") or DEFAULT_MODEL
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return DEFAULT_MODEL


def resolve_allowed_origins(config: dict[str, Any]) -> frozenset[str] | None:
    """Resolve an optional origin allowlist; None permits any canonical HTTPS origin."""
    env_value = os.environ.get(ENV_ALLOWED_ORIGINS)
    if env_value is not None:
        entries = [item.strip() for item in env_value.split(",") if item.strip()]
        return frozenset(entries)
    cfg_value = config.get("allowed_origins")
    if isinstance(cfg_value, list):
        return frozenset(
            item.strip()
            for item in cfg_value
            if isinstance(item, str) and item.strip()
        )
    return None


def validate_https_origin(origin: Any, allowlist: frozenset[str] | None = None) -> str:
    """Require a canonical HTTPS origin (no path, query, fragment or userinfo)."""
    if not isinstance(origin, str) or not origin:
        raise HostError("origin is missing", code=ERROR_CODE_INVALID_ARGUMENT)
    if any(
        ord(ch) < ASCII_PRINTABLE_MIN or ord(ch) > ASCII_PRINTABLE_MAX
        for ch in origin
    ):
        raise HostError("origin is invalid", code=ERROR_CODE_INVALID_ARGUMENT)
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise HostError(
            "origin must be an HTTPS origin", code=ERROR_CODE_INVALID_ARGUMENT
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise HostError(
            "origin port is invalid", code=ERROR_CODE_INVALID_ARGUMENT
        ) from exc
    if origin != f"https://{parsed.netloc}":
        raise HostError("origin is not canonical", code=ERROR_CODE_INVALID_ARGUMENT)
    if allowlist is not None and origin not in allowlist:
        raise HostError("origin is not allowed", code=ERROR_CODE_INVALID_ARGUMENT)
    return origin


def select_web_eid_algorithm(
    algorithm_name: str, key_bits: int
) -> tuple[str, str, int | None]:
    """Map CNG key algorithm and bit length to (Web eID alg, hash name, ec sig bytes)."""
    upper = algorithm_name.upper()
    if upper.startswith("EC"):
        if key_bits == EC_P384_BITS:
            return ALGORITHM_ES384, "sha384", EC_P384_SIG_BYTES
        if key_bits == EC_P256_BITS:
            return ALGORITHM_ES256, "sha256", EC_P256_SIG_BYTES
        if key_bits == EC_P521_BITS:
            return ALGORITHM_ES512, "sha512", EC_P521_SIG_BYTES
        raise HostError("unsupported elliptic-curve key size")
    if upper == "RSA":
        return ALGORITHM_RS256, "sha256", None
    raise HostError("unsupported authentication key algorithm")


def compute_web_eid_tbs_hash(
    origin: str, challenge: str, hash_name: str = "sha384"
) -> bytes:
    """Compute H(H(origin) || H(challenge)) for Web eID authentication signing."""
    origin_digest = hashlib.new(hash_name, origin.encode("utf-8")).digest()
    challenge_digest = hashlib.new(hash_name, challenge.encode("utf-8")).digest()
    return hashlib.new(hash_name, origin_digest + challenge_digest).digest()


def classify_windows_cng_status(status: int) -> HostError:
    """Map a Windows CNG SECURITY_STATUS code to a Web eID HostError."""
    code = status & WIN_STATUS_MASK
    if code in WIN_CANCELLED_STATUSES:
        return HostError("PIN entry cancelled", code=ERROR_CODE_USER_CANCELLED)
    if code == WIN_SCARD_W_CHV_BLOCKED:
        return HostError("PIN1 is locked")
    if code == WIN_SCARD_W_WRONG_CHV:
        return HostError("PIN1 is incorrect")
    if code in (
        WIN_SCARD_W_REMOVED_CARD,
        WIN_SCARD_E_NO_SMARTCARD,
        WIN_SCARD_E_NO_READERS_AVAILABLE,
    ):
        return HostError("no smart card token is present")
    return HostError(f"Windows smart card signing failed (0x{code:08x})")


def read_message(stream: Any = None) -> dict[str, Any] | None:
    """Read one length-prefixed JSON message from the browser pipe."""
    buf = sys.stdin.buffer if stream is None else stream
    header = buf.read(MESSAGE_HEADER_BYTES)
    if not header:
        return None
    if len(header) != MESSAGE_HEADER_BYTES:
        raise HostError("truncated message header")
    (length,) = struct.unpack(MESSAGE_HEADER_FORMAT, header)
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise HostError("message length is out of range")
    body = buf.read(length)
    if len(body) != length:
        raise HostError("truncated message body")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HostError("message JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise HostError("message payload must be a JSON object")
    return parsed


def write_message(payload: dict[str, Any], stream: Any = None) -> None:
    """Write one length-prefixed JSON message to the browser pipe."""
    buf = sys.stdout.buffer if stream is None else stream
    body = json.dumps(payload).encode("utf-8")
    buf.write(struct.pack(MESSAGE_HEADER_FORMAT, len(body)) + body)
    buf.flush()


def zero_bytearray(buffer: bytearray | None) -> None:
    """Overwrite a mutable secret buffer in place."""
    if buffer is None:
        return
    for index in range(len(buffer)):
        buffer[index] = 0


# ---- Windows CNG backend (native Windows Security Smart Card PIN prompt) ----


class WindowsCngProvider:
    """Signs via Windows CNG Smart Card KSP so Windows renders the native PIN UI."""

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise HostError("Windows CNG backend is only available on Windows")
        from ctypes import wintypes

        class CertContext(ctypes.Structure):
            _fields_ = [
                ("dwCertEncodingType", wintypes.DWORD),
                ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
                ("cbCertEncoded", wintypes.DWORD),
                ("pCertInfo", ctypes.c_void_p),
                ("hCertStore", ctypes.c_void_p),
            ]

        class CryptKeyProvInfo(ctypes.Structure):
            _fields_ = [
                ("pwszContainerName", wintypes.LPWSTR),
                ("pwszProvName", wintypes.LPWSTR),
                ("dwProvType", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("cProvParam", wintypes.DWORD),
                ("rgProvParam", ctypes.c_void_p),
                ("dwKeySpec", wintypes.DWORD),
            ]

        class CtlUsage(ctypes.Structure):
            _fields_ = [
                ("cUsageIdentifier", wintypes.DWORD),
                ("rgpszUsageIdentifier", ctypes.POINTER(ctypes.c_char_p)),
            ]

        class BcryptPkcs1PaddingInfo(ctypes.Structure):
            _fields_ = [("pszAlgId", wintypes.LPCWSTR)]

        self._wintypes = wintypes
        self._CertContext = CertContext
        self._CryptKeyProvInfo = CryptKeyProvInfo
        self._CtlUsage = CtlUsage
        self._BcryptPkcs1PaddingInfo = BcryptPkcs1PaddingInfo

        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._ncrypt = ctypes.WinDLL("ncrypt", use_last_error=True)
        self._cryptui = ctypes.WinDLL("cryptui", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        p_cert = ctypes.POINTER(CertContext)
        self._crypt32.CertOpenStore.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._crypt32.CertOpenStore.restype = ctypes.c_void_p
        self._crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._crypt32.CertCloseStore.restype = wintypes.BOOL
        self._crypt32.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, p_cert]
        self._crypt32.CertEnumCertificatesInStore.restype = p_cert
        self._crypt32.CertDuplicateCertificateContext.argtypes = [p_cert]
        self._crypt32.CertDuplicateCertificateContext.restype = p_cert
        self._crypt32.CertFreeCertificateContext.argtypes = [p_cert]
        self._crypt32.CertFreeCertificateContext.restype = wintypes.BOOL
        self._crypt32.CertAddCertificateContextToStore.argtypes = [
            ctypes.c_void_p,
            p_cert,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._crypt32.CertAddCertificateContextToStore.restype = wintypes.BOOL
        self._crypt32.CertVerifyTimeValidity.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._crypt32.CertVerifyTimeValidity.restype = ctypes.c_long
        self._crypt32.CertGetCertificateContextProperty.argtypes = [
            p_cert,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._crypt32.CertGetCertificateContextProperty.restype = wintypes.BOOL
        self._crypt32.CertGetEnhancedKeyUsage.argtypes = [
            p_cert,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._crypt32.CertGetEnhancedKeyUsage.restype = wintypes.BOOL
        self._crypt32.CryptAcquireCertificatePrivateKey.argtypes = [
            p_cert,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.BOOL),
        ]
        self._crypt32.CryptAcquireCertificatePrivateKey.restype = wintypes.BOOL

        self._ncrypt.NCryptGetProperty.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        self._ncrypt.NCryptGetProperty.restype = ctypes.c_long
        self._ncrypt.NCryptSetProperty.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._ncrypt.NCryptSetProperty.restype = ctypes.c_long
        self._ncrypt.NCryptSignHash.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        self._ncrypt.NCryptSignHash.restype = ctypes.c_long
        self._ncrypt.NCryptFreeObject.argtypes = [ctypes.c_void_p]
        self._ncrypt.NCryptFreeObject.restype = ctypes.c_long

        self._cryptui.CryptUIDlgSelectCertificateFromStore.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._cryptui.CryptUIDlgSelectCertificateFromStore.restype = p_cert

        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = ctypes.c_void_p

    def _is_smart_card_client_auth_cert(self, cert_ctx: Any) -> bool:
        dw = self._wintypes.DWORD
        if self._crypt32.CertVerifyTimeValidity(None, cert_ctx.contents.pCertInfo) != 0:
            return False

        cb = dw(0)
        if not self._crypt32.CertGetCertificateContextProperty(
            cert_ctx, CERT_KEY_PROV_INFO_PROP_ID, None, ctypes.byref(cb)
        ):
            return False
        buf = (ctypes.c_ubyte * cb.value)()
        if not self._crypt32.CertGetCertificateContextProperty(
            cert_ctx, CERT_KEY_PROV_INFO_PROP_ID, buf, ctypes.byref(cb)
        ):
            return False
        info = ctypes.cast(buf, ctypes.POINTER(self._CryptKeyProvInfo)).contents
        if info.pwszProvName != SMART_CARD_KSP:
            return False

        # Require explicit id-kp-clientAuth EKU rather than treating an absent
        # EKU (cUsageIdentifier == 0) as unrestricted: eID signing certificates
        # (PIN2 non-repudiation) often omit EKU and must never be offered here.
        eku_cb = dw(0)
        if not self._crypt32.CertGetEnhancedKeyUsage(
            cert_ctx, 0, None, ctypes.byref(eku_cb)
        ) or eku_cb.value == 0:
            return False
        eku_buf = (ctypes.c_ubyte * eku_cb.value)()
        if not self._crypt32.CertGetEnhancedKeyUsage(
            cert_ctx, 0, eku_buf, ctypes.byref(eku_cb)
        ):
            return False
        usage = ctypes.cast(eku_buf, ctypes.POINTER(self._CtlUsage)).contents
        for index in range(usage.cUsageIdentifier):
            if usage.rgpszUsageIdentifier[index] == OID_CLIENT_AUTH:
                return True
        return False

    def _query_key_algorithm(self, hkey: ctypes.c_void_p) -> tuple[str, int]:
        dw = self._wintypes.DWORD
        alg_cb = dw(0)
        st = self._ncrypt.NCryptGetProperty(
            hkey, NCRYPT_PROP_ALGORITHM_NAME, None, 0, ctypes.byref(alg_cb), 0
        )
        if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS or alg_cb.value < 2:
            raise HostError("failed to query smart card key algorithm")
        alg_buf = ctypes.create_unicode_buffer(alg_cb.value // 2)
        st = self._ncrypt.NCryptGetProperty(
            hkey, NCRYPT_PROP_ALGORITHM_NAME, alg_buf, alg_cb.value, ctypes.byref(alg_cb), 0
        )
        if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS:
            raise HostError("failed to query smart card key algorithm")

        bits = dw(0)
        bits_cb = dw(0)
        st = self._ncrypt.NCryptGetProperty(
            hkey,
            NCRYPT_PROP_LENGTH,
            ctypes.byref(bits),
            ctypes.sizeof(bits),
            ctypes.byref(bits_cb),
            0,
        )
        if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS:
            raise HostError("failed to query smart card key size")
        return alg_buf.value, int(bits.value)

    def _probe_candidate_live(
        self, cert_ctx: Any
    ) -> tuple[str, str, int | None, int] | None:
        """Silently verify the card for cert_ctx is present and supports signing."""
        dw = self._wintypes.DWORD
        hkey = ctypes.c_void_p(0)
        key_spec = dw(0)
        caller_free = self._wintypes.BOOL(0)
        ok = self._crypt32.CryptAcquireCertificatePrivateKey(
            cert_ctx,
            CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG | CRYPT_ACQUIRE_SILENT_FLAG,
            None,
            ctypes.byref(hkey),
            ctypes.byref(key_spec),
            ctypes.byref(caller_free),
        )
        if not ok or not hkey.value:
            return None
        try:
            alg_name, key_bits = self._query_key_algorithm(hkey)
            web_alg, hash_name, ec_sig_bytes = select_web_eid_algorithm(
                alg_name, key_bits
            )
            digest_len = hashlib.new(hash_name).digest_size
            dummy = (ctypes.c_ubyte * digest_len)()
            sig_len = dw(0)
            if ec_sig_bytes is None:
                pad = self._BcryptPkcs1PaddingInfo(pszAlgId=BCRYPT_SHA256_ALGORITHM)
                st = self._ncrypt.NCryptSignHash(
                    hkey,
                    ctypes.byref(pad),
                    dummy,
                    digest_len,
                    None,
                    0,
                    ctypes.byref(sig_len),
                    BCRYPT_PAD_PKCS1 | NCRYPT_SILENT_FLAG,
                )
            else:
                st = self._ncrypt.NCryptSignHash(
                    hkey,
                    None,
                    dummy,
                    digest_len,
                    None,
                    0,
                    ctypes.byref(sig_len),
                    NCRYPT_SILENT_FLAG,
                )
            if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS or sig_len.value == 0:
                return None
            return web_alg, hash_name, ec_sig_bytes, key_bits
        except HostError:
            return None
        finally:
            if caller_free.value and hkey.value:
                self._ncrypt.NCryptFreeObject(hkey)

    def _collect_live_candidates(
        self, store: ctypes.c_void_p
    ) -> list[tuple[Any, str, str, int | None, int]]:
        candidates: list[tuple[Any, str, str, int | None, int]] = []
        ctx = None
        while True:
            ctx = self._crypt32.CertEnumCertificatesInStore(store, ctx)
            if not ctx:
                break
            if not self._is_smart_card_client_auth_cert(ctx):
                continue
            probed = self._probe_candidate_live(ctx)
            if probed is None:
                continue
            dup = self._crypt32.CertDuplicateCertificateContext(ctx)
            if dup:
                web_alg, hash_name, ec_sig_bytes, key_bits = probed
                candidates.append((dup, web_alg, hash_name, ec_sig_bytes, key_bits))
        return candidates

    def _select_candidate(
        self,
        candidates: list[tuple[Any, str, str, int | None, int]],
        origin: str,
        hwnd: ctypes.c_void_p,
    ) -> tuple[Any, str, str, int | None, int]:
        if len(candidates) == 1:
            return candidates[0]
        mem_store = self._crypt32.CertOpenStore(
            ctypes.c_void_p(CERT_STORE_PROV_MEMORY), 0, None, 0, None
        )
        if not mem_store:
            return candidates[0]
        try:
            for cert_ctx, *_ in candidates:
                self._crypt32.CertAddCertificateContextToStore(
                    mem_store, cert_ctx, CERT_STORE_ADD_USE_EXISTING, None
                )
            chosen = self._cryptui.CryptUIDlgSelectCertificateFromStore(
                mem_store,
                hwnd,
                "Web eID",
                f"Select certificate to authenticate to {origin}",
                0,
                0,
                None,
            )
            if not chosen:
                raise HostError(
                    "certificate selection cancelled",
                    code=ERROR_CODE_USER_CANCELLED,
                )
            try:
                chosen_der = ctypes.string_at(
                    chosen.contents.pbCertEncoded, chosen.contents.cbCertEncoded
                )
            finally:
                self._crypt32.CertFreeCertificateContext(chosen)
            for entry in candidates:
                cert_ctx = entry[0]
                der = ctypes.string_at(
                    cert_ctx.contents.pbCertEncoded, cert_ctx.contents.cbCertEncoded
                )
                if der == chosen_der:
                    return entry
            raise HostError("selected certificate is unavailable")
        finally:
            self._crypt32.CertCloseStore(mem_store, 0)

    def probe(self) -> dict[str, Any]:
        """Inspect live smart card auth cert via Windows CNG without prompting."""
        store = self._crypt32.CertOpenStore(
            ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_W),
            0,
            None,
            CERT_SYSTEM_STORE_CURRENT_USER | CERT_STORE_READONLY_FLAG,
            ctypes.c_wchar_p("MY"),
        )
        if not store:
            raise HostError("failed to open Windows certificate store")
        candidates: list[tuple[Any, str, str, int | None, int]] = []
        try:
            candidates = self._collect_live_candidates(store)
            if not candidates:
                raise HostError("no smart card authentication certificate is present")
            cert_ctx, web_alg, _hash_name, _ec_sig, key_bits = candidates[0]
            return {
                "backend": "windows-cng",
                "candidates": len(candidates),
                "algorithm": web_alg,
                "keyBits": key_bits,
                "certBytes": int(cert_ctx.contents.cbCertEncoded),
            }
        finally:
            for cert_ctx, *_ in candidates:
                self._crypt32.CertFreeCertificateContext(cert_ctx)
            self._crypt32.CertCloseStore(store, 0)

    def authenticate(self, origin: str, challenge: str) -> tuple[bytes, str, bytes]:
        """Sign Web eID challenge via Windows CNG with native Windows Security PIN UI."""
        store = self._crypt32.CertOpenStore(
            ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_W),
            0,
            None,
            CERT_SYSTEM_STORE_CURRENT_USER | CERT_STORE_READONLY_FLAG,
            ctypes.c_wchar_p("MY"),
        )
        if not store:
            raise HostError("failed to open Windows certificate store")
        candidates: list[tuple[Any, str, str, int | None, int]] = []
        hkey = ctypes.c_void_p(0)
        caller_free = self._wintypes.BOOL(0)
        try:
            candidates = self._collect_live_candidates(store)
            if not candidates:
                raise HostError("no smart card authentication certificate is present")

            hwnd = ctypes.c_void_p(self._user32.GetForegroundWindow() or 0)
            cert_ctx, web_alg, hash_name, ec_sig_bytes, _key_bits = (
                self._select_candidate(candidates, origin, hwnd)
            )
            cert_der = ctypes.string_at(
                cert_ctx.contents.pbCertEncoded, cert_ctx.contents.cbCertEncoded
            )

            dw = self._wintypes.DWORD
            key_spec = dw(0)
            ok = self._crypt32.CryptAcquireCertificatePrivateKey(
                cert_ctx,
                CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG,
                None,
                ctypes.byref(hkey),
                ctypes.byref(key_spec),
                ctypes.byref(caller_free),
            )
            if not ok or not hkey.value:
                raise HostError("failed to open smart card private key")

            if hwnd.value:
                self._ncrypt.NCryptSetProperty(
                    hkey,
                    NCRYPT_PROP_WINDOW_HANDLE,
                    ctypes.byref(hwnd),
                    ctypes.sizeof(hwnd),
                    0,
                )
            ctx_buf = ctypes.create_unicode_buffer(f"Authenticate to {origin}")
            self._ncrypt.NCryptSetProperty(
                hkey, NCRYPT_PROP_USE_CONTEXT, ctx_buf, ctypes.sizeof(ctx_buf), 0
            )

            tbs_hash = compute_web_eid_tbs_hash(origin, challenge, hash_name=hash_name)
            digest_buf = (ctypes.c_ubyte * len(tbs_hash))(*tbs_hash)
            sig_len = dw(0)

            if ec_sig_bytes is None:
                pad = self._BcryptPkcs1PaddingInfo(pszAlgId=BCRYPT_SHA256_ALGORITHM)
                pad_ptr = ctypes.byref(pad)
                sign_flags = BCRYPT_PAD_PKCS1
            else:
                pad_ptr = None
                sign_flags = 0

            st = self._ncrypt.NCryptSignHash(
                hkey,
                pad_ptr,
                digest_buf,
                len(tbs_hash),
                None,
                0,
                ctypes.byref(sig_len),
                sign_flags | NCRYPT_SILENT_FLAG,
            )
            if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS or sig_len.value == 0:
                raise classify_windows_cng_status(st)

            sig_buf = (ctypes.c_ubyte * sig_len.value)()
            out_len = dw(0)
            st = self._ncrypt.NCryptSignHash(
                hkey,
                pad_ptr,
                digest_buf,
                len(tbs_hash),
                sig_buf,
                sig_len.value,
                ctypes.byref(out_len),
                sign_flags,
            )
            if (st & WIN_STATUS_MASK) != WIN_ERROR_SUCCESS:
                raise classify_windows_cng_status(st)

            signature = bytes(sig_buf[: out_len.value])
            if ec_sig_bytes is not None and len(signature) != ec_sig_bytes:
                raise HostError("unexpected ECDSA signature length")
            return cert_der, web_alg, signature
        finally:
            if caller_free.value and hkey.value:
                self._ncrypt.NCryptFreeObject(hkey)
            for cert_ctx, *_ in candidates:
                self._crypt32.CertFreeCertificateContext(cert_ctx)
            self._crypt32.CertCloseStore(store, 0)


# ---- POSIX PKCS#11 backend (FreeBSD / Linux / macOS) ----


def read_pin_file_posix(pin_file: str) -> bytearray:
    """Read PIN1 from a POSIX mode-600 file (non-Windows headless mode only)."""
    if IS_WINDOWS:
        raise HostError("PIN file mode is not supported on Windows")
    try:
        fd = os.open(pin_file, os.O_RDONLY)
    except OSError as exc:
        raise HostError("PIN file is unavailable") from exc
    buf: bytearray | None = None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise HostError("PIN file is unavailable")
        if info.st_mode & POSIX_GROUP_OTHER_MASK:
            raise HostError("PIN file permissions are too open")
        if info.st_size <= 0 or info.st_size > MAX_MESSAGE_BYTES:
            raise HostError("PIN file is empty")
        buf = bytearray(info.st_size)
        with os.fdopen(fd, "rb", buffering=0, closefd=False) as handle:
            read_len = handle.readinto(buf)
        if not read_len:
            zero_bytearray(buf)
            raise HostError("PIN file is empty")
        del buf[read_len:]
        while buf and buf[-1] in NEWLINE_BYTES:
            buf[-1] = 0
            buf.pop()
        while buf and buf[0] in NEWLINE_BYTES:
            buf[0] = 0
            del buf[0]
        if not buf:
            raise HostError("PIN file is empty")
        result = buf
        buf = None
        return result
    finally:
        zero_bytearray(buf)
        os.close(fd)


def prompt_pin_dialog(origin: str, token_flags: int) -> bytearray:
    """Prompt the cardholder for PIN1 in a topmost masked GUI dialog (POSIX)."""
    try:
        import tkinter as tk
    except ImportError as exc:
        raise HostError("interactive PIN dialog is unavailable") from exc

    result: dict[str, bytearray | None] = {"pin": None}

    root = tk.Tk()
    root.title("RefineID Web eID")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, padx=18, pady=14)
    frame.pack(fill="both", expand=True)

    prompt_text = f"Enter ID-card PIN1 to authenticate to:\n{origin}"
    if token_flags & CKF_USER_PIN_FINAL_TRY:
        prompt_text += "\n\nWarning: final attempt before PIN1 locks."
    elif token_flags & CKF_USER_PIN_COUNT_LOW:
        prompt_text += "\n\nNote: previous PIN1 attempt failed."

    tk.Label(frame, text=prompt_text, justify="left").pack(anchor="w")

    entry = tk.Entry(frame, show="*", width=26)
    entry.pack(fill="x", pady=(10, 12))

    buttons = tk.Frame(frame)
    buttons.pack(fill="x")

    def on_cancel(_event: Any = None) -> None:
        entry.delete(0, "end")
        result["pin"] = None
        root.destroy()

    def on_submit(_event: Any = None) -> None:
        value = entry.get()
        entry.delete(0, "end")
        if not value:
            return
        result["pin"] = bytearray(value.encode("utf-8"))
        root.destroy()

    tk.Button(buttons, text="Cancel", width=10, command=on_cancel).pack(
        side="right", padx=(8, 0)
    )
    tk.Button(buttons, text="Authenticate", width=12, command=on_submit).pack(
        side="right"
    )

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.bind("<Return>", on_submit)
    root.bind("<Escape>", on_cancel)

    root.update_idletasks()
    width = root.winfo_reqwidth()
    height = root.winfo_reqheight()
    x_pos = max(0, (root.winfo_screenwidth() - width) // 2)
    y_pos = max(0, (root.winfo_screenheight() - height) // 3)
    root.geometry(f"+{x_pos}+{y_pos}")
    root.lift()
    root.focus_force()
    entry.focus_set()

    root.mainloop()

    pin = result["pin"]
    if pin is None:
        raise HostError("PIN entry cancelled", code=ERROR_CODE_USER_CANCELLED)
    return pin


def obtain_pin(origin: str, token_flags: int) -> bytearray | None:
    """Obtain PIN1 bytes on POSIX, or None for a protected authentication path."""
    if token_flags & CKF_PROTECTED_AUTHENTICATION_PATH:
        return None
    pin_file = os.environ.get(ENV_PIN_FILE)
    if pin_file and not IS_WINDOWS:
        return read_pin_file_posix(pin_file)
    return prompt_pin_dialog(origin, token_flags)


def _make_ulong_attr(attr_type: int, value: int) -> tuple[CkAttribute, CkUlong]:
    storage = CkUlong(value)
    attr = CkAttribute(
        type=attr_type,
        pValue=ctypes.addressof(storage),
        ulValueLen=ctypes.sizeof(storage),
    )
    return attr, storage


def _make_bool_attr(attr_type: int, value: int) -> tuple[CkAttribute, CkBbool]:
    storage = CkBbool(value)
    attr = CkAttribute(
        type=attr_type,
        pValue=ctypes.addressof(storage),
        ulValueLen=ctypes.sizeof(storage),
    )
    return attr, storage


def _make_bytes_attr(
    attr_type: int, value: bytes
) -> tuple[CkAttribute, ctypes.Array[Any]]:
    storage = (CkByte * len(value))(*value)
    attr = CkAttribute(
        type=attr_type,
        pValue=ctypes.addressof(storage),
        ulValueLen=len(value),
    )
    return attr, storage


class Pkcs11Provider:
    """Minimal PKCS#11 v2.40 client speaking directly to RefineID's module (POSIX)."""

    def __init__(self, module_path: str) -> None:
        try:
            self._lib = ctypes.CDLL(module_path)
        except OSError as exc:
            raise HostError("PKCS#11 module could not be loaded") from exc
        get_fn_list = self._lib.C_GetFunctionList
        get_fn_list.argtypes = [ctypes.POINTER(ctypes.POINTER(CkFunctionList))]
        get_fn_list.restype = CkRv

        fn_ptr = ctypes.POINTER(CkFunctionList)()
        rv = get_fn_list(ctypes.byref(fn_ptr))
        if rv != CKR_OK or not fn_ptr:
            raise HostError("C_GetFunctionList failed")
        self._fn = fn_ptr.contents
        if (
            self._fn.version.major != PKCS11_VERSION_MAJOR
            or self._fn.version.minor != PKCS11_VERSION_MINOR
        ):
            raise HostError("unexpected PKCS#11 interface version")

    def _find_matching_slot(self, model: str) -> tuple[int, int]:
        count = CkUlong(0)
        rv = self._fn.C_GetSlotList(CK_TRUE, None, ctypes.byref(count))
        if rv != CKR_OK or count.value == 0:
            raise HostError("no smart card token is present")
        slots = (CkSlotId * count.value)()
        rv = self._fn.C_GetSlotList(CK_TRUE, slots, ctypes.byref(count))
        if rv != CKR_OK:
            raise HostError("smart card slot enumeration failed")

        for index in range(count.value):
            slot_id = int(slots[index])
            info = CkTokenInfo()
            rv = self._fn.C_GetTokenInfo(slot_id, ctypes.byref(info))
            if rv != CKR_OK:
                continue
            token_model = bytes(info.model).decode("ascii", errors="replace").strip()
            if token_model == model:
                return slot_id, int(info.flags)
        raise HostError("matching smart card token is not present")

    def _find_first_object(
        self, session: CkSessionHandle, attrs: list[CkAttribute]
    ) -> int | None:
        template = (CkAttribute * len(attrs))(*attrs)
        rv = self._fn.C_FindObjectsInit(session, template, len(attrs))
        if rv != CKR_OK:
            raise HostError("PKCS#11 object search failed")
        try:
            handle = CkObjectHandle(0)
            found = CkUlong(0)
            rv = self._fn.C_FindObjects(
                session, ctypes.byref(handle), 1, ctypes.byref(found)
            )
            if rv != CKR_OK:
                raise HostError("PKCS#11 object search failed")
            return int(handle.value) if found.value == 1 else None
        finally:
            self._fn.C_FindObjectsFinal(session)

    def _read_bytes_attribute(
        self, session: CkSessionHandle, handle: int, attr_type: int
    ) -> bytes:
        probe = CkAttribute(type=attr_type, pValue=None, ulValueLen=0)
        rv = self._fn.C_GetAttributeValue(session, handle, ctypes.byref(probe), 1)
        if rv != CKR_OK or probe.ulValueLen == 0:
            raise HostError("PKCS#11 attribute is unavailable")
        buf = (CkByte * probe.ulValueLen)()
        query = CkAttribute(
            type=attr_type, pValue=ctypes.addressof(buf), ulValueLen=probe.ulValueLen
        )
        rv = self._fn.C_GetAttributeValue(session, handle, ctypes.byref(query), 1)
        if rv != CKR_OK:
            raise HostError("PKCS#11 attribute read failed")
        return bytes(buf[: query.ulValueLen])

    def _read_ulong_attribute(
        self, session: CkSessionHandle, handle: int, attr_type: int
    ) -> int:
        value = CkUlong(0)
        query = CkAttribute(
            type=attr_type,
            pValue=ctypes.addressof(value),
            ulValueLen=ctypes.sizeof(value),
        )
        rv = self._fn.C_GetAttributeValue(session, handle, ctypes.byref(query), 1)
        if rv != CKR_OK:
            raise HostError("PKCS#11 attribute read failed")
        return int(value.value)

    def _find_auth_cert(self, session: CkSessionHandle) -> tuple[bytes, bytes, int]:
        attr_class, _keep1 = _make_ulong_attr(CKA_CLASS, CKO_CERTIFICATE)
        attr_cat, _keep2 = _make_ulong_attr(
            CKA_CERTIFICATE_CATEGORY, CK_CERTIFICATE_CATEGORY_TOKEN_USER
        )
        cert_handle = self._find_first_object(session, [attr_class, attr_cat])
        if cert_handle is None:
            raise HostError("authentication certificate is unavailable")
        cert_der = self._read_bytes_attribute(session, cert_handle, CKA_VALUE)
        cert_id = self._read_bytes_attribute(session, cert_handle, CKA_ID)
        key_type = self._read_ulong_attribute(session, cert_handle, CKA_KEY_TYPE)
        return cert_der, cert_id, key_type

    def _find_auth_key(self, session: CkSessionHandle, cert_id: bytes) -> int:
        attr_class, _keep1 = _make_ulong_attr(CKA_CLASS, CKO_PRIVATE_KEY)
        attr_sign, _keep2 = _make_bool_attr(CKA_SIGN, CK_TRUE)
        attrs = [attr_class, attr_sign]
        _keep3: Any = None
        if cert_id:
            attr_id, _keep3 = _make_bytes_attr(CKA_ID, cert_id)
            attrs.append(attr_id)
        key_handle = self._find_first_object(session, attrs)
        if key_handle is None:
            raise HostError("authentication private key is unavailable")
        return key_handle

    def _login(self, session: CkSessionHandle, pin: bytearray | None) -> bool:
        if pin is None:
            rv = self._fn.C_Login(session, CKU_USER, None, 0)
        else:
            pin_buf = (CkByte * len(pin)).from_buffer(pin)
            rv = self._fn.C_Login(session, CKU_USER, pin_buf, len(pin))
        if rv == CKR_OK:
            return True
        if rv == CKR_USER_ALREADY_LOGGED_IN:
            return False
        if rv == CKR_PIN_INCORRECT:
            raise HostError("PIN1 is incorrect")
        if rv == CKR_PIN_LOCKED:
            raise HostError("PIN1 is locked")
        if rv in (CKR_PIN_INVALID, CKR_PIN_LEN_RANGE):
            raise HostError("PIN1 is invalid")
        raise HostError("card PIN1 verification failed")

    def _sign_ecdsa(
        self, session: CkSessionHandle, key_handle: int, digest: bytes
    ) -> bytes:
        mechanism = CkMechanism(mechanism=CKM_ECDSA, pParameter=None, ulParameterLen=0)
        rv = self._fn.C_SignInit(session, ctypes.byref(mechanism), key_handle)
        if rv != CKR_OK:
            raise HostError("authentication signature initialization failed")

        data_buf = (CkByte * len(digest))(*digest)
        sig_len = CkUlong(0)
        rv = self._fn.C_Sign(
            session, data_buf, len(digest), None, ctypes.byref(sig_len)
        )
        if rv != CKR_OK or sig_len.value == 0:
            raise HostError("authentication signature failed")

        sig_buf = (CkByte * sig_len.value)()
        rv = self._fn.C_Sign(
            session, data_buf, len(digest), sig_buf, ctypes.byref(sig_len)
        )
        if rv != CKR_OK:
            raise HostError("authentication signature failed")
        signature = bytes(sig_buf[: sig_len.value])
        if len(signature) != EC_P384_SIG_BYTES:
            raise HostError("unexpected ECDSA signature length")
        return signature

    def probe(self, model: str) -> dict[str, Any]:
        """Inspect token presence without prompting for PIN or reading PII."""
        rv = self._fn.C_Initialize(None)
        if rv not in (CKR_OK, CKR_CRYPTOKI_ALREADY_INITIALIZED):
            raise HostError("PKCS#11 initialization failed")
        initialized_here = rv == CKR_OK
        session = CkSessionHandle(0)
        try:
            slot_id, flags = self._find_matching_slot(model)
            rv = self._fn.C_OpenSession(
                slot_id, CKF_SERIAL_SESSION, None, None, ctypes.byref(session)
            )
            if rv != CKR_OK:
                raise HostError("failed to open PKCS#11 session")
            cert_der, _cert_id, key_type = self._find_auth_cert(session)
            return {
                "backend": "pkcs11",
                "model": model,
                "certBytes": len(cert_der),
                "isEc": key_type == CKK_EC,
                "pinLocked": bool(flags & CKF_USER_PIN_LOCKED),
                "protectedPath": bool(flags & CKF_PROTECTED_AUTHENTICATION_PATH),
            }
        finally:
            if session.value:
                self._fn.C_CloseSession(session)
            if initialized_here:
                self._fn.C_Finalize(None)

    def sign_authentication(
        self,
        model: str,
        origin: str,
        tbs_hash: bytes,
        pin_getter: Callable[[str, int], bytearray | None],
    ) -> tuple[bytes, bytes]:
        """Read auth cert, verify PIN1, and sign tbs_hash with CKM_ECDSA."""
        if len(tbs_hash) != SHA384_DIGEST_BYTES:
            raise HostError("unexpected authentication digest length")
        rv = self._fn.C_Initialize(None)
        if rv not in (CKR_OK, CKR_CRYPTOKI_ALREADY_INITIALIZED):
            raise HostError("PKCS#11 initialization failed")
        initialized_here = rv == CKR_OK
        session = CkSessionHandle(0)
        logged_in = False
        pin: bytearray | None = None
        try:
            slot_id, flags = self._find_matching_slot(model)
            if flags & CKF_USER_PIN_LOCKED:
                raise HostError("PIN1 is locked")
            rv = self._fn.C_OpenSession(
                slot_id, CKF_SERIAL_SESSION, None, None, ctypes.byref(session)
            )
            if rv != CKR_OK:
                raise HostError("failed to open PKCS#11 session")
            cert_der, cert_id, key_type = self._find_auth_cert(session)
            if key_type != CKK_EC:
                raise HostError("authentication key is not elliptic-curve")

            pin = pin_getter(origin, flags)
            logged_in = self._login(session, pin)
            zero_bytearray(pin)
            pin = None

            key_handle = self._find_auth_key(session, cert_id)
            signature = self._sign_ecdsa(session, key_handle, tbs_hash)
            return cert_der, signature
        finally:
            zero_bytearray(pin)
            if logged_in and session.value:
                self._fn.C_Logout(session)
            if session.value:
                self._fn.C_CloseSession(session)
            if initialized_here:
                self._fn.C_Finalize(None)


def authenticate(
    arguments: Any,
    *,
    cng_factory: Callable[[], Any] | None = None,
    pkcs11_factory: Callable[[str], Any] = Pkcs11Provider,
    pin_getter: Callable[[str, int], bytearray | None] = obtain_pin,
    config: dict[str, Any] | None = None,
    use_windows_cng: bool = IS_WINDOWS,
) -> dict[str, Any]:
    """Validate a Web eID authenticate command and return the signed token."""
    if not isinstance(arguments, dict):
        raise HostError(
            "authenticate arguments are missing", code=ERROR_CODE_INVALID_ARGUMENT
        )

    cfg = load_config() if config is None else config
    allowlist = resolve_allowed_origins(cfg)
    if allowlist is None and not use_windows_cng and os.environ.get(ENV_PIN_FILE):
        allowlist = DEFAULT_HEADLESS_ORIGINS

    origin = validate_https_origin(arguments.get("origin"), allowlist=allowlist)
    challenge = arguments.get("challengeNonce")
    if not isinstance(challenge, str) or len(challenge) < MIN_CHALLENGE_LENGTH:
        raise HostError("challenge nonce is missing", code=ERROR_CODE_INVALID_ARGUMENT)

    if use_windows_cng:
        provider = (cng_factory or WindowsCngProvider)()
        cert_der, algorithm, signature = provider.authenticate(origin, challenge)
    else:
        module_path = resolve_pkcs11_path(cfg)
        model = resolve_model(cfg)
        tbs_hash = compute_web_eid_tbs_hash(origin, challenge)
        provider = pkcs11_factory(module_path)
        cert_der, signature = provider.sign_authentication(
            model, origin, tbs_hash, pin_getter
        )
        algorithm = ALGORITHM_ES384

    return {
        "unverifiedCertificate": base64.b64encode(cert_der).decode("ascii"),
        "algorithm": algorithm,
        "signature": base64.b64encode(signature).decode("ascii"),
        "format": TOKEN_FORMAT,
        "appVersion": APP_VERSION,
    }


def handle_command(
    message: dict[str, Any],
    *,
    cng_factory: Callable[[], Any] | None = None,
    pkcs11_factory: Callable[[str], Any] = Pkcs11Provider,
    pin_getter: Callable[[str, int], bytearray | None] = obtain_pin,
    config: dict[str, Any] | None = None,
    use_windows_cng: bool = IS_WINDOWS,
) -> tuple[dict[str, Any], bool]:
    """Dispatch one decoded `eu.webeid` command. Returns (reply, should_exit)."""
    try:
        command = message.get("command")
        if command == "quit":
            return {}, True
        if command == "authenticate":
            return (
                authenticate(
                    message.get("arguments"),
                    cng_factory=cng_factory,
                    pkcs11_factory=pkcs11_factory,
                    pin_getter=pin_getter,
                    config=config,
                    use_windows_cng=use_windows_cng,
                ),
                False,
            )
        raise HostError("unsupported command", code=ERROR_CODE_INVALID_ARGUMENT)
    except HostError as error:
        return {"error": {"code": error.code, "message": str(error)}}, False


def run_probe() -> int:
    """CLI self-check: verify smart card auth detection without PIN or PII."""
    try:
        if IS_WINDOWS:
            info = WindowsCngProvider().probe()
        else:
            cfg = load_config()
            module_path = resolve_pkcs11_path(cfg)
            model = resolve_model(cfg)
            info = Pkcs11Provider(module_path).probe(model)
    except HostError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}))
        return 1
    print(json.dumps({"ok": True, **info}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--probe"]:
        return run_probe()

    configure_binary_stdio()
    write_message({"version": NATIVE_VERSION})
    while True:
        try:
            message = read_message()
        except HostError as error:
            write_message({"error": {"code": error.code, "message": str(error)}})
            return 1
        if message is None:
            return 0
        reply, should_exit = handle_command(message)
        write_message(reply)
        if should_exit:
            return 0


if __name__ == "__main__":
    sys.exit(main())
