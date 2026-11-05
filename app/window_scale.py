"""The mouse while a small window is enlarged to the screen.

The picture fills the monitor and the real window stays small, so a click at the
middle of the picture would land beside the window. Nothing has to be remapped: the
overlay is click-through, so what a click hits is whatever lies under the cursor.
This keeps the window placed so that the pixel the picture shows at the cursor is the
pixel under it. The cursor stays the system's own, and the game reads true coordinates
whichever way it asks for them.

Not while the game has the cursor hidden or confined (mouse look: it reads raw motion
and recentres on its own), not while the menu is open, and not while anything else has
the focus. The window goes back where it was each time this stops.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

from winapi import window_frame_rect

_user32 = ctypes.windll.user32
_SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE, _SWP_ASYNC = 0x0001, 0x0004, 0x0010, 0x4000
_CURSOR_SHOWING = 0x1
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_STEP_S = 0.004
_IDLE_S = 0.05


class _CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", ctypes.c_void_p), ("ptScreenPos", wintypes.POINT)]


def env_value(cfg: dict) -> str | None:
    """NS_WINDOW_UPSCALE for a new worker. HDR composites from the native capture."""
    return "monitor" if cfg.get("window_upscale") and not cfg.get("hdr") else None


def apply_env(cfg: dict) -> None:
    value = env_value(cfg)
    if value is None:
        os.environ.pop("NS_WINDOW_UPSCALE", None)
    else:
        os.environ["NS_WINDOW_UPSCALE"] = value
    os.environ["NS_CLIENT_PID"] = str(os.getpid())


def window_origin(cursor: tuple, monitor: tuple, frame_size: tuple) -> tuple:
    """Where the window's frame goes so the picture's pixel at the cursor is under it."""
    cx, cy = cursor
    mx, my, mw, mh = monitor
    fw, fh = frame_size
    return (round(cx - (cx - mx) * fw / mw), round(cy - (cy - my) * fh / mh))


def _cursor_is_free() -> bool:
    """False when the game hid the cursor or confined it: mouse look."""
    info = _CursorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not _user32.GetCursorInfo(ctypes.byref(info)) or not info.flags & _CURSOR_SHOWING:
        return False
    clip = wintypes.RECT()
    if not _user32.GetClipCursor(ctypes.byref(clip)):
        return True
    return (clip.left <= _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
            and clip.top <= _user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
            and clip.right - clip.left >= _user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
            and clip.bottom - clip.top >= _user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))


class WindowPlacer:
    """Its own thread: the frame loop can stall for a rebuild, and the cursor does not."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target: tuple | None = None   # (hwnd, monitor)
        self._saved: tuple | None = None    # (hwnd, x, y) from before the first move
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_target(self, hwnd: int | None, monitor: tuple | None = None) -> None:
        with self._lock:
            self._target = (int(hwnd), monitor) if hwnd and monitor else None
        if self._target is not None and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="window-placer", daemon=True)
            self._thread.start()

    @property
    def following(self) -> bool:
        return self._target is not None

    def step(self, cursor: tuple | None = None, free: bool | None = None) -> bool:
        """One placement; True when the window moved. The arguments are for tests."""
        with self._lock:
            target = self._target
        if target is None:
            self._restore()
            return False
        hwnd, monitor = target
        if self._saved is not None and self._saved[0] != hwnd:
            self._restore()
        if not (_cursor_is_free() if free is None else free):
            return False
        if cursor is None:
            point = wintypes.POINT()
            if not _user32.GetCursorPos(ctypes.byref(point)):
                return False
            cursor = (point.x, point.y)
        mx, my, mw, mh = monitor
        if not (mx <= cursor[0] < mx + mw and my <= cursor[1] < my + mh):
            return False
        frame = window_frame_rect(hwnd)
        rect = wintypes.RECT()
        if frame is None or not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return False
        x, y = window_origin(cursor, monitor, (frame[2], frame[3]))
        if (x, y) == (frame[0], frame[1]):
            return False
        if self._saved is None:
            self._saved = (hwnd, rect.left, rect.top)
        # The frame sits inside the window's rectangle by its invisible border.
        return bool(_user32.SetWindowPos(wintypes.HWND(hwnd), None,
                                         x - (frame[0] - rect.left), y - (frame[1] - rect.top), 0, 0,
                                         _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_ASYNC))

    def _restore(self) -> None:
        saved, self._saved = self._saved, None
        if saved is None:
            return
        hwnd, x, y = saved
        if _user32.IsWindow(wintypes.HWND(hwnd)):
            _user32.SetWindowPos(wintypes.HWND(hwnd), None, x, y, 0, 0,
                                 _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:
                print(f"[main] the window placer: {exc}")
            time.sleep(_STEP_S if self._target is not None else _IDLE_S)

    def stop(self) -> None:
        with self._lock:
            self._target = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._restore()


def _focus_is_on(hwnd: int) -> bool:
    fg = _user32.GetForegroundWindow()
    if not fg:
        return False
    if int(fg) == int(hwnd):
        return True
    fg_pid, target_pid = wintypes.DWORD(), wintypes.DWORD()
    _user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(target_pid))
    return fg_pid.value == target_pid.value


def sync(st) -> None:
    """Once per main-loop pass: whether the window is being kept under the cursor."""
    hwnd = getattr(st, "window_hwnd", None)
    want = (hwnd is not None and env_value(st.cfg) is not None
            and not getattr(st, "worker_failed", False)
            and not st.display.menu.visible and _focus_is_on(hwnd))
    placer = getattr(st, "window_placer", None)
    if not want and placer is None:
        return
    if placer is None:
        placer = st.window_placer = WindowPlacer()
    if want:
        ox, oy = getattr(st, "mon_origin", (0, 0))
        placer.set_target(hwnd, (ox, oy, st.mon_w, st.mon_h))
    else:
        placer.set_target(None)


def following(st) -> bool:
    placer = getattr(st, "window_placer", None)
    return placer is not None and placer.following


def stop(st) -> None:
    placer = getattr(st, "window_placer", None)
    if placer is not None:
        placer.stop()
        st.window_placer = None
