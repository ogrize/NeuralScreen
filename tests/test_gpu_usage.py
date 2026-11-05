"""The frame counter shows how busy the GPU is.

1. the real library gives a sane number where there is an NVIDIA GPU;
2. the reader runs apart from the caller, even when the driver hangs;
3. no library means no reading, and it is not looked for again;
4. a reading that has gone stale is not shown, and the reader stops when nobody asks;
5. the badge carries it, leaves it out with no reading, and stays hidden at rest;
6. the main loop asks only while the counter is on.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import gpu_usage


def reset() -> None:
    gpu_usage._value = None
    gpu_usage._read_at = 0.0
    gpu_usage._asked_at = 0.0
    gpu_usage._thread = None
    gpu_usage._unavailable = False


def wait_for(check, seconds=3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


class Source:
    def __init__(self, read) -> None:
        self.read, self.closed = read, threading.Event()

    def close(self) -> None:
        self.closed.set()


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    if shutil.which("nvidia-smi"):
        reader = gpu_usage._open()
        if reader is None:
            failures.append("the NVIDIA library did not open")
        else:
            value = reader.read()
            reader.close()
            if not isinstance(value, int) or not 0 <= value <= 100:
                failures.append(f"a real reading of {value}")

    with contextlib.redirect_stdout(io.StringIO()):
        # A driver that hangs does not hold up the caller.
        reset()
        release = threading.Event()
        source = Source(lambda: (release.wait(10), 50)[1])
        with patch.object(gpu_usage, "_open", return_value=source):
            started = time.perf_counter()
            first = gpu_usage.percent()
            spent = time.perf_counter() - started
            want("nothing yet", first, None)
            if spent > 0.1:
                failures.append(f"percent() waited {spent:.3f} s on the driver")
            release.set()
            if not wait_for(lambda: gpu_usage.percent() == 50):
                failures.append("the reading never arrived")

            # It goes stale once the driver stops answering.
            source.read = lambda: None
            time.sleep(gpu_usage.READ_EVERY + 0.2)
            gpu_usage._read_at = time.monotonic() - gpu_usage.FRESH_SECONDS - 1
            want("a stale reading", gpu_usage.percent(), None)

            # Nobody asks: the reader closes and goes.
            thread = gpu_usage._thread
            with patch.object(gpu_usage, "STOP_AFTER", 0.0):
                if not source.closed.wait(3.0):
                    failures.append("the reader kept running with nobody asking")
                if thread is not None:
                    thread.join(3.0)
            want("no reader left", gpu_usage._thread, None)

        # No library: no reading, and it is looked for once.
        reset()
        with patch.object(gpu_usage, "_load_library", return_value=None) as load:
            want("no library", gpu_usage.percent(), None)
            wait_for(lambda: gpu_usage._unavailable)
            want("noted", gpu_usage._unavailable, True)
            want("still none", gpu_usage.percent(), None)
            time.sleep(0.05)
            want("looked for once", load.call_count, 1)
        reset()

        import pygame
        import display as display_mod
        pygame.init()
        disp = display_mod.Display(1280, 720)

    drawn: list[str] = []
    real_font = disp._font

    class Spy:
        def render(self, text, *args, **kwargs):
            drawn.append(text)
            return real_font.render(text, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_font, name)

    disp._font = Spy()
    disp._lang = "en"

    def text_for(hud):
        drawn.clear()
        disp._hud = hud
        disp._draw_fps_badge()
        return " | ".join(drawn)

    live = {"fps_overlay": "tr", "nr": True, "gpu_ok": True, "fps": 39.0, "latency_ms": 23.4}
    if "GPU 92%" not in text_for(dict(live, gpu_percent=92)):
        failures.append(f"no GPU reading on the badge: {drawn}")
    if "GPU 0%" not in text_for(dict(live, gpu_percent=0)):
        failures.append(f"an idle GPU is still a reading: {drawn}")
    if "GPU" in text_for(dict(live, gpu_percent=None)):
        failures.append(f"a GPU reading with nothing to read: {drawn}")
    off = {"fps_overlay": "tr", "nr": False, "gpu_ok": True, "fps": 0.0, "output_fps": 60.0}
    if "GPU 50%" not in text_for(dict(off, gpu_percent=50)):
        failures.append(f"NR off with frames going loses the reading: {drawn}")
    if text_for(dict(off, output_fps=0.0, gpu_percent=50)):
        failures.append(f"the resting counter draws: {drawn}")

    # The longest badge on a 4K screen still fits the worker's shared bitmap.
    import badge as badge_channel

    class Sink:
        published = None

        def publish(self, pixels, w, h, corner, margin, row):
            self.published = (len(pixels), w, h)

        def hide(self):
            self.published = None

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        wide = display_mod.Display(3840, 2160)
    wide.badge_out = sink = Sink()
    wide._lang = "pl"
    wide.menu.set_state({"nr": True, "gpu_ok": True})
    wide._hud = dict(live, fps=33.3, display_fps=144.0, fg_multiplier_active=4,
                     latency_ms=139.0, gpu_percent=100)
    wide._draw_fps_badge()
    if sink.published is None:
        failures.append("the 4K badge was not published")
    else:
        size, w, h = sink.published
        if w > badge_channel.WIDTH or h > badge_channel.HEIGHT:
            failures.append(f"the 4K badge is {w}x{h}, past the shared bitmap")
        if size != w * h * 4:
            failures.append(f"the 4K badge holds {size} bytes for {w}x{h}: its rows would shear")

    source_text = (ROOT / "main.py").read_text(encoding="utf-8").replace("\r", "")
    if "gpu_usage.percent()\n" not in source_text \
            or 'if str(st.cfg.get("fps_overlay", "off")) != "off"' not in source_text:
        failures.append("the main loop does not ask, or asks with the counter off")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: the frame counter shows GPU usage, and never waits for it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
