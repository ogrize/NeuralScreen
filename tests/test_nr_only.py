"""Only with NR: the camera and the colour go off with NR and come back with it.

1. each switch is on the panel, in its own block, and off by default;
2. what reaches the worker follows NR, with the sliders kept;
3. switched off, both still run with NR off, as before;
4. one switch leaves the other effect alone;
5. NR off may sleep when the only effects left are NR-only;
6. the main loop re-sends on any NR change, not only the panel's.
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
import pipeline
import protocol
import settings_io


def sent(cfg, paused):
    """(camera, saturation) in the LENS command for these settings."""
    st = types.SimpleNamespace(
        cfg=cfg, paused=paused, worker_failed=False,
        worker=types.SimpleNamespace(poll=lambda: None, pid=7))
    with patch.object(protocol, "send_lens") as send:
        commands.send_lens_state(st)
    args, kwargs = send.call_args
    return (args[1], kwargs["saturation"]), st


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    base = {"lens": True, "lens_saturation": 0.4}

    want("both off, NR on", sent(dict(base), False)[0], (True, 0.4))
    want("both off, NR off", sent(dict(base), True)[0], (True, 0.4))
    camera = dict(base, lens_nr_only=True)
    want("camera only, NR on", sent(camera, False)[0], (True, 0.4))
    want("camera only, NR off", sent(camera, True)[0], (False, 0.4))
    colour = dict(base, colour_nr_only=True)
    want("colour only, NR on", sent(colour, False)[0], (True, 0.4))
    want("colour only, NR off", sent(colour, True)[0], (True, 0.0))
    both = dict(base, lens_nr_only=True, colour_nr_only=True)
    want("both, NR on", sent(both, False)[0], (True, 0.4))
    want("both, NR off", sent(both, True)[0], (False, 0.0))
    if both.get("lens_saturation") != 0.4 or both.get("lens") is not True:
        failures.append("Only with NR changed the saved settings")

    _, st = sent(dict(both), True)
    want("what was sent is remembered", st.lens_gate_sent, (False, False))
    st.paused = False
    if commands.lens_gate(st) == st.lens_gate_sent:
        failures.append("an NR change does not show as a difference to send")
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    if "lens_gate_sent" not in source or "commands.lens_gate(st)" not in source:
        failures.append("the main loop does not follow NR for the camera and colour")

    def sleeps(cfg):
        cfg.setdefault("frame_generation", False)
        idle = types.SimpleNamespace(cfg=cfg, paused=True, recorder=None,
                                     pending_shot=None, window_hwnd=None)
        return pipeline.wants_low_cost_off(idle)

    want("NR off stays awake for the camera", sleeps(dict(lens=True)), False)
    want("NR off stays awake for the colour", sleeps(dict(lens_saturation=0.4)), False)
    want("NR-only effects let it sleep", sleeps(dict(both)), True)
    want("the other effect keeps it awake", sleeps(dict(base, lens_nr_only=True)), False)

    defaults = settings_io._validate_config(dict(settings_io._load_default_config()))
    want("camera default", defaults.get("lens_nr_only"), False)
    want("colour default", defaults.get("colour_nr_only"), False)

    import pygame
    from test_ui_buttons import build
    pygame.init()
    menu = build()
    menu.folds = {"camera", "colour"}
    menu.layout(1920, 1080)
    owner, head = {}, None
    for item in menu.items:
        if item.kind == "fold":
            head = item.key
        elif item.kind == "toggle" and item.key in ("lens_nr_only", "colour_nr_only"):
            owner[item.key] = head
            if menu._activate_item(item) != [("toggle", item.key)]:
                failures.append(f"{item.key} does not click")
    want("the switches' blocks", owner, {"lens_nr_only": "camera", "colour_nr_only": "colour"})

    state = types.SimpleNamespace(cfg={}, paused=True, worker=None, worker_failed=False,
                                  display=types.SimpleNamespace(menu=menu))
    with patch.object(settings_io, "save_menu_layout"), contextlib.redirect_stdout(io.StringIO()):
        commands.apply_menu_action(state, ("toggle", "lens_nr_only"))
        commands.apply_menu_action(state, ("toggle", "colour_nr_only"))
    want("the switches set the config", (state.cfg.get("lens_nr_only"), state.cfg.get("colour_nr_only")),
         (True, True))

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: Only with NR turns the camera and the colour off with NR and back on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
