"""The frame counter shows how long a new picture took to reach our Present.

1. the reading comes from the worker's [lat] line, and goes when the line stops;
2. the log filter lets the line through;
3. the badge carries it, leaves it out when there is no reading, and is not drawn
   at all while NR off has the pipeline resting;
4. the worker measures on both present paths, once per picture.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import pipeline
import settings_io


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    line = "16:44:01.903  [lat] 23.4 ms mean, 31.0 ms max | 29.9 new pictures a second"
    st = types.SimpleNamespace(worker_logs=[line, "16:44:02.000  [perf] send 1ms"], lat_seen=None)
    with patch.object(settings_io.time, "monotonic", return_value=100.0):
        want("a new line", settings_io.latency_ms(st), 23.4)
    with patch.object(settings_io.time, "monotonic", return_value=100.0 + settings_io.LATENCY_FRESH_SECONDS):
        want("the same line, still fresh", settings_io.latency_ms(st), 23.4)
    with patch.object(settings_io.time, "monotonic", return_value=101.0 + settings_io.LATENCY_FRESH_SECONDS):
        want("the same line, gone stale", settings_io.latency_ms(st), None)
        st.worker_logs.append(line.replace("23.4", "40.0"))
        want("the next line", settings_io.latency_ms(st), 40.0)
    want("no line at all", settings_io.latency_ms(types.SimpleNamespace(worker_logs=[], lat_seen=None)), None)

    if "[lat]" not in pipeline._LOG_ALWAYS:
        failures.append("the log filter drops the [lat] line")

    import contextlib
    import io

    import pygame
    import display as display_mod
    pygame.init()
    with contextlib.redirect_stdout(io.StringIO()):
        disp = display_mod.Display(640, 360)
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
    badge = next(name for name in dir(disp) if "badge" in name.lower() and name.startswith("_draw"))
    screen = pygame.Surface((640, 360))

    def text_for(hud):
        drawn.clear()
        disp._hud = dict(hud, fps_overlay="tr", nr=True, gpu_ok=True, fps=39.0)
        try:
            getattr(disp, badge)(screen)
        except TypeError:
            getattr(disp, badge)()
        return " | ".join(drawn)

    if "Latency 23 ms" not in text_for({"latency_ms": 23.4}):
        failures.append(f"the badge does not show the latency: {drawn}")
    if "Latency" in text_for({"latency_ms": None}):
        failures.append(f"the badge shows a latency with no reading: {drawn}")

    # NR off and resting: the pipeline adds nothing, and the counter goes away.
    drawn.clear()
    disp._hud = {"fps_overlay": "tr", "nr": False, "gpu_ok": True, "fps": 0.0, "output_fps": 0.0}
    getattr(disp, badge)()
    if drawn:
        failures.append(f"the resting counter still draws: {drawn}")

    host = (ROOT / "native" / "dlss5-feed-host64.cpp").read_text(encoding="utf-8")
    fg = (ROOT / "native" / "frame_generation.inl").read_text(encoding="utf-8")
    if "source_fresh && !FgRequested()) NoteLatency" not in host:
        failures.append("the ordinary path does not measure, or measures FG's frames too")
    if "NoteLatency(chosen->source_seconds)" not in fg:
        failures.append("the FG presenter does not measure the real frame")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: the frame counter shows the worker's latency, and only a live reading")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
