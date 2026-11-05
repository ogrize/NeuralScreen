"""The FPS fold: where the frame counter sits and which readings it carries.

1. the fold holds the corner row and a switch per reading, all on by default;
2. each switch flips its setting, and the setting is saved with the others;
3. the badge leaves out a reading that is switched off, and hides with all three off;
4. the main loop hands the switches to the badge, and asks for no GPU reading it would not show.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import commands
import settings_io

KEYS = ("fps_show_rate", "fps_show_latency", "fps_show_gpu")


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    defaults = settings_io._validate_config(dict(settings_io._load_default_config()))
    for key in KEYS:
        want(f"{key} on by default", defaults.get(key), True)
    want("the fold is remembered", "fps" in settings_io.MENU_FOLDS, True)

    import pygame
    from test_ui_buttons import build
    pygame.init()
    menu = build()
    menu.folds = set()
    menu.layout(1920, 1080)
    head = next((i for i in menu.items if i.kind == "fold" and i.key == "fps"), None)
    if head is None:
        failures.append("there is no FPS fold")
    elif any(i.kind == "toggle" and i.key in KEYS for i in menu.items):
        failures.append("the switches show with the fold closed")
    menu.folds = {"fps"}
    menu.layout(1920, 1080)
    for key in KEYS:
        sw = next((i for i in menu.items if i.kind == "toggle" and i.key == key), None)
        if sw is None or menu._activate_item(sw) != [("toggle", key)]:
            failures.append(f"the fold has no working {key} switch")
    if not any(i.key.startswith("fps_overlay") for i in menu.items if i.kind != "tile"):
        failures.append("the fold has no corner row")

    state = types.SimpleNamespace(cfg={}, display=types.SimpleNamespace(menu=menu))
    with patch.object(settings_io, "save_menu_layout"), contextlib.redirect_stdout(io.StringIO()):
        commands.apply_menu_action(state, ("toggle", "fps_show_gpu"))
        want("off", state.cfg.get("fps_show_gpu"), False)
        commands.apply_menu_action(state, ("toggle", "fps_show_gpu"))
        want("back on", state.cfg.get("fps_show_gpu"), True)

    import display as display_mod
    with contextlib.redirect_stdout(io.StringIO()):
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
    live = {"fps_overlay": "tr", "nr": True, "gpu_ok": True, "fps": 39.0,
            "latency_ms": 23.4, "gpu_percent": 92}

    def text_for(**shown):
        drawn.clear()
        disp._hud = dict(live, **shown)
        disp._draw_fps_badge()
        return " | ".join(drawn)

    everything = text_for()
    for part in ("NR 39.0", "Latency 23 ms", "GPU 92%"):
        if part not in everything:
            failures.append(f"all on, but no {part!r}: {everything!r}")
    only_gpu = text_for(fps_show_rate=False, fps_show_latency=False)
    if "GPU 92%" not in only_gpu or "NR" in only_gpu or "Latency" in only_gpu:
        failures.append(f"only GPU on, drew {only_gpu!r}")
    no_latency = text_for(fps_show_latency=False)
    if "Latency" in no_latency or "NR 39.0" not in no_latency:
        failures.append(f"latency off, drew {no_latency!r}")
    if text_for(fps_show_rate=False, fps_show_latency=False, fps_show_gpu=False):
        failures.append(f"all off still draws: {drawn}")
    # At rest it stays hidden, whatever the switches say.
    drawn.clear()
    disp._hud = {"fps_overlay": "tr", "nr": False, "gpu_ok": True, "fps": 0.0,
                 "output_fps": 0.0, "gpu_percent": 50, "fps_show_rate": False}
    disp._draw_fps_badge()
    if drawn:
        failures.append(f"the resting counter draws: {drawn}")

    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for key in KEYS:
        if f'"{key}": st.cfg.get("{key}", True) is not False' not in source:
            failures.append(f"main.py does not hand {key} to the badge")
    if 'and st.cfg.get("fps_show_gpu", True) is not False else None' not in source:
        failures.append("main.py reads the GPU with its reading switched off")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: the FPS fold places the counter and switches each reading on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
