"""A small window enlarged to the screen: the switch, and the mouse.

1. the window is placed so the pixel the picture shows at the cursor is under the
   cursor, at the corners and in the middle, on a monitor at any origin, for a real
   window: ScreenToClient of the cursor gives the picture's pixel;
2. nothing moves while the game hides or confines the cursor, or with the cursor on
   another monitor, and the window goes back where it was when it stops;
3. an older config's DLSS switches are dropped, and the window's motion stays on;
4. the switch shows only in window mode, a new worker hears it through its
   environment (not with HDR), and NR OFF stays awake for it only with a window.

The picture itself: tests/experiment_window_upscale.py.

Run:  runtime\\python.exe tests\\test_window_scale.py
"""
import contextlib
import ctypes
import io
import os
import sys
import time
import types
from ctypes import wintypes
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame  # noqa: E402

user32 = ctypes.windll.user32
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]


def main() -> int:
    failures = []
    import window_scale
    from offscreen_target import Target
    from winapi import window_frame_rect

    mid = window_scale.window_origin((1280, 720), (0, 0, 2560, 1440), (1280, 720))
    if mid != (640, 360):
        failures.append(f"the middle of the picture puts the frame at {mid}, not (640, 360)")
    if window_scale.window_origin((2560 + 100, 0), (2560, 0, 1920, 1080), (960, 540)) != (2610, 0):
        failures.append("a monitor at a nonzero origin is placed wrong")

    target = Target(1280, 720, name="NsScale", ghost=True)
    monitor = (0, 0, 2560, 1440)
    placer = window_scale.WindowPlacer()
    try:
        start = window_frame_rect(target.hwnd)
        # The thread is not what is tested: one step at a time.
        placer._target = (int(target.hwnd), monitor)
        for cursor in ((0, 0), (1280, 720), (2559, 1439), (640, 1200), (2000, 300)):
            placer.step(cursor=cursor, free=True)
            time.sleep(0.05)
            point = wintypes.POINT(*cursor)
            user32.ScreenToClient(wintypes.HWND(target.hwnd), ctypes.byref(point))
            want = (cursor[0] // 2, cursor[1] // 2)
            if abs(point.x - want[0]) > 1 or abs(point.y - want[1]) > 1:
                failures.append(f"cursor {cursor}: the window reads ({point.x}, {point.y}), "
                                f"the picture shows {want}")
        before = window_frame_rect(target.hwnd)
        placer.step(cursor=(100, 100), free=False)
        time.sleep(0.05)
        if window_frame_rect(target.hwnd) != before:
            failures.append("the window moved while the game had the cursor")
        placer.step(cursor=(3000, 100), free=True)
        time.sleep(0.05)
        if window_frame_rect(target.hwnd) != before:
            failures.append("the window moved for a cursor on another monitor")
        placer._target = None
        placer.step()
        time.sleep(0.05)
        if window_frame_rect(target.hwnd) != start:
            failures.append(f"the window did not go back: {window_frame_rect(target.hwnd)} != {start}")
    finally:
        placer.stop()
        target.close()

    pygame.init()
    import commands
    import display as display_mod
    import pipeline
    import settings_io

    with contextlib.redirect_stdout(io.StringIO()):
        disp = display_mod.Display(640, 360)
    menu = disp.menu
    menu.visible = True
    menu.page = "main"
    menu.folds = {"source"}
    menu.set_state({"window_mode": False, "window_upscale": False})
    menu.layout(1920, 1080)
    if any(i.key == "window_upscale" for i in menu.items):
        failures.append("the switch shows in full-screen mode")
    menu.set_state({"window_mode": True})
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "window_upscale"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "window_upscale")]:
        failures.append("window mode shows no switch")
    if "window_upscale" not in settings_io._BOOL_KEYS:
        failures.append("window_upscale is not validated as a boolean")

    cfg = settings_io._validate_config(dict(settings_io._load_default_config(), dlss=True, dlaa=False,
                                            dlss_type="ultra", dlss_upscale_nr=True, menu_folds=["dlss", "net"]))
    if any(k in cfg for k in ("dlss", "dlaa", "dlss_type", "dlss_upscale_nr")) or cfg["menu_folds"] != ["net"]:
        failures.append("an older config kept its DLSS settings")
    if "st.cfg.get(\"window_upscale\") and st.window_hwnd is not None" not in (BASE / "main.py").read_text(encoding="utf-8"):
        failures.append("the enlarged window would lose its motion field with NR off")
    window_scale.apply_env({"window_upscale": True})
    if os.environ.get("NS_WINDOW_UPSCALE") != "monitor":
        failures.append("a new worker would not hear the switch")
    window_scale.apply_env({"window_upscale": True, "hdr": True})
    if "NS_WINDOW_UPSCALE" in os.environ:
        failures.append("the mode was asked for with HDR on")
    window_scale.apply_env({})

    def idle(cfg, hwnd):
        st = types.SimpleNamespace(paused=True, cfg=cfg, recorder=None, pending_shot=None,
                                   window_hwnd=hwnd)
        return pipeline.wants_low_cost_off(st)
    if idle({"window_upscale": True}, 5):
        failures.append("with a window enlarged, NR OFF would idle and show nothing")
    if not idle({"window_upscale": True}, None):
        failures.append("with no window, NR OFF stayed awake for the switch")

    calls = []
    real = pipeline.apply_window_upscale
    pipeline.apply_window_upscale = lambda st, on: calls.append(on)
    st = types.SimpleNamespace(cfg={}, display=disp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "window_upscale"))
    finally:
        pipeline.apply_window_upscale = real
    if calls != [True]:
        failures.append(f"the switch asked for {calls}")
    pygame.quit()

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
