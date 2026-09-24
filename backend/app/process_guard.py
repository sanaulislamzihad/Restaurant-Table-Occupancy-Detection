"""Make child processes (ffmpeg, MediaMTX) die together with the backend.

A normal shutdown stops them explicitly. This guard also covers a crash or a
forced kill of the backend, which would otherwise leave orphan processes:

* Windows: children are put in a Job Object with KILL_ON_JOB_CLOSE, so Windows
  ends them as soon as the backend process is gone.
* Linux: children ask the kernel for SIGTERM when their parent dies.
"""

from __future__ import annotations

import ctypes
import signal
import subprocess
import sys
from typing import Any

from loguru import logger

if sys.platform == "win32":
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _set_parent_death_signal() -> None:  # runs in the child, Linux only
    PR_SET_PDEATHSIG = 1
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class ChildProcessGuard:
    """Starts child processes so that they cannot outlive this process."""

    def __init__(self) -> None:
        self._job: int | None = None
        if sys.platform == "win32":
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel32.SetInformationJobObject.argtypes = (
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            )
            self._kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            job = self._kernel32.CreateJobObjectW(None, None)
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job and self._kernel32.SetInformationJobObject(
                job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self._job = job  # kept open for the whole life of this process on purpose
            else:
                logger.warning("Could not create a job object; child processes may outlive a crash")

    @staticmethod
    def popen_kwargs() -> dict[str, Any]:
        """Extra subprocess.Popen arguments for a guarded child."""
        if sys.platform == "win32":
            return {"creationflags": subprocess.CREATE_NO_WINDOW}  # no console window, no shared Ctrl+C
        if sys.platform.startswith("linux"):
            return {"preexec_fn": _set_parent_death_signal}
        return {}

    def adopt(self, process: subprocess.Popen[Any]) -> None:
        """Tie a started child to this process's lifetime (Windows)."""
        handle = getattr(process, "_handle", None)
        if self._job is None or handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self._job, int(handle)):
            logger.warning("Could not add process {} to the job object", process.pid)
