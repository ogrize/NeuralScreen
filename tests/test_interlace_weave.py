r"""Interlaced NR: pass 1 on the even lines, pass 2 on the odd, none black.

With "Interlace the screen" on and two passes, the worker runs both passes on
the whole frame and weaves their results. To tell the lines apart, pass 2 is
given a per-pass set with no strength at all, so on its lines the output stays
at the input, while pass 1 runs Natural.

Checked on the worker in the pixel channel, with Boost on and with it off:

* the even lines move away from the input further than the odd lines do;
* no line is black;
* with the switch off no such difference between the lines appears;
* Show black lines, on the network's input: the two fields swap between frames, and
  the network's result differs from the one it gives without them;
* NR runs: Every frame makes two passes a frame, Alternating one, Half-rate one on every
  other frame (with no Boost it has no composite to reuse and makes one a frame);
  Alternating keeps pass 1 on the even lines and pass 2 on the odd; a half-rate
  picture follows a moving input between its NR frames;
* Third pass: three passes a frame from Every frame and two from Alternating; it
  changes the picture, keeps its brightness, and blacks out no line;
* Blend the rows: the even lines keep 65% of pass 1's change and the odd take
  35% of it, so what tells them apart drops to about 30%.

Run:  runtime\\python.exe tests\\test_interlace_weave.py
"""
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import WORKER_EXE  # noqa: E402

FW, FH, W, H = 1280, 720, 832, 468
FRAMES = 10


def _frame() -> np.ndarray:
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[0:FH, 0:FW]
    g = (96 + 40 * np.sin(xx / 23.0) * np.cos(yy / 17.0)
         + rng.normal(0, 10, (FH, FW))).clip(0, 255).astype(np.uint8)
    f = np.zeros((FH, FW, 4), np.uint8)
    f[..., 0] = g
    f[..., 1] = (g * 0.9).astype(np.uint8)
    f[..., 2] = (g * 0.8).astype(np.uint8)
    f[..., 3] = 255
    return np.ascontiguousarray(f)


def _modes(img: np.ndarray, small: bool, mode: int, second_off: bool, frames: int = 40, moving: bool = False,
           clean: bool = False):
    """A run in one NR mode: the last output, its frame, the worker's pass count."""
    import protocol as pipe
    from pipeline import shutdown_worker, start_worker
    from settings_io import PROFILES

    os.environ["NS_NR_SMALL"] = "1" if small else "0"
    prm = dict(PROFILES["Natural"])
    second = dict(prm, intensity=0.0, local_tone=0.0, local_structure=0.0, skin_structure=0.0)
    worker, logs, reader, stop = start_worker(prm, W, H, 2, FW, FH, None)
    motion = np.zeros((H, W, 2), np.float16)
    out, shown, errors = None, None, []
    try:
        pipe.send_lens(worker, False, 110.0, interlace=True, interlace_mode=mode, interlace_clean=clean)
        pipe.send_resize(worker, prm, W, H, 2, FW, FH, nr_small=small,
                         nr_passes=1 if mode == 1 else 3 if clean else 2)
        reader.wait_rack(30)
        if second_off:
            pipe.send_per_pass(worker, second)
            reader.wait_per_pass(5)
        for i in range(frames):
            shown = np.ascontiguousarray(np.roll(img, i * 6, axis=1)) if moving else img
            pipe.send_frame(worker, i, shown, motion, i == 0, i, want_pixels=True)
            got = reader.recv(i, 20)
            if got is not None:
                out = got
                if i > frames // 2:
                    errors.append(float(np.abs(got[..., :3].astype(np.int16)
                                               - shown[..., :3].astype(np.int16)).mean()))
    finally:
        shutdown_worker(worker, stop)
    counted = [str(x) for x in logs if "NR passes" in str(x)]
    runs = int(counted[-1].split(";")[-1].split()[0]) if counted else -1
    return out, runs, (sum(errors) / len(errors) if errors else None)


def _run(interlace: bool, img: np.ndarray, small: bool, blend: bool = False, black: bool = False):
    import protocol as pipe
    from pipeline import shutdown_worker, start_worker
    from settings_io import PROFILES

    os.environ["NS_NR_SMALL"] = "1" if small else "0"
    prm = dict(PROFILES["Natural"])
    second = dict(prm, intensity=0.0, local_tone=0.0, local_structure=0.0, skin_structure=0.0)
    worker, logs, reader, stop = start_worker(prm, W, H, 2, FW, FH, None)
    motion = np.zeros((H, W, 2), np.float16)
    out = None
    try:
        # The switch first: what the resize builds depends on it without Boost.
        pipe.send_lens(worker, False, 110.0, interlace=interlace, interlace_blend=blend,
                       interlace_black=black)
        pipe.send_resize(worker, prm, W, H, 2, FW, FH, nr_small=small, nr_passes=2)
        reader.wait_rack(30)
        pipe.send_per_pass(worker, second)
        reader.wait_per_pass(5)
        for i in range(FRAMES):
            pipe.send_frame(worker, i, img, motion, i == 0, i, want_pixels=True)
            got = reader.recv(i, 20)
            if got is not None:
                out = got
    finally:
        shutdown_worker(worker, stop)
    return out, list(logs)


def _run_two(img: np.ndarray, small: bool):
    """The last two frames with both passes on the profile and the black lines on."""
    import protocol as pipe
    from pipeline import shutdown_worker, start_worker
    from settings_io import PROFILES

    os.environ["NS_NR_SMALL"] = "1" if small else "0"
    prm = dict(PROFILES["Natural"])
    worker, logs, reader, stop = start_worker(prm, W, H, 2, FW, FH, None)
    motion = np.zeros((H, W, 2), np.float16)
    last = before = None
    try:
        pipe.send_lens(worker, False, 110.0, interlace=True, interlace_black=True)
        pipe.send_resize(worker, prm, W, H, 2, FW, FH, nr_small=small, nr_passes=2)
        reader.wait_rack(30)
        for i in range(FRAMES + 2):
            pipe.send_frame(worker, i, img, motion, i == 0, i, want_pixels=True)
            got = reader.recv(i, 20)
            if got is not None:
                before, last = last, got
    finally:
        shutdown_worker(worker, stop)
    return last, before


def _moved(out: np.ndarray, img: np.ndarray, row: int) -> float:
    return float(np.abs(out[row::2, :, :3].astype(np.int16)
                        - img[row::2, :, :3].astype(np.int16)).mean())


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    os.environ["NS_HDR"] = "0"
    failures = []
    img = _frame()
    for small in (True, False):
        name = "Boost" if small else "no Boost"
        woven, logs = _run(True, img, small)
        plain, _ = _run(False, img, small)
        if woven is None or plain is None:
            print(f"FAIL: {name}: no pixels came back")
            return 1
        if not any("NR cascade built" in line and "effective=2" in line for line in logs):
            print(f"SKIP: {name}: the worker could not build two passes here")
            continue
        if not any("interlaced NR on" in line for line in logs):
            failures.append(f"{name}: the worker did not say it was weaving")
        even, odd = _moved(woven, img, 0), _moved(woven, img, 1)
        p_even, p_odd = _moved(plain, img, 0), _moved(plain, img, 1)
        darkest = float(woven[..., :3].mean(axis=(1, 2)).min())
        print(f"    {name}: woven even lines moved {even:.2f}, odd {odd:.2f}; "
              f"switch off even {p_even:.2f}, odd {p_odd:.2f}; darkest line {darkest:.0f}")
        if abs(even - odd) < 0.3:
            failures.append(f"{name}: the lines do not come from different passes "
                            f"(even {even:.2f}, odd {odd:.2f})")
        if abs(p_even - p_odd) > 0.2:
            failures.append(f"{name}: with the switch off the lines differ "
                            f"(even {p_even:.2f}, odd {p_odd:.2f})")
        if darkest < 40:
            failures.append(f"{name}: a line came out black ({darkest:.0f})")
        lined, before = _run_two(img, small)
        if lined is None or before is None:
            failures.append(f"{name}: no pixels came back with the black lines")
            continue
        row_mean = lambda o, r: float(o[r::2, :, :3].mean())  # noqa: E731
        now, then = (row_mean(lined, 0), row_mean(lined, 1)), (row_mean(before, 0), row_mean(before, 1))
        print(f"    {name}: black lines, input mean {img[..., :3].mean():.1f}: this frame even "
              f"{now[0]:.1f} odd {now[1]:.1f}; last frame even {then[0]:.1f} odd {then[1]:.1f}")
        if (now[0] < now[1]) == (then[0] < then[1]):
            failures.append(f"{name}: the darker lines did not swap between frames ({now}, {then})")
        for mode, label, want in ((0, "every frame", 2.0), (2, "alternating", 1.0),
                                  (1, "half-rate", 0.5 if small else 1.0)):
            _, runs, _ = _modes(img, small, mode, False)
            rate = runs / 40.0
            print(f"    {name}: {label}: {runs} NR passes over 40 frames ({rate:.2f} a frame)")
            if abs(rate - want) > 0.2:
                failures.append(f"{name}: {label} ran {rate:.2f} passes a frame, not {want}")
        alt, _, _ = _modes(img, small, 2, True)
        if alt is None:
            failures.append(f"{name}: alternating returned no pixels")
        else:
            a_even, a_odd = _moved(alt, img, 0), _moved(alt, img, 1)
            print(f"    {name}: alternating: even lines moved {a_even:.2f}, odd {a_odd:.2f}")
            if abs(a_even - a_odd) < 0.3:
                failures.append(f"{name}: alternating lost the rows ({a_even:.2f}, {a_odd:.2f})")
        _, _, every_err = _modes(img, small, 0, False, moving=True)
        _, _, half_err = _modes(img, small, 1, False, moving=True)
        print(f"    {name}: moving picture, error from the frame: every {every_err:.2f}, half-rate {half_err:.2f}")
        if small and half_err > every_err + 12.0:
            failures.append(f"{name}: half-rate left the moving picture behind ({half_err:.2f})")
        two, _, _ = _modes(img, small, 0, False)
        for mode, label, want in ((0, "every frame", 3.0), (2, "alternating", 2.0)):
            third, runs, _ = _modes(img, small, mode, False, clean=True)
            rate = runs / 40.0
            print(f"    {name}: third pass, {label}: {runs} NR passes over 40 frames ({rate:.2f} a frame)")
            if abs(rate - want) > 0.2:
                failures.append(f"{name}: third pass, {label}: {rate:.2f} passes a frame, not {want}")
            if third is None:
                failures.append(f"{name}: third pass, {label}: no pixels")
            elif mode == 0:
                changed = float(np.abs(third[..., :3].astype(np.int16) - two[..., :3].astype(np.int16)).mean())
                bright = float(third[..., :3].mean()) / float(two[..., :3].mean())
                dark = float(third[..., :3].mean(axis=(1, 2)).min())
                print(f"    {name}: third pass changes the picture by {changed:.2f}, brightness x{bright:.3f}, "
                      f"darkest line {dark:.0f}")
                if changed < 0.05:
                    failures.append(f"{name}: the third pass changed nothing ({changed:.2f})")
                if abs(bright - 1.0) > 0.03:
                    failures.append(f"{name}: the third pass moved the brightness x{bright:.3f}")
                if dark < 40:
                    failures.append(f"{name}: the third pass left a dark line ({dark:.0f})")
        blended, _ = _run(True, img, small, blend=True)
        if blended is None:
            failures.append(f"{name}: no pixels came back with the rows blended")
            continue
        # By the signed mean: the change of a pass, not how far the pixels moved.
        change = lambda o, r: float((o[r::2, :, :3].astype(np.float64)  # noqa: E731
                                     - img[r::2, :, :3]).mean())
        apart = abs(change(woven, 0) - change(woven, 1))
        mixed = abs(change(blended, 0) - change(blended, 1))
        ratio = mixed / apart if apart else 1.0
        print(f"    {name}: rows apart by {apart:.2f}, blended {mixed:.2f} ({ratio:.0%})")
        if apart < 0.3:
            print(f"SKIP: {name}: the passes hardly differ here, so blending shows nothing")
        elif not 0.2 <= ratio <= 0.4:
            failures.append(f"{name}: blending left {ratio:.0%} of the difference, not about 30%")
    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: pass 1 is on the even lines, pass 2 on the odd, and none is black")
    return 0


if __name__ == "__main__":
    sys.exit(main())
