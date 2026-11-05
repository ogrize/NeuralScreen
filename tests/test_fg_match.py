"""Match the screen: the lowest FG step that fills it, with the source uncapped.

1. the step for a source rate, on 144 and 60 Hz, up to the picked most;
2. readings must agree, and a rate near a boundary does not flap;
3. a report from the old step is ignored, and a refused step becomes the ceiling;
4. a still source changes nothing, and a new refresh or pick is honoured;
5. the real rate is read from the presenter's line, whose older parsers still work;
6. switched off, or with an enlarged window, the picked multiplier is sent;
7. the frame limit stays the user's own;
8. the live refresh reading, the panel switch, and the worker's timer and report.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import commands
import fg_match
import settings_io
from fg_match import Matcher, parse_report, wanted_step

LINE = ("13:46:13.614  [fg] displayed 180.5 FPS (real + generated, {step}x); "
        "experimental flat-depth guides; real {real} fps")


def settled(real, refresh=144, most=6, start=2):
    """Where the step lands from `start` after readings at a steady rate."""
    m = Matcher()
    m.multiplier = start
    now = 100.0
    m.configure(now, refresh, most)
    for _ in range(20):
        now += 2.0
        m.reading(now, m.multiplier, real)
    return m.multiplier


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    want("60 real on 144", settled(60), 3)
    want("80 real on 144", settled(80), 2)
    want("40 real on 144", settled(40), 4)
    want("24 real on 144", settled(24), 6)
    want("12 real on 144 stops at the most", settled(12), 6)
    want("the pick is the most", settled(40, most=2), 2)
    want("60 real on 60 Hz", settled(60, refresh=60), 2)
    want("x6 falls to what fills the screen", settled(60, start=6), 3)
    # 71 x 2 = 142 is within reach of 144, and x3 at 71 does not earn x2 back.
    want("near a boundary stays at x2", wanted_step(144, 71, 2, 6), 2)
    want("near a boundary stays at x3", wanted_step(144, 72, 3, 6), 3)

    m = Matcher()
    m.configure(100.0, 144, 6)
    m.reading(102.0, 2, 60)
    want("one reading is not enough", m.multiplier, 2)
    m.reading(104.0, 2, 90)
    m.reading(106.0, 2, 60)
    want("a reading that disagrees starts over", m.multiplier, 2)
    m.reading(108.0, 2, 60)
    want("two that agree are", m.multiplier, 3)
    m.reading(109.0, 2, 60)
    want("a report from the old step is ignored", m.multiplier, 3)
    m.reading(120.0, 2, 60)
    want("still x2 long after: the runtime refused x3", m.multiplier, 2)
    for t in (122.0, 124.0, 126.0):
        m.reading(t, 2, 60)
    want("a refused step is not asked for again", m.multiplier, 2)
    m.configure(128.0, 144, 5)
    for t in (130.0, 132.0):
        m.reading(t, 2, 60)
    want("a new pick tries it again", m.multiplier, 3)

    m = Matcher()
    m.configure(100.0, 144, 6)
    m.multiplier = 3
    for t in range(102, 130, 2):
        m.reading(float(t), 3, 2.0)
    want("a still source changes nothing", m.multiplier, 3)
    m.configure(130.0, 60, 6)
    for t in (132.0, 134.0, 136.0):
        m.reading(t, 3, 60.0)
    want("a new refresh is honoured", m.multiplier, 2)

    line = LINE.format(step=4, real="59.5")
    want("the report", parse_report(line), (4, 59.5))
    want("an older report line", parse_report("[fg] displayed 80.0 FPS (real + generated, 2x); x"), None)
    st = types.SimpleNamespace(worker_logs=[line])
    want("the panel's step parser", settings_io._fg_active_multiplier(st), 4)
    want("the panel's rate parser", settings_io._fg_displayed_fps(st), 180.5)

    st = types.SimpleNamespace(
        cfg={"frame_generation": True, "frame_multiplier": 6, "frame_limit_mode": "unlimited"},
        window_hwnd=None, worker_logs=[], monitor_devicename="", fg_matcher=None)
    matcher = st.fg_matcher = Matcher()
    matcher.refresh, matcher.multiplier = 144, 3
    want("multiplier sent", fg_match.multiplier(st), 3)
    st.cfg["frame_multiplier"] = 2
    want("never above the pick", fg_match.multiplier(st), 2)
    st.cfg["frame_multiplier"] = 6
    st.cfg["fg_match_screen"] = False
    want("off: the pick is sent", fg_match.multiplier(st), 6)
    st.cfg["fg_match_screen"] = True
    st.cfg["window_upscale"], st.window_hwnd = True, 123
    want("enlarged window: the pick is sent", fg_match.multiplier(st), 6)
    st.window_hwnd = None
    st.cfg["frame_generation"] = False
    fg_match.update(st)
    if st.fg_matcher is not None:
        failures.append("FG off left the matcher running")
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    if "frame_pacer.wait(settings_io.frame_limit_fps(st.cfg), loop_start)" not in source:
        failures.append("the frame limit is no longer the user's own")

    worker = (ROOT / "native" / "frame_generation.inl").read_text(encoding="utf-8")
    if "timeBeginPeriod(1)" not in worker or "timeEndPeriod(1)" not in worker:
        failures.append("the presenter lost its 1 ms timer: most generated frames come up late")
    if '"real %.1f fps"' not in worker:
        failures.append("the presenter's report no longer names the real rate")

    hz = fg_match.monitor_refresh()
    if os.name == "nt" and not 24 <= hz <= 1000:
        failures.append(f"the main screen reads {hz} Hz")
    if fg_match.monitor_refresh(r"\\.\NoSuchDisplay") != 0:
        failures.append("a missing display did not read as 0")

    cfg = settings_io._validate_config(dict(settings_io._load_default_config()))
    if cfg.get("fg_match_screen") is not True:
        failures.append("Match the screen is not on by default")

    import pygame
    from test_ui_buttons import build
    pygame.init()
    menu = build()
    menu.folds = {"fg"}
    menu.set_state({"frame_generation": True, "frame_multiplier": 6,
                    "fg_match": {"multiplier": 3, "real": 60, "refresh": 144}})
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "fg_match_screen"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "fg_match_screen")]:
        failures.append("the panel has no working Match the screen switch")
    state = types.SimpleNamespace(cfg={}, display=types.SimpleNamespace(menu=menu))
    saved = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(state, ("toggle", "fg_match_screen"))
    finally:
        settings_io.save_menu_layout = saved
    if state.cfg.get("fg_match_screen") is not False:
        failures.append("the switch did not turn a default-on setting off")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: FG runs the lowest step that fills the screen, uncapped and steady")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
