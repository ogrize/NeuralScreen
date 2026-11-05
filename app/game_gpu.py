"""NR ahead of the game: the game's GPU class goes down while NR is on.

A game with the focus can take the GPU from the network almost entirely (#142).
Raising the worker's class needs the realtime class and an administrator; lowering
the game's needs neither. Measured on Phantom Blade Zero in focus on an RTX 4070,
NR on, new frames a second and latency:

    game's class   normal      below normal   idle       normal again
    frames/s       10.8        12.2           37.9       10.1
    latency        98 ms       93 ms          30 ms      120 ms

Below normal changed nearly nothing, so the class used is idle. It takes GPU time
from the game only while something else wants it: a game alone runs as before.

The class belongs to the running process and is gone with it, but it outlives a
NeuralScreen that was killed. So what was lowered is written down, and the next
start puts it back.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

from paths import BASE_DIR
from winapi import _window_pid, foreign_foreground

#: D3DKMT_SCHEDULINGPRIORITYCLASS_IDLE.
CLASS_IDLE = 0
CLASS_NORMAL = 2
UPDATE_EVERY = 0.5
STATE_FILE = BASE_DIR / "game_gpu_state.json"

_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED = 0x1000
_MONITOR_DEFAULTTONEAREST = 2

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_gdi32 = ctypes.WinDLL("gdi32")
_set_class = _gdi32.D3DKMTSetProcessSchedulingPriorityClass
_set_class.argtypes = [wintypes.HANDLE, ctypes.c_int]
_set_class.restype = ctypes.c_long
_get_class = _gdi32.D3DKMTGetProcessSchedulingPriorityClass
_get_class.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
_get_class.restype = ctypes.c_long
_user32 = ctypes.WinDLL("user32")
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]


class _Lowered:
    """The process whose class we changed, while we hold it changed."""

    def __init__(self, handle, pid: int, name: str, original: int) -> None:
        self.handle, self.pid, self.name, self.original = handle, pid, name, original


_current: _Lowered | None = None
_refused: set[int] = set()
_next_update = 0.0


def _open(pid: int):
    access = _PROCESS_SET_INFORMATION | _PROCESS_QUERY_INFORMATION | _PROCESS_QUERY_LIMITED
    return _kernel32.OpenProcess(access, False, int(pid)) or None


def _alive(handle) -> bool:
    code = wintypes.DWORD()
    return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259


def _name(handle) -> str:
    buffer = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return os.path.basename(buffer.value)
    return "the game"


def _started(handle) -> int:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        return 0
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def _covers_its_monitor(hwnd: int) -> bool:
    """A full-screen or borderless game does; a browser beside a chat does not."""
    rect = wintypes.RECT()
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    monitor = _user32.MonitorFromWindow(ctypes.c_void_p(hwnd), _MONITOR_DEFAULTTONEAREST)
    if (not _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)) or not monitor
            or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info))):
        return False
    area = info.rcMonitor
    return (rect.left <= area.left and rect.top <= area.top
            and rect.right >= area.right and rect.bottom >= area.bottom)


def _game_pid(st) -> int:
    """The window NR works on, or the full-screen window that has the focus."""
    hwnd = getattr(st, "window_hwnd", None)
    if hwnd:
        return int(_window_pid(int(hwnd)) or 0)
    foreground = foreign_foreground()
    if foreground and _covers_its_monitor(foreground):
        return int(_window_pid(foreground) or 0)
    return 0


def _wanted(st) -> bool:
    return (st.cfg.get("game_gpu_low") is True and not bool(getattr(st, "paused", True))
            and not bool(getattr(st, "worker_failed", False)))


def _write_state(lowered: _Lowered | None) -> None:
    try:
        if lowered is None:
            STATE_FILE.unlink(missing_ok=True)
        else:
            STATE_FILE.write_text(json.dumps({"pid": lowered.pid, "class": lowered.original,
                                              "started": _started(lowered.handle)}),
                                  encoding="utf-8")
    except OSError as exc:
        print(f"[main] could not note the game's GPU priority: {exc}", file=sys.stderr)


def _lower(pid: int) -> None:
    global _current
    handle = _open(pid)
    if handle is None:
        _refused.add(pid)
        print(f"[main] the game's GPU priority: process {pid} refused access", file=sys.stderr)
        return
    original = ctypes.c_int(-1)
    status = _get_class(handle, ctypes.byref(original))
    if status != 0 or original.value < 0:
        original.value = CLASS_NORMAL
    lowered = _Lowered(handle, pid, _name(handle), original.value)
    # Written first: a crash between the two leaves a note that restores nothing.
    _write_state(lowered)
    status = _set_class(handle, CLASS_IDLE)
    if status != 0:
        _refused.add(pid)
        _kernel32.CloseHandle(handle)
        _write_state(None)
        print(f"[main] the game's GPU priority: {lowered.name} refused 0x{status & 0xFFFFFFFF:08X}",
              file=sys.stderr)
        return
    _current = lowered
    print(f"[main] the game's GPU priority: {lowered.name} lowered to idle while NR is on")


def release() -> None:
    """Give the game its class back. Also the way out of the program."""
    global _current
    lowered, _current = _current, None
    if lowered is None:
        return
    if _alive(lowered.handle):
        status = _set_class(lowered.handle, lowered.original)
        print(f"[main] the game's GPU priority: {lowered.name} back to class {lowered.original}"
              + (f" (0x{status & 0xFFFFFFFF:08X})" if status else ""))
    _kernel32.CloseHandle(lowered.handle)
    _write_state(None)


def recover() -> None:
    """A NeuralScreen that was killed left a game lowered: put it back at the start."""
    try:
        note = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        pid, original, started = int(note["pid"]), int(note["class"]), int(note["started"])
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError):
        _write_state(None)
        return
    handle = _open(pid)
    # The same pid and start time: the same process, not one that reused the pid.
    if handle is not None:
        if _alive(handle) and _started(handle) == started:
            _set_class(handle, original)
            print(f"[main] the game's GPU priority: {_name(handle)} put back after a crash")
        _kernel32.CloseHandle(handle)
    _write_state(None)


def update(st) -> None:
    """Called every pass of the main loop, NR on or off; acts twice a second."""
    global _next_update
    now = time.monotonic()
    if now < _next_update:
        return
    _next_update = now + UPDATE_EVERY
    if _current is not None and not _alive(_current.handle):
        release()
    if not _wanted(st):
        release()
        return
    pid = _game_pid(st)
    # Nothing full-screen in focus (an Alt-Tab): the game keeps the class it has,
    # it is still the one taking the GPU.
    if not pid or pid in _refused or (_current is not None and _current.pid == pid):
        return
    release()
    _lower(pid)


def status() -> str | None:
    """The game that is lowered now, for the panel."""
    return _current.name if _current is not None else None
