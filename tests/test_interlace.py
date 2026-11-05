"""Interlace the screen: a switch of its own that also works with NR off.

The Network fold has "Interlace the screen". The picture itself is checked
on the worker by test_lens.py and test_interlace_weave.py; this is the control
plane:

1. the switch is in the Network fold and its command flips the config;
2. the LENS command carries it, and a worker is told about it even with the
   camera off;
3. NR OFF idles with the switch on as with it off: there is nothing to weave;
4. on asks for two passes and leaves Boost alone, off puts back the count;
5. the NR-runs picker reaches LENS and asks for the passes it needs;
6. NR passes is off the panel while interlacing, and the count follows the mode;
   Own set stays, with Boost off as well, except at Half-rate, and starting it
   changes nothing on screen: it seeds from the whole profile;
7. the third pass reaches LENS, asks for three passes, and is not offered at Half-rate;
8. the blend and black-line switches show only with interlacing on and change what
   LENS says, and the black lines keep NR OFF awake.

Run:  runtime\\python.exe tests\\test_interlace.py
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


def _sent_for(protocol, interlace, black):
    out = _Stdin()
    protocol.send_lens(types.SimpleNamespace(stdin=out), False, 110.0, interlace=interlace,
                       interlace_black=black)
    return out.sent


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
    menu.folds = {"net", "interlace"}
    menu.set_state({"interlace": False})
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "interlace"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "interlace")]:
        failures.append("the Network fold has no interlace switch")
    if "interlace" not in settings_io._BOOL_KEYS:
        failures.append("interlace is not validated as a boolean")

    worker = types.SimpleNamespace(stdin=_Stdin(), pid=1, poll=lambda: None)
    st = types.SimpleNamespace(cfg={}, display=disp, worker=worker, worker_failed=False,
                               paused=True, recorder=None, pending_shot=None,
                               nr_small=False, nr_passes=1, work_scale=0.5,
                               params={}, pending_apply=None)
    st.cfg["profile"] = "Natural"
    applied = []
    real_apply = pipeline.request_apply
    pipeline.request_apply = lambda st, *a: applied.append(a)
    if not pipeline.wants_low_cost_off(st):
        failures.append("with the switch off, NR OFF did not idle")
    real_save = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: True   # no config.json written here
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace"))
        on = (len(applied[-1]), st.nr_passes)
        sent = worker.stdin.sent
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace"))
        off = (len(applied[-1]), st.nr_passes)
        worker.stdin.sent = sent
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace"))
        worker.stdin.sent = sent
    finally:
        settings_io.save_menu_layout = real_save
        pipeline.request_apply = real_apply
    # Three arguments: scale, profile, params - a fourth would be a Boost change.
    if on != (3, 2):
        failures.append(f"on applied {on}, not two passes with Boost left alone")
    if off != (3, 1):
        failures.append(f"off applied {off}, not the count it found")
    if st.cfg.get("interlace") is not True:
        failures.append(f"the command left cfg={st.cfg}")
    if not pipeline.wants_low_cost_off(st):
        failures.append("with the switch on, NR OFF stayed awake for nothing")
    if not commands.lens_wanted({"interlace": True}):
        failures.append("a new worker is not told about the interlacing")
    if len(worker.stdin.sent) != struct.calcsize(protocol.LENS_FMT):
        failures.append(f"the switch sent {len(worker.stdin.sent)} bytes, not one LENS")
    else:
        field = float(int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent)[-1]) & ~1024)  # Match game frames
        if field != 3.0:
            failures.append(f"LENS carried extras={field}, not interlace and its default blend")
    menu.set_state({"interlace": False})
    menu.layout(1920, 1080)
    if any(i.key == "interlace_blend" for i in menu.items):
        failures.append("the blend switch shows with interlacing off")
    menu.set_state({"interlace": True, "interlace_blend": True})
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "interlace_blend"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "interlace_blend")]:
        failures.append("interlacing on shows no blend switch")
    settings_io.save_menu_layout = lambda st: True
    worker.stdin.sent = b""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace_blend"))
    finally:
        settings_io.save_menu_layout = real_save
    field = float(int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent)[-1]) & ~1024)  # Match game frames
    if field != 1.0 or st.cfg.get("interlace_blend") is not False:  # interlace alone
        failures.append(f"blend off sent interlace={field}, cfg {st.cfg.get('interlace_blend')}")
    menu.set_state({"interlace": True})
    menu.layout(1920, 1080)
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "interlace_black"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "interlace_black")]:
        failures.append("interlacing on shows no black-line switch")
    settings_io.save_menu_layout = lambda st: True
    worker.stdin.sent = b""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace_black"))
    finally:
        settings_io.save_menu_layout = real_save
    field = int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent)[-1])
    if not field & 64 or not field & 1:
        failures.append(f"the black lines did not reach LENS ({field})")
    if pipeline.wants_low_cost_off(st):
        failures.append("with black lines on, NR OFF would idle and show none")
    st.cfg["interlace"] = False
    if not pipeline.wants_low_cost_off(st):
        failures.append("with interlacing off, the black-line switch kept NR OFF awake")
    if int(struct.unpack(protocol.LENS_FMT,
                         _sent_for(protocol, False, True))[-1]) & 64:
        failures.append("black lines were asked for with interlacing off")
    st.cfg["interlace"] = True
    menu.folds = {"net", "interlace"}
    menu.set_state({"interlace": True, "nr_small": True})
    menu.layout(1920, 1080)
    if any(i.key == "nr_passes" for i in menu.items):
        failures.append("NR passes shows while interlacing")
    menu.set_state({"interlace": False})
    menu.layout(1920, 1080)
    if not any(i.key == "nr_passes" for i in menu.items):
        failures.append("NR passes is gone with interlacing off")
    menu.set_state({"interlace": True})
    menu.layout(1920, 1080)
    for boost, passes, shown in ((True, 2, True), (False, 2, True), (True, 1, False)):
        menu.set_state({"interlace": True, "nr_small": boost, "nr_passes": passes})
        menu.layout(1920, 1080)
        here = any(i.kind == "toggle" and i.key == "pass_params" for i in menu.items)
        if here != shown:
            failures.append(f"Boost {boost}, {passes} passes: Own set shown {here}, not {shown}")
    menu.set_state({"interlace": False, "nr_small": False, "nr_passes": 2})
    menu.layout(1920, 1080)
    if any(i.kind == "toggle" and i.key == "pass_params" for i in menu.items):
        failures.append("Own set shows with neither Boost nor interlacing, where nothing uses it")
    menu.set_state({"interlace": True, "nr_small": True, "nr_passes": 2})
    menu.layout(1920, 1080)
    seed = types.SimpleNamespace(cfg={"interlace": True}, display=disp, worker=None, nr_passes=2,
                                 nr_pass_params=None, worker_failed=False, work_scale=0.5,
                                 pending_apply=None,
                                 params={"style": 1, "intensity": 1.0, "local_tone": 0.5,
                                         "local_structure": 1.0, "skin_structure": -1.0})
    seed.cfg["profile"] = "Natural"
    pipeline.request_apply = lambda st, *a: None
    settings_io.save_menu_layout = lambda st: True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(seed, ("toggle", "pass_params"))
    finally:
        settings_io.save_menu_layout = real_save
        pipeline.request_apply = real_apply
    if not seed.nr_pass_params or seed.nr_pass_params.get("local_tone") != 0.5:
        failures.append(f"Own set seeded {seed.nr_pass_params}: its tone would split the rows")
    for extra, want in (({"nr_passes": 4}, 2), ({"nr_passes": 4, "interlace_mode": "half"}, 1)):
        cfg = settings_io._validate_config(dict(settings_io._load_default_config(), interlace=True, **extra))
        if cfg["nr_passes"] != want:
            failures.append(f"{extra} loaded with {cfg['nr_passes']} passes, not {want}")
    cfg = settings_io._validate_config(dict(settings_io._load_default_config(), interlace=False, nr_passes=4))
    if cfg["nr_passes"] != 4:
        failures.append(f"with interlacing off the count changed to {cfg['nr_passes']}")
    if not any(i.kind == "segmented" and i.key == "interlace_mode" for i in menu.items):
        failures.append("interlacing on shows no NR-runs picker")
    applied.clear()
    pipeline.request_apply = lambda st, *a: applied.append(a)
    settings_io.save_menu_layout = lambda st: True
    worker.stdin.sent = b""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("interlace_mode", "half"))
            half_passes = st.nr_passes
            half_field = int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent[-struct.calcsize(protocol.LENS_FMT):])[-1])
            commands.apply_menu_action(st, ("interlace_mode", "alternate"))
            alt_passes = st.nr_passes
            alt_field = int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent[-struct.calcsize(protocol.LENS_FMT):])[-1])
            commands.apply_menu_action(st, ("interlace_mode", "nonsense"))
    finally:
        settings_io.save_menu_layout = real_save
        pipeline.request_apply = real_apply
    if (half_field >> 7) & 3 != 1 or half_passes != 1:
        failures.append(f"half-rate sent mode {(half_field >> 7) & 3} with {half_passes} passes")
    if (alt_field >> 7) & 3 != 2 or alt_passes != 2:
        failures.append(f"alternating sent mode {(alt_field >> 7) & 3} with {alt_passes} passes")
    if st.cfg.get("interlace_mode") != "alternate":
        failures.append("a mode that does not exist was taken")
    st.cfg["interlace_mode"] = "every"
    sw = next((i for i in menu.items if i.kind == "toggle" and i.key == "interlace_clean"), None)
    if sw is None or menu._activate_item(sw) != [("toggle", "interlace_clean")]:
        failures.append("interlacing on shows no third-pass switch")
    pipeline.request_apply = lambda st, *a: None
    settings_io.save_menu_layout = lambda st: True
    worker.stdin.sent = b""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            commands.apply_menu_action(st, ("toggle", "interlace_clean"))
    finally:
        settings_io.save_menu_layout = real_save
        pipeline.request_apply = real_apply
    field = int(struct.unpack(protocol.LENS_FMT, worker.stdin.sent)[-1])
    if not field & 512 or st.nr_passes != 3:
        failures.append(f"the third pass sent {field} with {st.nr_passes} passes")
    st.cfg["interlace_clean"] = False
    st.nr_passes = 2
    menu.set_state({"interlace_mode": "half"})
    menu.layout(1920, 1080)
    if any(i.key == "interlace_clean" for i in menu.items):
        failures.append("the third pass is offered at Half-rate, with nothing woven to clean")
    menu.set_state({"interlace_mode": "every"})
    menu.layout(1920, 1080)
    cfg = settings_io._validate_config(dict(settings_io._load_default_config(), interlace=True,
                                            interlace_clean=True, nr_passes=2))
    if cfg["nr_passes"] != 3:
        failures.append(f"a saved third pass loaded with {cfg['nr_passes']} passes")
    if "interlace_blend" not in settings_io._BOOL_KEYS:
        failures.append("interlace_blend is not validated as a boolean")
    pygame.quit()

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
