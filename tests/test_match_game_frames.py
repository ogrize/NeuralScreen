"""Match game frames: no NR on a frame the game has not changed while it moves.

1. the switch is in Network, on by default, and reaches the worker over LENS;
2. switched off, a new worker is told, since it starts with the switch on;
3. the worker keeps its result on such a repeat, and still settles a stopped picture;
4. the counter reads new pictures from the worker's line, and stops with it.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import struct
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
import protocol
import settings_io


def extras_sent(cfg):
    stdin = io.BytesIO()
    st = types.SimpleNamespace(
        cfg=cfg, paused=False, worker_failed=False,
        worker=types.SimpleNamespace(poll=lambda: None, pid=7, stdin=stdin))
    commands.send_lens_state(st)
    return int(struct.unpack(protocol.LENS_FMT, stdin.getvalue())[-1])


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    want("on by default", bool(extras_sent({}) & 1024), True)
    want("off is sent", bool(extras_sent({"nr_hold_repeats": False}) & 1024), False)
    want("off needs a new worker told", commands.lens_wanted({"nr_hold_repeats": False}), True)
    want("the default does not", commands.lens_wanted({}), False)
    defaults = settings_io._validate_config(dict(settings_io._load_default_config()))
    want("the config default", defaults.get("nr_hold_repeats"), True)

    host = (ROOT / "native" / "dlss5-feed-host64.cpp").read_text(encoding="utf-8")
    if not re.search(r"keep_result = !bypass && still_frame &&\s+\(repeat_in_motion \|\| g_still_evals > kStillEvaluations\)", host):
        failures.append("the worker does not keep its result on a repeat")
    if "g_still_evals + (repeat_in_motion ? 0u : 1u)" not in host:
        failures.append("a repeat in motion uses up the evaluations a stopped picture settles on")
    if "g_hold_repeats = (extras & 1024u) != 0;" not in host:
        failures.append("the worker does not read the switch")

    line = "1:0:0.0  [lat] 12.0 ms mean, 30.0 ms max | 29.8 new pictures a second | source gap 33.0"
    st = types.SimpleNamespace(worker_logs=[line], lat_seen=None)
    with patch.object(settings_io.time, "monotonic", return_value=100.0):
        want("the new pictures", settings_io.new_pictures_fps(st), 29.8)
    with patch.object(settings_io.time, "monotonic", return_value=200.0):
        want("a line gone stale", settings_io.new_pictures_fps(st), None)

    import pygame
    from test_ui_buttons import build
    pygame.init()
    menu = build()
    menu.folds = {"net"}
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "nr_hold_repeats"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "nr_hold_repeats")]:
        failures.append("Network has no working switch")
    state = types.SimpleNamespace(cfg={}, paused=False, worker=None, worker_failed=False,
                                  display=types.SimpleNamespace(menu=menu))
    with patch.object(settings_io, "save_menu_layout"), contextlib.redirect_stdout(io.StringIO()):
        commands.apply_menu_action(state, ("toggle", "nr_hold_repeats"))
    want("the switch turns a default-on setting off", state.cfg.get("nr_hold_repeats"), False)

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: Match game frames holds NR on repeats while the game moves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
