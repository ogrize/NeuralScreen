"""The Interlace tile stands beside Boost, works with its menu closed, and fits.

1. it is on the top row, right after Boost, with the menu closed;
2. it reports the same action as the switch inside the Interlace menu;
3. it shows the setting, on and off;
4. its name fits the tile in all twelve languages.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame


def main() -> int:
    failures: list[str] = []
    pygame.init()
    pygame.display.set_mode((64, 64))
    import fonts
    import i18n
    import overlay_ui as ui

    def menu_in(lang: str, **state):
        menu = ui.OverlayMenu(1.0, lambda size, mono=False, bold=False, _l=lang:
                              fonts.load(size, mono=mono, lang=_l))
        menu.visible = True
        menu.page = "main"
        menu.folds = set()
        menu.set_state(dict({"lang": lang, "nr": True, "gpu_ok": True}, **state))
        menu.draw(pygame.Surface((1920, 1080)))
        return menu

    def tiles(menu):
        return [i for i in menu.items if i.kind == "tile"]

    menu = menu_in("en", interlace=True)
    row = tiles(menu)
    keys = [t.key for t in row]
    want = ["nr", "boost", "interlace", "frame_generation", "lens", "colour", "theme", "fps_overlay"]
    if keys != want:
        failures.append(f"the tiles are {keys}, not {want}")
    by_key = {t.key: t for t in row}
    if not failures:
        boost, tile = by_key["boost"], by_key["interlace"]
        if tile.rect.top != boost.rect.top or tile.rect.left <= boost.rect.left:
            failures.append(f"Interlace at {tile.rect} is not beside Boost at {boost.rect}")
        if menu._activate_item(tile) != [("toggle", "interlace")]:
            failures.append("the tile does not toggle interlacing")
        if tile.value != 1.0 or tile.extra.get("value_text") != "On":
            failures.append(f"on, the tile reads {tile.value} {tile.extra.get('value_text')}")
    off = next(t for t in tiles(menu_in("en", interlace=False)) if t.key == "interlace")
    if off.value != 0.0 or off.extra.get("value_text") != "Off":
        failures.append(f"off, the tile reads {off.value} {off.extra.get('value_text')}")

    for lang in i18n.STRINGS:
        menu = menu_in(lang, interlace=True)
        tile = next(t for t in tiles(menu) if t.key == "interlace")
        label = str(tile.extra.get("label", ""))
        # The font the tile draws its name in; a longer name is clipped.
        need = menu._small_font.size(label)[0]
        if need > tile.rect.width - 2 * menu._u(8):
            failures.append(f"{lang}: {label!r} needs {need} px, the tile has {tile.rect.width}")

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("OK: the Interlace tile stands beside Boost and fits in twelve languages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
