"""The Theme tile on the main page takes the next theme.

1. the tile asks for the switch, shows the theme by name and is lit off light;
2. the command walks light -> dark -> contrast -> light and saves each one;
3. all six tiles' labels and values fit their tiles, in every theme and in all
   12 languages.

Run:  runtime\\python.exe tests\\test_theme_tile.py
"""
import contextlib
import io
import os
import sys
import types

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))

import pygame  # noqa: E402


def main() -> int:
    failures = []
    pygame.init()
    import commands
    import display as display_mod
    import settings_io
    from i18n import STRINGS

    with contextlib.redirect_stdout(io.StringIO()):
        disp = display_mod.Display(640, 360)
    menu = disp.menu
    menu.visible = True
    menu.page = "main"
    menu.set_state({"theme": "dark"})
    menu.layout(1920, 1080)
    tile = next((i for i in menu.items if i.kind == "tile" and i.key == "theme"), None)
    if tile is None or menu._activate_item(tile) != [("toggle", "theme")]:
        failures.append("the main page has no Theme tile asking for the switch")
    elif tile.extra["value_text"] != "Dark" or tile.value != 1.0:
        failures.append(f"the tile shows {tile.extra} value={tile.value}")

    saved = []
    st = types.SimpleNamespace(cfg={"theme": "light"}, display=disp)
    real_save = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: saved.append(st.cfg["theme"]) or True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(3):
                commands.apply_menu_action(st, ("toggle", "theme"))
    finally:
        settings_io.save_menu_layout = real_save
    if saved != ["dark", "contrast", "light"]:
        failures.append(f"the themes went {saved}")
    if menu.state.get("theme") != "light":
        failures.append(f"the menu was left on {menu.state.get('theme')}")

    for lang in STRINGS:
        for theme in settings_io.THEME_NAMES:
            menu.set_lang(lang) if hasattr(menu, "set_lang") else setattr(menu, "lang", lang)
            menu.set_state({"theme": theme, "frame_generation": True, "frame_multiplier": 4})
            menu.layout(1920, 1080)
            room = lambda item: item.rect.w - menu._u(20)  # noqa: E731 - both insets
            for item in menu.items:
                if item.kind != "tile":
                    continue
                for font, text in ((menu._small_font, item.extra["label"]),
                                   (menu._font, item.extra["value_text"])):
                    if font.size(text)[0] > room(item):
                        failures.append(f"{lang}/{theme}: {text!r} is "
                                        f"{font.size(text)[0]} px in {room(item)} px of a tile")
    pygame.quit()

    for f in sorted(set(failures)):
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
