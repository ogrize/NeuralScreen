"""An open fold-out's frame holds exactly its own controls.

The groups on the main page were told apart by a hairline, and open ones ran
into each other. Each is now a card, and an open one has a frame:

1. every control of an open block lies inside its block's frame, and the next
   block starts below it;
2. the last block's frame stops above the capture buttons;
3. a closed block has no frame;
4. the card stands inside the panel, clear of its edge.

Run:  runtime\\python.exe tests\\test_fold_groups.py
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))
sys.path.insert(0, os.path.join(BASE, "tests"))

import pygame  # noqa: E402


def main() -> int:
    failures = []
    pygame.init()
    pygame.display.set_mode((64, 64))
    import overlay_ui as ui
    import settings_io
    from test_menu_folds import menu_with

    # FG and interlacing each have a block of their own, and nothing of theirs
    # is left in Network or above the blocks.
    def owner(key):
        menu = menu_with(settings_io.MENU_FOLDS, interlace=True, interlace_mode="every",
                         frame_generation=True)
        menu.layout(1920, 1080)
        head = None
        for it in menu.items:
            if it.kind == "fold":
                head = it.key
            elif it.key == key and it.kind != "tile":
                return head
        return "absent"

    for key, block in (("frame_multiplier:2", "fg"), ("fg_match_screen", "fg"),
                       ("fg_ui_protect", "fg"), ("interlace", "interlace"),
                       ("interlace_mode", "interlace"), ("interlace_clean", "interlace"),
                       ("interlace_blend", "interlace"), ("interlace_black", "interlace"),
                       ("frame_limit_mode", "net")):
        if owner(key) != block:
            failures.append(f"{key} is in {owner(key)}, not {block}")

    menu = menu_with(settings_io.MENU_FOLDS)
    menu.layout(1920, 1080)
    items = menu.items
    folds = [i for i, it in enumerate(items) if it.kind == "fold"]
    if not folds:
        failures.append("no fold-outs on the page")
    for n, at in enumerate(folds):
        head = items[at]
        if "body_h" not in head.extra:
            failures.append(f"open block {head.key} has no frame")
            continue
        bottom = head.rect.bottom + head.extra["body_h"]
        end = folds[n + 1] if n + 1 < len(folds) else None
        inside = items[at + 1:end] if end else []
        if end is None:
            # The last block: its controls up to the capture strip.
            for it in items[at + 1:]:
                if it.key == "screenshot":
                    if it.rect.top < bottom:
                        failures.append(f"the capture strip is inside {head.key}'s frame")
                    break
                inside.append(it)
        for it in inside:
            if it.rect.bottom > bottom + 1:
                failures.append(f"{it.kind} {it.key} sticks out of {head.key}'s frame")
        if end is not None and items[end].rect.top < bottom:
            failures.append(f"{items[end].key} starts inside {head.key}'s frame")
        out = menu._u(ui.FOLD_OUTSET)
        if head.rect.x - out < 0:
            failures.append(f"{head.key}'s card leaves the panel on the left")

    menu = menu_with(())
    menu.layout(1920, 1080)
    for it in menu.items:
        if it.kind == "fold" and "body_h" in it.extra:
            failures.append(f"closed block {it.key} has a frame")
    pygame.quit()

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
