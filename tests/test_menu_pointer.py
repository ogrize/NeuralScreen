"""Opening the panel brings back a pointer a game hides.

1. the switch is on the settings page, on by default, and works;
2. only a panel open the user asked for shows it: not the bar, not with the
   switch off, not a duplicate activation of a panel already open;
3. a pointer that shows is left alone, and a hidden one is moved onto the panel;
4. the close gives the game the foreground and its pointer back, with NR off too;
5. end to end, with `--run`: a real foreground window hides the pointer and
   confines it, force_pointer_visible brings it back and frees it, and the close
   returns all of it.

Part 5 takes the foreground for a few seconds, so a run_tests pass leaves it out.

Run:  runtime\\python.exe tests\\test_menu_pointer.py --run
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import os
import subprocess
import sys
import time
import types
from ctypes import wintypes
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("SDL_VIDEODRIVER", "windows" if "--run" in sys.argv else "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

GAME = r"""
import ctypes, os, sys, time
os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
import pygame
pygame.init()
info = pygame.display.Info()
screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.NOFRAME)
screen.fill((120, 0, 0)); pygame.display.flip()
pygame.mouse.set_visible(False)
user32 = ctypes.windll.user32
# The way a game hides it: on this input queue, whatever window asks.
while user32.ShowCursor(False) >= 0:
    pass
hwnd = pygame.display.get_wm_info()["window"]
theirs = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
mine = ctypes.windll.kernel32.GetCurrentThreadId()
user32.AttachThreadInput(mine, theirs, True)
user32.SetForegroundWindow(hwnd)
user32.AttachThreadInput(mine, theirs, False)
pygame.event.pump()
# Where a game keeps it: on the game, away from the panel, which it cannot reach.
w, h = info.current_w, info.current_h
rect = (ctypes.c_long * 4)(w - 80, h - 80, w - 20, h - 20)
user32.SetCursorPos(w - 50, h - 50)
user32.ClipCursor(ctypes.byref(rect))
print("ready", flush=True)
end = time.time() + 12
while time.time() < end:
    pygame.event.pump()
    time.sleep(0.01)
user32.ClipCursor(None)
"""


def end_to_end(failures: list[str]) -> None:
    import pygame
    import display as display_mod

    user32 = ctypes.windll.user32
    # Up before the game, as in real use: making the window would take the foreground.
    size = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    disp = display_mod.Display(*size)
    disp.set_layer_state(display_mod.LAYER_HUD)
    disp.set_menu_input(False)
    disp.set_visible(False)
    game = subprocess.Popen([sys.executable, "-c", GAME], stdout=subprocess.PIPE, text=True)
    saved = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(saved))
    try:
        # pygame prints its banner first.
        if not any("ready" in line for line in iter(game.stdout.readline, "")):
            failures.append("the game window did not come up")
            return
        time.sleep(0.5)
        if display_mod.system_cursor_visible():
            failures.append("the game did not hide the pointer: this run shows nothing")
            return
        game_hwnd = int(user32.GetForegroundWindow() or 0)
        where = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(where))
        disp.menu.visible = True
        disp.menu.page = "main"
        disp.set_menu_opaque(True)
        disp.apply_menu_mode_input()
        disp.reveal()
        disp.set_visible(True)
        disp.raise_topmost()
        disp.draw_overlay(0.0)
        pygame.event.pump()
        hidden_before = not display_mod.system_cursor_visible()
        shown = disp.force_pointer_visible()
        clip = wintypes.RECT()
        user32.GetClipCursor(ctypes.byref(clip))
        wide = clip.right - clip.left > 200
        ours = int(user32.GetForegroundWindow() or 0) == int(pygame.display.get_wm_info()["window"])
        print(f"    hidden before {hidden_before}, shows {shown}, confinement gone {wide}, "
              f"panel has the foreground {ours}")
        if not shown:
            failures.append("the pointer is still hidden after the panel opened")
        if not wide:
            failures.append("the pointer is still confined after the panel opened")
        if not ours:
            failures.append("the panel did not get the foreground")

        # The close: the game has the foreground, the pointer and its confinement.
        import commands
        disp.menu.visible = False
        disp.set_menu_input(False)
        commands._give_back_focus(types.SimpleNamespace(display=disp, last_foreground=game_hwnd))
        time.sleep(0.2)
        pygame.event.pump()
        back = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(back))
        user32.GetClipCursor(ctypes.byref(clip))
        print(f"    closed: foreground back {int(user32.GetForegroundWindow() or 0) == game_hwnd}, "
              f"pointer back {(back.x, back.y) == (where.x, where.y)}, "
              f"confined again {clip.right - clip.left <= 200}")
        if int(user32.GetForegroundWindow() or 0) != game_hwnd:
            failures.append("the close did not give the game the foreground")
        if (back.x, back.y) != (where.x, where.y):
            failures.append("the close did not put the pointer back")
        if clip.right - clip.left > 200:
            failures.append("the close did not confine the pointer again")
    finally:
        game.kill()
        user32.ClipCursor(None)
        user32.SetCursorPos(saved.x, saved.y)
        pygame.quit()


def main() -> int:
    failures: list[str] = []
    source = (ROOT / "app" / "commands.py").read_text(encoding="utf-8")
    at = source.find("st.display.force_pointer_visible()")
    guard = source[max(0, at - 260):at]
    for needed in ("not was_open", '"mini"', '"menu_show_pointer"'):
        if needed not in guard:
            failures.append(f"the open does not check {needed}")

    if "--run" in sys.argv:
        end_to_end(failures)
    else:
        import commands
        import settings_io
        import pygame
        from test_ui_buttons import build
        pygame.init()
        cfg = settings_io._validate_config(dict(settings_io._load_default_config()))
        if cfg.get("menu_show_pointer") is not True:
            failures.append("the switch is not on by default")
        menu = build()
        menu.page, menu.settings_tab = "settings", "app"
        menu.layout(1920, 1080)
        sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "menu_show_pointer"), None)
        if sw is None or menu._activate_item(sw) != [("toggle", "menu_show_pointer")]:
            failures.append("the settings page has no working switch")
        st = types.SimpleNamespace(cfg={}, display=types.SimpleNamespace(menu=menu))
        with patch.object(settings_io, "save_menu_layout"):
            commands.apply_menu_action(st, ("toggle", "menu_show_pointer"))
        if st.cfg.get("menu_show_pointer") is not False:
            failures.append("the switch did not turn a default-on setting off")

        # A pointer that shows stays where it is; only a hidden one is moved.
        import display as display_mod
        panel = pygame.Rect(100, 200, 40, 60)
        fake = types.SimpleNamespace(menu=types.SimpleNamespace(panel_rect=panel), _origin=(2560, 10),
                                     _remember_pointer=lambda: None)

        def moved(answers):
            calls = MagicMock()
            calls.GetForegroundWindow.return_value = 1
            with patch.object(display_mod, "user32", calls),                     patch.object(display_mod, "system_cursor_visible", side_effect=answers),                     patch.object(display_mod.time, "sleep"),                     patch.object(pygame.display, "get_wm_info", return_value={"window": 1}),                     patch.object(pygame.event, "pump"):
                shown = display_mod.Display.force_pointer_visible(fake)
            calls.ClipCursor.assert_called_once_with(None)
            return shown, calls.SetCursorPos.call_args_list

        # The close needs the game's window even when NR off stopped the sampling.
        open_at = source.find("was_open = st.display.menu.visible")
        if not callable(getattr(commands, "foreign_foreground", None)) or "foreign_foreground()" not in source[open_at:open_at + 300]:
            failures.append("the open does not remember the window to give back to")

        # What the close does with the pointer follows the handover.
        for handed, expect in ((True, "restore_pointer"), (False, "forget_pointer")):
            shell = MagicMock()
            st2 = types.SimpleNamespace(display=shell, last_foreground=5)
            with patch.object(commands, "give_back_foreground", return_value=handed), \
                    contextlib.redirect_stdout(io.StringIO()):
                commands._give_back_focus(st2)
            if not getattr(shell, expect).called:
                failures.append(f"a close with handover {handed} did not {expect}")

        shown, moves = moved([True])
        if not shown or moves:
            failures.append(f"a showing pointer was moved: {moves}")
        shown, moves = moved([False] * 6 + [True])
        if not shown or [tuple(m.args) for m in moves] != [(2680, 240)]:
            failures.append(f"a hidden pointer was not brought onto the panel: {moves}")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: opening the panel brings back a pointer a game hides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
