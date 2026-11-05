"""The FPS tile, and a counter that shows while frames run.

1. the tile asks for the switch and shows the state;
2. off and on again puts the counter back in the corner it was in, and picking a
   corner remembers it;
3. the counter draws with the menu open and with NR off while frames run (the output
   rate); with NR off while the pipeline rests, or switched off, it draws nothing;
4. the idle branch of the main loop leaves no old reading behind and hides the layer.

Run:  runtime\\python.exe tests\\test_fps_tile.py
"""
import contextlib
import io
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))

import pygame  # noqa: E402


class _Sink:
    def __init__(self):
        self.published = 0
        self.hidden = 0

    def publish(self, *a):
        self.published += 1
        return True

    def hide(self):
        self.hidden += 1


def main() -> int:
    failures = []
    pygame.init()
    import commands
    import display as display_mod
    import settings_io

    with contextlib.redirect_stdout(io.StringIO()):
        disp = display_mod.Display(640, 360)
    menu = disp.menu
    menu.visible = True
    menu.page = "main"
    menu.set_state({"fps_overlay": "bl"})
    menu.layout(1920, 1080)
    tile = next((i for i in menu.items if i.kind == "tile" and i.key == "fps_overlay"), None)
    if tile is None or menu._activate_item(tile) != [("toggle", "fps_overlay")]:
        failures.append("the main page has no FPS tile")
    elif tile.value != 1.0:
        failures.append("the tile shows off with the counter on")

    st = types.SimpleNamespace(cfg={"fps_overlay": "bl"}, display=disp)
    real_save = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "fps_overlay"))
            off = st.cfg["fps_overlay"]
            commands.apply_menu_action(st, ("toggle", "fps_overlay"))
            back = st.cfg["fps_overlay"]
            commands.apply_menu_action(st, ("fps_overlay", "tl"))
            commands.apply_menu_action(st, ("toggle", "fps_overlay"))
            commands.apply_menu_action(st, ("toggle", "fps_overlay"))
            picked = st.cfg["fps_overlay"]
    finally:
        settings_io.save_menu_layout = real_save
    if off != "off" or back != "bl":
        failures.append(f"off then on went {off} then {back}, not off then bl")
    if picked != "tl":
        failures.append(f"a picked corner came back as {picked}")
    cfg = settings_io._validate_config(dict(settings_io._load_default_config(), fps_overlay_corner="x"))
    if cfg["fps_overlay_corner"] != "tr":
        failures.append("a bad corner in the config was kept")

    sink = _Sink()
    disp.badge_out = sink
    for name, visible, hud, drawn in (
            ("NR on, menu closed", False, {"nr": True, "gpu_ok": True, "fps": 60.0}, True),
            ("NR on, menu open", True, {"nr": True, "gpu_ok": True, "fps": 60.0}, True),
            ("NR off, frames running", False, {"nr": False, "gpu_ok": True, "output_fps": 72.0}, True),
            ("NR off, resting", False, {"nr": False, "gpu_ok": True, "output_fps": 0.0}, False),
            ("the card cannot run NR", False, {"nr": True, "gpu_ok": False}, False)):
        menu.visible = visible
        sink.published = 0
        disp.set_hud(dict(hud, fps_overlay="tr"))
        disp._draw_fps_badge()
        if bool(sink.published) != drawn:
            failures.append(f"{name}: drawn {bool(sink.published)}")
    sink.published = 0
    disp.set_hud({"nr": True, "gpu_ok": True, "fps": 60.0, "fps_overlay": "off"})
    disp._draw_fps_badge()
    if sink.published:
        failures.append("the counter drew while off")
    pygame.quit()

    source = (BASE / "main.py").read_text(encoding="utf-8")
    idle = source[source.index("def _service_idle_overlay"):source.index("while st.running:")]
    if '"output_fps": 0.0' not in idle or "badge_out = None" in idle:
        failures.append("the idle branch keeps a counter on the screen")

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
