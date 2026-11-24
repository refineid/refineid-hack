#!/usr/bin/env python3
"""Native messaging host answering Web eID authentication requests.

Speaks PKCS#11 v2.40 directly to the RefineID PKCS#11 module via ctypes so
the same host runs on Windows, FreeBSD, Linux and macOS without OpenSSL or
pkcs11-provider. Builds the Web eID authentication token:
SHA-384(origin) concatenated with SHA-384(challenge), hashed with SHA-384
and signed with CKM_ECDSA by the card's authentication key.

The PIN stays on the machine in a zeroed bytearray; the host never logs,
prints or returns it, nor any cardholder data or challenge.
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

# ---- Web eID protocol constants ----

EC_SIGNATURE_BYTES = 96
SHA384_DIGEST_BYTES = 48
MIN_CHALLENGE_LENGTH = 32
ALGORITHM_ES384 = "ES384"
TOKEN_FORMAT = "web-eid:1.0"
APP_VERSION = "refineid/2.0.0"
EXTENSION_VERSION = "2.0.0"
ERROR_CODE_FATAL = "ERR_WEBEID_NATIVE_FATAL"
ERROR_CODE_USER_CANCELLED = "ERR_WEBEID_USER_CANCELLED"
MAX_MESSAGE_BYTES = 1 << 20
MESSAGE_HEADER_BYTES = 4
MESSAGE_HEADER_FORMAT = "=I"

DEFAULT_MODEL = "ESTeID"
ALLOWED_ORIGINS = frozenset({"https://tara.ria.ee"})
CONFIG_FILENAME = "config.json"

ENV_PKCS11_MODULE = "REFINEID_WEBEID_PKCS11"
ENV_MODEL = "REFINEID_WEBEID_MODEL"
ENV_PIN_FILE = "REFINEID_WEBEID_PIN_FILE"
POSIX_GROUP_OTHER_MASK = 0o077
ASCII_LF = 0x0A
ASCII_CR = 0x0D
NEWLINE_BYTES = (ASCII_LF, ASCII_CR)

# ---- PKCS#11 v2.40 constants (OASIS pkcs11t.h) ----

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

IS_WINDOWS = sys.platform == "win32"

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
    """Prompt the cardholder for PIN1 in a topmost masked GUI dialog."""
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

    prompt_text = f"Enter ID-card PIN1 to log in to\n{origin}"
    if token_flags & CKF_USER_PIN_FINAL_TRY:
        prompt_text += "\n\nWarning: final attempt before PIN1 locks."
    elif token_flags & CKF_USER_PIN_COUNT_LOW:
        prompt_text += "\n\nNote: previous PIN1 attempt failed."

    tk.Label(frame, text=prompt_text, justify="left").pack(anchor="w")

    entry = tk.Entry(frame, show="*", width=24)
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
    tk.Button(buttons, text="OK", width=10, command=on_submit).pack(side="right")

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
    """Obtain PIN1 bytes, or None when the token uses a protected auth path."""
    if token_flags & CKF_PROTECTED_AUTHENTICATION_PATH:
        return None
    pin_file = os.environ.get(ENV_PIN_FILE)
    if pin_file and not IS_WINDOWS:
        return read_pin_file_posix(pin_file)
    return prompt_pin_dialog(origin, token_flags)


def compute_web_eid_tbs_hash(origin: str, challenge: str) -> bytes:
    """Compute the 48-byte SHA-384 digest signed by ES384 for Web eID.

    Web eID specifies ES384 over SHA-384(origin) || SHA-384(challenge).
    PKCS#11 CKM_ECDSA takes the pre-computed digest, so we hash the 96-byte
    concatenation with SHA-384 before handing 48 bytes to the token.
    """
    payload = (
        hashlib.sha384(origin.encode("utf-8")).digest()
        + hashlib.sha384(challenge.encode("utf-8")).digest()
    )
    return hashlib.sha384(payload).digest()


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


def _make_bytes_attr(attr_type: int, value: bytes) -> tuple[CkAttribute, ctypes.Array[Any]]:
    storage = (CkByte * len(value))(*value)
    attr = CkAttribute(
        type=attr_type,
        pValue=ctypes.addressof(storage),
        ulValueLen=len(value),
    )
    return attr, storage


class Pkcs11Provider:
    """Minimal PKCS#11 v2.40 client speaking directly to RefineID's module."""

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
        if len(signature) != EC_SIGNATURE_BYTES:
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
    message: dict[str, Any],
    provider_factory: Callable[[str], Any] = Pkcs11Provider,
    pin_getter: Callable[[str, int], bytearray | None] = obtain_pin,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a Web eID authenticate request and return the signed token."""
    origin = message.get("origin")
    challenge = message.get("challengeNonce")
    if not isinstance(origin, str) or origin not in ALLOWED_ORIGINS:
        raise HostError("origin is not allowed")
    if not isinstance(challenge, str) or len(challenge) < MIN_CHALLENGE_LENGTH:
        raise HostError("challenge nonce is missing")

    cfg = load_config() if config is None else config
    module_path = resolve_pkcs11_path(cfg)
    model = resolve_model(cfg)
    tbs_hash = compute_web_eid_tbs_hash(origin, challenge)

    provider = provider_factory(module_path)
    cert_der, signature = provider.sign_authentication(
        model, origin, tbs_hash, pin_getter
    )
    return {
        "unverifiedCertificate": base64.b64encode(cert_der).decode("ascii"),
        "algorithm": ALGORITHM_ES384,
        "signature": base64.b64encode(signature).decode("ascii"),
        "format": TOKEN_FORMAT,
        "appVersion": APP_VERSION,
    }


def status() -> dict[str, str]:
    """Return extension and native host version strings for Web eID status."""
    return {"extension": EXTENSION_VERSION, "nativeApp": APP_VERSION}


def handle_request(
    message: dict[str, Any],
    provider_factory: Callable[[str], Any] = Pkcs11Provider,
    pin_getter: Callable[[str, int], bytearray | None] = obtain_pin,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one decoded native-messaging request."""
    try:
        action = message.get("action")
        if action == "authenticate":
            return authenticate(
                message,
                provider_factory=provider_factory,
                pin_getter=pin_getter,
                config=config,
            )
        if action == "status":
            return status()
        raise HostError("unknown action")
    except HostError as error:
        return {"error": {"code": error.code, "message": str(error)}}


def run_probe() -> int:
    """CLI self-check: verify module load and card detection without PII."""
    try:
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
    while True:
        try:
            message = read_message()
        except HostError as error:
            write_message({"error": {"code": error.code, "message": str(error)}})
            return 1
        if message is None:
            return 0
        write_message(handle_request(message))


if __name__ == "__main__":
    sys.exit(main())
