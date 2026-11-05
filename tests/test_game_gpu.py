"""NR ahead of the game: its GPU class goes down with NR on and comes back with NR off.

1. the real call, on a real process of ours: lowered to idle, then put back;
2. it follows NR, the switch and the game's window, and only full-screen windows;
3. a process that refuses is not asked again;
4. a game left lowered by a killed NeuralScreen is put back at the next start, and
   a process that merely reused the pid is left alone;
5. the switch is in Network, off by default, and the main loop and its exit call in.
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import commands
import game_gpu
import settings_io


# A process has a GPU class only once it has a device, as any game does.
GAME = """
import ctypes, time, uuid
riid = uuid.UUID("189819f1-1db6-4b57-be54-1821339b85f7").bytes_le
device = ctypes.c_void_p()
ctypes.WinDLL("d3d12").D3D12CreateDevice(None, 0xB000, riid, ctypes.byref(device))
print("ready" if device else "none", flush=True)
time.sleep(60)
"""


def start_game():
    process = subprocess.Popen([sys.executable, "-c", GAME], stdout=subprocess.PIPE, text=True)
    return process, process.stdout.readline().strip() == "ready"


def read_class(pid: int) -> int:
    handle = game_gpu._open(pid)
    value = ctypes.c_int(-1)
    game_gpu._get_class(handle, ctypes.byref(value))
    game_gpu._kernel32.CloseHandle(handle)
    return value.value


def tick(st, pid):
    game_gpu._next_update = 0.0
    with patch.object(game_gpu, "_game_pid", return_value=pid), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        game_gpu.update(st)


def main() -> int:
    failures: list[str] = []

    def want(name, got, expected):
        if got != expected:
            failures.append(f"{name}: {got}, not {expected}")

    with tempfile.TemporaryDirectory() as folder:
        game_gpu.STATE_FILE = Path(folder) / "state.json"
        game, game_ready = start_game()
        other, other_ready = start_game()
        if not (game_ready and other_ready):
            failures.append("no D3D12 device for the test processes")
        try:
            st = types.SimpleNamespace(cfg={"game_gpu_low": True}, paused=False, worker_failed=False)
            before = read_class(game.pid)
            tick(st, game.pid)
            want("NR on lowers the game", read_class(game.pid), game_gpu.CLASS_IDLE)
            want("the panel names it", game_gpu.status() is not None, True)
            want("the note is written", json.loads(game_gpu.STATE_FILE.read_text())["pid"], game.pid)

            tick(st, 0)
            want("an Alt-Tab keeps it lowered", read_class(game.pid), game_gpu.CLASS_IDLE)

            tick(st, other.pid)
            want("the new game is lowered", read_class(other.pid), game_gpu.CLASS_IDLE)
            want("the old one is put back", read_class(game.pid), before)

            st.paused = True
            tick(st, other.pid)
            want("NR off puts it back", read_class(other.pid), before)
            want("nothing held", game_gpu.status(), None)
            want("the note is gone", game_gpu.STATE_FILE.exists(), False)

            st.paused = False
            st.cfg["game_gpu_low"] = False
            tick(st, game.pid)
            want("switched off leaves it alone", read_class(game.pid), before)

            st.cfg["game_gpu_low"] = True
            tick(st, game.pid)
            st.worker_failed = True
            tick(st, game.pid)
            want("a failed worker puts it back", read_class(game.pid), before)
            st.worker_failed = False

            # A killed NeuralScreen: the class stays down and the note stays.
            tick(st, game.pid)
            game_gpu._kernel32.CloseHandle(game_gpu._current.handle)
            game_gpu._current = None
            want("still lowered", read_class(game.pid), game_gpu.CLASS_IDLE)
            with contextlib.redirect_stdout(io.StringIO()):
                game_gpu.recover()
            want("the next start puts it back", read_class(game.pid), before)
            want("and clears the note", game_gpu.STATE_FILE.exists(), False)

            # The pid now belongs to a process started at another time.
            handle = game_gpu._open(other.pid)
            game_gpu._set_class(handle, game_gpu.CLASS_IDLE)
            game_gpu.STATE_FILE.write_text(json.dumps({"pid": other.pid, "class": before, "started": 5}))
            game_gpu.recover()
            want("a reused pid is left alone", read_class(other.pid), game_gpu.CLASS_IDLE)
            game_gpu._set_class(handle, before)
            game_gpu._kernel32.CloseHandle(handle)

            # A game that exits: nothing held, nothing to restore.
            tick(st, game.pid)
            game.kill()
            game.wait()
            tick(st, 0)
            want("a game that exited is let go", game_gpu.status(), None)
            want("its note is gone", game_gpu.STATE_FILE.exists(), False)

            # One with no GPU device yet refuses, and is asked once.
            bare = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            game_gpu._refused.clear()
            tick(st, bare.pid)
            want("a process with no device is let be", game_gpu.status(), None)
            want("and leaves no note", game_gpu.STATE_FILE.exists(), False)
            want("and is noted as refused", bare.pid in game_gpu._refused, True)
            bare.kill()
            bare.wait()

            # A process that refuses is asked once.
            game_gpu._refused.clear()
            with patch.object(game_gpu, "_open", return_value=None) as opened:
                tick(st, 4242)
                tick(st, 4242)
            want("asked once", opened.call_count, 1)
        finally:
            game_gpu.release()
            for process in (game, other):
                process.kill()
                process.wait()

    defaults = settings_io._validate_config(dict(settings_io._load_default_config()))
    want("off by default", defaults.get("game_gpu_low"), False)
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for needed in ("game_gpu.update(st)", "game_gpu.release()", "game_gpu.recover()"):
        if needed not in source:
            failures.append(f"main.py does not call {needed}")

    import pygame
    from test_ui_buttons import build
    pygame.init()
    menu = build()
    menu.folds = {"net"}
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "game_gpu_low"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "game_gpu_low")]:
        failures.append("Network has no working switch")
    state = types.SimpleNamespace(cfg={}, display=types.SimpleNamespace(menu=menu))
    with patch.object(settings_io, "save_menu_layout"), contextlib.redirect_stdout(io.StringIO()):
        commands.apply_menu_action(state, ("toggle", "game_gpu_low"))
    want("the switch sets the config", state.cfg.get("game_gpu_low"), True)

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: the game's GPU class follows NR, comes back, and survives a crash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
