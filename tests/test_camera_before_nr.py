r"""The camera runs without NR, and with NR it runs once, before the network.

Control plane: NR OFF stays awake while the camera is on, and idles without it.

On the worker, with Boost on and off, a ramp through a 110-degree fisheye:

* NR off: the output is the bent picture;
* NR off with the before/after wipe at 50% and the colour on: left of it the
  picture is the input, bit for bit, right of it the colour has changed it;
* NR on, at zero strength so the network leaves the picture alone: the worker
  says the camera runs before NR, and the output is that same bent picture. No
  camera would leave it unbent, and the pass run again would bend it twice.

Run:  runtime\\python.exe tests\\test_camera_before_nr.py
"""
import os
import sys
import types
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


def _ramp() -> np.ndarray:
    yy, xx = np.indices((FH, FW), dtype=np.float64)
    f = np.empty((FH, FW, 4), np.uint8)
    f[..., 0] = np.round(xx * 255.0 / (FW - 1))
    f[..., 1] = np.round(yy * 255.0 / (FH - 1))
    f[..., 2] = 128
    f[..., 3] = 255
    return np.ascontiguousarray(f)


def _run(img: np.ndarray, small: bool):
    import protocol as pipe
    from pipeline import shutdown_worker, start_worker
    from settings_io import PROFILES

    os.environ["NS_NR_SMALL"] = "1" if small else "0"
    prm = dict(PROFILES["Natural"], intensity=0.0, local_tone=0.0, local_structure=0.0,
               skin_structure=0.0)
    worker, logs, reader, stop = start_worker(prm, W, H, 2, FW, FH, None)
    motion = np.zeros((H, W, 2), np.float16)
    bent = with_nr = None
    index = 0
    try:
        pipe.send_resize(worker, prm, W, H, 2, FW, FH, nr_small=small, nr_passes=1)
        reader.wait_rack(30)
        pipe.send_lens(worker, True, 110.0, bend=True, noise=0.0)
        for bypass in (True, False):
            for _ in range(FRAMES):
                pipe.send_frame(worker, index, img, motion, index == 0, index,
                                want_pixels=True, bypass=bypass)
                got = reader.recv(index, 20)
                index += 1
                if got is not None:
                    if bypass:
                        bent = got
                    else:
                        with_nr = got
    finally:
        shutdown_worker(worker, stop)
    return bent, with_nr, list(logs)


def _wipe() -> tuple:
    import protocol as pipe
    from pipeline import shutdown_worker, start_worker
    from settings_io import PROFILES

    os.environ["NS_NR_SMALL"] = "1"
    prm = dict(PROFILES["Natural"])
    worker, logs, reader, stop = start_worker(prm, W, H, 2, FW, FH, None)
    motion = np.zeros((H, W, 2), np.float16)
    grey = np.full((FH, FW, 4), 100, np.uint8)
    grey[..., 3] = 255
    grey = np.ascontiguousarray(grey)
    out = None
    try:
        pipe.send_lens(worker, False, 110.0, exposure=1.0)
        for i in range(4):
            pipe.send_frame(worker, i, grey, motion, i == 0, i, want_pixels=True,
                            bypass=True, split=0.5)
            got = reader.recv(i, 20)
            if got is not None:
                out = got
    finally:
        shutdown_worker(worker, stop)
    return grey, out


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a[..., :2].astype(np.int16) - b[..., :2].astype(np.int16)).mean())


def main() -> int:
    failures = []
    import pipeline

    st = types.SimpleNamespace(paused=True, cfg={"lens": True}, recorder=None, pending_shot=None)
    if pipeline.wants_low_cost_off(st):
        failures.append("with the camera on, NR OFF would idle and show nothing")
    st.cfg["lens"] = False
    if not pipeline.wants_low_cost_off(st):
        failures.append("with the camera off, NR OFF did not idle")

    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
    else:
        os.environ["NS_HDR"] = "0"
        grey, wiped = _wipe()
        if wiped is None:
            failures.append("the wipe: no pixels came back")
        else:
            left = wiped[:, : FW // 2 - 4, :3]
            right = wiped[:, FW // 2 + 8:, :3]
            print(f"    wipe with NR off: left {left.mean():.1f}, right {right.mean():.1f} "
                  f"(input {grey[..., :3].mean():.0f})")
            if not np.array_equal(left, grey[:, : FW // 2 - 4, :3]):
                failures.append("the wipe: left of it is not the input")
            if right.mean() < grey[..., :3].mean() + 20:
                failures.append("the wipe: right of it the colour did not show")
        img = _ramp()
        for small in (True, False):
            name = "Boost" if small else "no Boost"
            bent, with_nr, logs = _run(img, small)
            if bent is None or with_nr is None:
                failures.append(f"{name}: no pixels came back")
                continue
            to_bent, to_flat, flat = _diff(with_nr, bent), _diff(with_nr, img), _diff(bent, img)
            print(f"    {name}: NR frame vs bent {to_bent:.2f}, vs unbent {to_flat:.2f}; "
                  f"bent vs unbent {flat:.2f}")
            if not any("the camera runs before NR" in line for line in logs):
                failures.append(f"{name}: the worker did not run the camera before NR")
            if flat < 3.0:
                failures.append(f"{name}: with NR off the camera did not bend the picture")
            if to_bent > 1.0:
                failures.append(f"{name}: the NR frame is not the once-bent picture "
                                f"({to_bent:.2f} from it, {flat:.2f} between the two)")
    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the camera runs with NR off, and with NR on once, before the network")
    return 0


if __name__ == "__main__":
    sys.exit(main())
