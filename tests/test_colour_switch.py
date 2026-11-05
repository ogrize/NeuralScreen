"""Colour has a tile of its own and works with NR off.

1. the tile is on the main page and in the bar, and clicking it asks for the
   colour's switch;
2. off sends a LENS with all five amounts at zero and leaves the sliders where
   they are; on sends them again;
3. NR OFF stays awake while the colour changes the picture, and idles when the
   switch is off or every slider is at zero;
4. a new worker is told only while the colour is in force.

The colour's effect on the picture with NR off is checked on the worker by
test_lens.py.

Run:  runtime\\python.exe tests\\test_colour_switch.py
"""
import contextlib
import io
import os
import struct
import sys
import types

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))

import pygame  # noqa: E402


class _Stdin:
    def __init__(self):
        self.sent = b""

    def write(self, data):
        self.sent += data

    def flush(self):
        pass


def main() -> int:
    failures = []
    pygame.init()
    import commands
    import display as display_mod
    import pipeline
    import protocol
    import settings_io

    with contextlib.redirect_stdout(io.StringIO()):
        disp = display_mod.Display(640, 360)
    menu = disp.menu
    menu.visible = True
    menu.page = "main"
    menu.set_state({"colour": True})
    menu.layout(1920, 1080)
    tile = next((i for i in menu.items if i.kind == "tile" and i.key == "colour"), None)
    if tile is None or menu._activate_item(tile) != [("toggle", "colour")]:
        failures.append("the main page has no Colour tile asking for the switch")
    menu._layout_bar(1920, 1080)
    if not any(i.kind == "tile" and i.key == "colour" for i in menu.items):
        failures.append("the bar has no Colour tile")

    worker = types.SimpleNamespace(stdin=_Stdin(), pid=1, poll=lambda: None)
    st = types.SimpleNamespace(cfg={"lens_saturation": 0.5, "lens_tint": -0.25}, display=disp,
                               worker=worker, worker_failed=False, paused=True, recorder=None,
                               pending_shot=None)
    if pipeline.wants_low_cost_off(st):
        failures.append("with a slider off zero, NR OFF would idle and show no colour")
    if not commands.lens_wanted(st.cfg):
        failures.append("a new worker is not told about the colour")

    real_save = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: True   # no config.json written here
    size = struct.calcsize(protocol.LENS_FMT)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "colour"))
            off = struct.unpack(protocol.LENS_FMT, worker.stdin.sent[-size:])
            off_idles = pipeline.wants_low_cost_off(st)
            told_off = commands.lens_wanted(st.cfg)
            commands.apply_menu_action(st, ("toggle", "colour"))
            on = struct.unpack(protocol.LENS_FMT, worker.stdin.sent[-size:])
    finally:
        settings_io.save_menu_layout = real_save
    # magic, flags, fov, noise, pts, then saturation .. tint and the interlace.
    if off[5:10] != (0.0, 0.0, 0.0, 0.0, 0.0):
        failures.append(f"off still sent the colour {off[5:10]}")
    if on[5] != 0.5 or on[9] != -0.25:
        failures.append(f"on did not send the sliders back {on[5:10]}")
    if st.cfg["lens_saturation"] != 0.5 or st.cfg["lens_tint"] != -0.25:
        failures.append(f"the switch moved the sliders: {st.cfg}")
    if not off_idles:
        failures.append("with the switch off, NR OFF stayed awake for nothing")
    if told_off:
        failures.append("with the switch off, a new worker is still told about the colour")
    st.cfg = {"lens_saturation": 0.0}
    if not pipeline.wants_low_cost_off(st):
        failures.append("with every slider at zero, NR OFF stayed awake for nothing")
    pygame.quit()

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
