import ctypes
import os
from ctypes import wintypes

from .errors import ToolError


def unprotect(encoded: str) -> str:
    import base64

    if os.name != "nt":
        raise ToolError("windows_credentials_required")

    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    raw = base64.b64decode(encoded, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ToolError("credential_unavailable")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.length).decode("utf-8")
    finally:
        ctypes.memset(target.data, 0, target.length)
        kernel.LocalFree(target.data)


class Job:
    """Kill the bridge and its child processes if the owning server exits."""

    def __init__(self, pid: int):
        self.handle = None
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ToolError("process_isolation_failed")
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, Windows x64 layout; BasicLimitInformation.LimitFlags.
        info = ctypes.create_string_buffer(144)
        ctypes.c_uint32.from_buffer(info, 16).value = 0x2000
        process = None
        try:
            if not kernel.SetInformationJobObject(handle, 9, info, len(info)):
                raise ToolError("process_isolation_failed")
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process or not kernel.AssignProcessToJobObject(handle, process):
                raise ToolError("process_isolation_failed")
            self.handle = handle
        finally:
            if process:
                kernel.CloseHandle(process)
            if not self.handle:
                kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
