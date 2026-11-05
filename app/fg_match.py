"""Frame Generation stepped to the screen: real frames x step reaches the refresh.

The picked multiplier becomes the most FG may use. The matcher runs the lowest
step that fills the screen, so a 60 fps source on 144 Hz runs x3 even when x6 is
picked, and it never caps the source. A cap looks like the way to an exact match
but is worse on a real source (measured on 144 Hz with a 60 fps source): a cap of
36 lands on the source's own grid at 30 real frames, and showed about 100 FPS,
where no cap at x4 kept all 60 and showed 155.

The real rate comes from the presenter's report, because the main loop's frame
count also holds repeats of a picture that has not changed.
"""
from __future__ import annotations

import ctypes
import math
import re
import time

import settings_io

#: Going up: the step leaves the screen this far short of its refresh.
UNDERFILLED = 0.95
#: Going down: the step below must still beat the refresh by this much.
SPARE = 1.10
#: A step change rebuilds the FG feature, so readings must agree first.
AGREE_READINGS = 2
#: The presenter's log names the old step for a report after a change.
REFUSED_AFTER = 5.0
#: A rate this low is a still or stalled source, not a measure of what it can do.
MIN_REAL = 10.0
REFRESH_EVERY = 5.0
READ_LOG_EVERY = 0.5

_REPORT = re.compile(r"\[fg\] displayed [\d.]+ FPS \(real \+ generated, (\d+)x\).*; real ([\d.]+)")


def monitor_refresh(devicename: str = "") -> int:
    """The monitor's current refresh rate in Hz, or 0 when Windows does not say."""

    class DEVMODEW(ctypes.Structure):
        _fields_ = [("dmDeviceName", ctypes.c_wchar * 32),
                    ("dmSpecVersion", ctypes.c_ushort),
                    ("dmDriverVersion", ctypes.c_ushort),
                    ("dmSize", ctypes.c_ushort),
                    ("dmDriverExtra", ctypes.c_ushort),
                    ("dmFields", ctypes.c_ulong),
                    ("dmUnion", ctypes.c_byte * 16),
                    ("dmColor", ctypes.c_short),
                    ("dmDuplex", ctypes.c_short),
                    ("dmYResolution", ctypes.c_short),
                    ("dmTTOption", ctypes.c_short),
                    ("dmCollate", ctypes.c_short),
                    ("dmFormName", ctypes.c_wchar * 32),
                    ("dmLogPixels", ctypes.c_ushort),
                    ("dmBitsPerPel", ctypes.c_ulong),
                    ("dmPelsWidth", ctypes.c_ulong),
                    ("dmPelsHeight", ctypes.c_ulong),
                    ("dmDisplayFlags", ctypes.c_ulong),
                    ("dmDisplayFrequency", ctypes.c_ulong),
                    ("dmTail", ctypes.c_byte * 32)]

    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    current_settings = -1
    try:
        found = ctypes.windll.user32.EnumDisplaySettingsW(
            devicename or None, current_settings, ctypes.byref(mode))
    except (AttributeError, OSError, ValueError):
        return 0
    # 0 and 1 are Windows' "the hardware's default", not a rate.
    hz = int(mode.dmDisplayFrequency) if found else 0
    return hz if hz > 1 else 0


def parse_report(line: str) -> tuple[int, float] | None:
    """(step, real frames a second) from the presenter's report line."""
    found = _REPORT.search(line)
    return (int(found.group(1)), float(found.group(2))) if found else None


def wanted_step(refresh: float, real: float, step: int, most: int) -> int:
    """The step for this reading: `step` itself unless it is clearly wrong."""
    most = max(2, int(most))
    if real * step < refresh * UNDERFILLED:
        return min(most, max(2, math.ceil(refresh / real)))
    if step > 2 and real * (step - 1) >= refresh * SPARE:
        return min(most, max(2, math.ceil(refresh * SPARE / real)))
    return min(most, step)


class Matcher:
    """Holds the running step; `reading` takes each new presenter report."""

    def __init__(self) -> None:
        self.multiplier = 2
        self.real = 0.0
        self.refresh = 0
        self._most = 0
        self._ceiling: int | None = None
        self._changed_at = 0.0
        self._pending: int | None = None
        self._agree = 0

    def configure(self, now: float, refresh: int, most: int) -> None:
        most = max(2, int(most))
        if refresh == self.refresh and most == self._most:
            return
        if most != self._most:
            # A new pick is a fresh attempt at any step the runtime refused.
            self._ceiling = None
        self.refresh, self._most = refresh, most
        self.multiplier = min(self.multiplier, most)
        self._changed_at = now
        self._pending, self._agree = None, 0

    def reading(self, now: float, step: int, real: float) -> None:
        if self.refresh <= 0:
            return
        if step != self.multiplier:
            # Past the first report, a lower step is the runtime's refusal.
            if step < self.multiplier and now - self._changed_at >= REFUSED_AFTER:
                self._ceiling = max(2, step)
                self.multiplier = self._ceiling
                self._changed_at = now
            return
        self.real = real
        if real < MIN_REAL:
            self._pending, self._agree = None, 0
            return
        most = min(self._most, self._ceiling) if self._ceiling else self._most
        wanted = wanted_step(self.refresh, real, step, most)
        if wanted == step:
            self._pending, self._agree = None, 0
            return
        self._agree = self._agree + 1 if self._pending == wanted else 1
        self._pending = wanted
        if self._agree >= AGREE_READINGS:
            self.multiplier = wanted
            self._changed_at = now
            self._pending, self._agree = None, 0


def _wanted(st) -> bool:
    """Only while FG really runs: an enlarged window presents before it."""
    cfg = st.cfg
    return (cfg.get("fg_match_screen", True) is not False
            and bool(cfg.get("frame_generation", False))
            and not (cfg.get("window_upscale")
                     and getattr(st, "window_hwnd", None) is not None))


def _matcher(st) -> Matcher | None:
    if not _wanted(st):
        return None
    return getattr(st, "fg_matcher", None)


def update(st) -> None:
    """Called once per frame by the main loop; the log is read twice a second."""
    if not _wanted(st):
        st.fg_matcher = None
        return
    matcher = getattr(st, "fg_matcher", None)
    now = time.perf_counter()
    if matcher is None:
        matcher = st.fg_matcher = Matcher()
        matcher._refresh_read_at = 0.0
        matcher._log_read_at = 0.0
        matcher._last_line = ""
        matcher._hz = 0
    if now - matcher._refresh_read_at >= REFRESH_EVERY:
        matcher._refresh_read_at = now
        matcher._hz = monitor_refresh(getattr(st, "monitor_devicename", "") or "")
    matcher.configure(now, matcher._hz, settings_io.fg_user_multiplier(st.cfg))
    if now - matcher._log_read_at < READ_LOG_EVERY:
        return
    matcher._log_read_at = now
    for line in reversed(st.worker_logs[-200:]):
        if "[fg] displayed" not in line:
            continue
        if line != matcher._last_line:
            matcher._last_line = line
            report = parse_report(line)
            if report is not None:
                matcher.reading(now, *report)
        break


def multiplier(st) -> int:
    """The multiplier to ask the worker for this frame."""
    picked = settings_io.fg_user_multiplier(st.cfg)
    matcher = _matcher(st)
    if matcher is None or matcher.refresh <= 0:
        return picked
    return min(picked, matcher.multiplier)


def status(st) -> dict | None:
    """What the panel says: None while the matcher is not running."""
    matcher = _matcher(st)
    if matcher is None or matcher.refresh <= 0:
        return None
    return {"multiplier": multiplier(st), "real": round(matcher.real),
            "refresh": matcher.refresh}
