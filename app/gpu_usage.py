"""How busy the GPU is, for the frame counter.

The figure comes from the NVIDIA driver's own library (NVML), the same one
nvidia-smi reads. It is the whole GPU, the game and everything else together.

A saturated GPU can hold the call up, so it runs on its own thread and the main
loop only ever picks up the last number. The thread stops soon after nobody asks,
so a counter that is switched off costs nothing. No NVIDIA library, or no NVIDIA
GPU: there is no reading, and the counter leaves it out.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time

READ_EVERY = 1.0
#: Older than this is not shown: the driver has stopped answering.
FRESH_SECONDS = 3.0
STOP_AFTER = 5.0

_value: int | None = None
_read_at = 0.0
_asked_at = 0.0
_thread: threading.Thread | None = None
_unavailable = False


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _load_library():
    # By full path: a bare name would also search the program's own folders.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for path in (os.path.join(root, "System32", "nvml.dll"),
                 os.path.join(files, "NVIDIA Corporation", "NVSMI", "nvml.dll")):
        try:
            return ctypes.WinDLL(path)
        except OSError:
            continue
    return None


class _Nvml:
    """Every NVIDIA GPU in the machine; read() gives the busiest."""

    def __init__(self, library) -> None:
        self.library = library
        self.handles: list[ctypes.c_void_p] = []
        library.nvmlDeviceGetUtilizationRates.argtypes = [ctypes.c_void_p,
                                                          ctypes.POINTER(_Utilization)]
        library.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,
                                                          ctypes.POINTER(ctypes.c_void_p)]
        if library.nvmlInit_v2() != 0:
            raise OSError("nvmlInit failed")
        count = ctypes.c_uint(0)
        if library.nvmlDeviceGetCount_v2(ctypes.byref(count)) == 0:
            for index in range(count.value):
                handle = ctypes.c_void_p()
                if library.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) == 0:
                    self.handles.append(handle)
        if not self.handles:
            library.nvmlShutdown()
            raise OSError("no NVIDIA GPU")

    def read(self) -> int | None:
        best = None
        for handle in self.handles:
            rates = _Utilization()
            if self.library.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(rates)) == 0:
                best = rates.gpu if best is None else max(best, rates.gpu)
        return best

    def close(self) -> None:
        self.library.nvmlShutdown()


def _open():
    library = _load_library()
    if library is None:
        return None
    try:
        return _Nvml(library)
    except (OSError, AttributeError):
        return None


def _run() -> None:
    global _value, _read_at, _thread, _unavailable
    source = _open()
    if source is None:
        _unavailable = True
        _thread = None
        print("[main] GPU usage: no NVIDIA reading on this machine")
        return
    print("[main] GPU usage: reading from the NVIDIA driver")
    try:
        while time.monotonic() - _asked_at < STOP_AFTER:
            try:
                value = source.read()
            except OSError:
                value = None
            if value is not None:
                _value, _read_at = value, time.monotonic()
            time.sleep(READ_EVERY)
    finally:
        try:
            source.close()
        except OSError:
            pass
        _thread = None


def percent() -> int | None:
    """The latest reading, or None. Never waits on the driver."""
    global _asked_at, _thread
    now = time.monotonic()
    _asked_at = now
    if _thread is None and not _unavailable:
        _thread = threading.Thread(target=_run, name="gpu-usage", daemon=True)
        _thread.start()
    if _value is None or now - _read_at > FRESH_SECONDS:
        return None
    return _value
