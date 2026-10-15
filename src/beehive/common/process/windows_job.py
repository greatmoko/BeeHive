"""按职责组织的 windows_job。"""
from __future__ import annotations
import subprocess
import sys
from typing import Any


def _win_job_create() -> Any:
    """创建一个 Windows Job Object，便于 TerminateJobObject 一次收掉子进程树。"""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    handle = kernel32.CreateJobObjectW(None, None)
    return handle or None


def _win_job_assign(job: Any, proc: subprocess.Popen) -> bool:
    if not job or sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    ph = int(getattr(proc, "_handle", 0) or 0)
    if not ph:
        return False
    return bool(kernel32.AssignProcessToJobObject(job, ph))


def _win_job_kill(job: Any) -> None:
    if not job or sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    try:
        kernel32.TerminateJobObject(job, 1)
    except OSError:
        pass
    _win_job_close(job)


def _win_job_close(job: Any) -> None:
    if not job or sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    try:
        kernel32.CloseHandle(job)
    except OSError:
        pass
