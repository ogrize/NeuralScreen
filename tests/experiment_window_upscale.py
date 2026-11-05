r"""An experiment, not a test of the suite: a small window enlarged to the screen.

A game in a smaller window really renders fewer pixels, which DLSS over a finished
frame cannot give it. This runs a stand-in game (a window whose pixel shader costs a
fixed amount per pixel, its frame rate following its size as a GPU-limited game's
does) and reports its frame rate alone and with the window enlarged to the monitor,
by DLSS and by a plain bilinear filter.

    python experiment_window_upscale.py GPULOAD.EXE RESULT_DIR [--iters N] [--show FILE]
                                        [--capped]

--show also puts the enlarged picture on the monitor for a moment and saves it.
--capped measures the stand-in at a frame limit instead: uncapped, a game takes the
whole GPU and the enlarged picture starves, however high the game's own rate reads.

The motion field here is zero: the frame rates do not depend on it. The picture in
motion is the real program's, with its optical flow.
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))

import numpy as np  # noqa: E402

MW, MH = 2560, 1440
user32 = ctypes.windll.user32


def find_window(timeout: float = 15.0) -> int:
    end = time.time() + timeout
    while time.time() < end:
        hwnd = user32.FindWindowW("NsGpuLoad", None)
        if hwnd:
            return hwnd
        time.sleep(0.1)
    raise RuntimeError("the load window never appeared")


CAP = [0]
NR = [False]


def load(exe, w, h, iters, seconds, result):
    if result.exists():
        result.unlink()
    return subprocess.Popen([exe, str(w), str(h), str(iters), str(seconds), str(result), str(CAP[0])])


def read_fps(proc, result) -> float:
    proc.wait(timeout=120)
    return float(result.read_text().strip())


def alone(exe, w, h, iters, seconds, result) -> float:
    return read_fps(load(exe, w, h, iters, seconds, result), result)


def enlarged(exe, w, h, iters, seconds, result, filt, show=None):
    from pipeline import shutdown_worker, start_worker
    from protocol import send_frame, send_motion_size, send_wgc, send_window
    from settings_io import PROFILES

    os.environ["NS_WINDOW_UPSCALE"] = "monitor" if show else f"{MW}x{MH}"
    os.environ["NS_WINDOW_UPSCALE_FILTER"] = filt
    os.environ.pop("NS_CLIENT_PID", None)   # no focus rule: nothing here takes the focus
    os.environ["NS_NR_SMALL"] = "0"
    os.environ["NS_HDR"] = "0"
    if show:
        os.environ["NS_WINDOW_POS"] = "0,0"
    else:
        os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    proc = load(exe, w, h, iters, seconds, result)
    hwnd = find_window()
    worker, logs, reader, stop = start_worker(dict(PROFILES["Natural"]), w, h, 2, 0, 0, None)
    gw, gh = 160, 90
    motion = np.zeros((gh, gw, 2), np.float16)
    frames, shown, measured = 0, 0, 0.0
    miss = None
    try:
        send_wgc(worker, hwnd)
        reader.wait_wgak(15)
        send_motion_size(worker, gw, gh)
        reader.wait_mack(10)
        send_window(worker, w, h)
        reader.wait_wack(10)
        start = time.perf_counter()
        i = 0
        while proc.poll() is None:
            send_frame(worker, i, None, motion, i == 0, i, no_color=True, motion_small=True, bypass=not NR[0])
            reader.recv(i, timeout=10.0)
            i += 1
            now = time.perf_counter() - start
            if now > 3.0:
                if frames == 0:
                    measured = time.perf_counter()
                frames += 1
            if show and shown == 0 and now > 5.0:
                from PIL import ImageGrab
                ImageGrab.grab(bbox=(0, 0, MW, MH)).resize((960, 540)).save(show)
                # What a click at the middle of the picture would land on.
                point = ctypes.wintypes.POINT(MW // 2, MH // 2)
                under = user32.WindowFromPoint(point)
                miss = under != hwnd
                shown = 1
        rate = frames / (time.perf_counter() - measured) if frames else 0.0
    finally:
        shutdown_worker(worker, stop)
        if proc.poll() is None:
            proc.kill()
    text = [str(line) for line in logs]
    notes = [line.strip()[-80:] for line in text if "[dlss]" in line]
    return read_fps(proc, result), rate, notes, miss


def main() -> int:
    import ctypes.wintypes  # noqa: F401 - POINT
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("out", type=Path)
    ap.add_argument("--iters", type=int, default=0)
    ap.add_argument("--show")
    ap.add_argument("--capped", action="store_true")
    ap.add_argument("--nr", action="store_true", help="also run the neural pass on the window")
    args = ap.parse_args()
    result = args.out / "load.txt"
    NR[0] = args.nr
    iters = args.iters
    if iters == 0:
        # A load that a 2560x1440 window runs at about 40 fps, as a heavy game would.
        iters = 200
        for _ in range(6):
            fps = alone(args.exe, MW, MH, iters, 3, result)
            print(f"    calibrating: {iters} iterations -> {fps:.0f} fps at {MW}x{MH}", flush=True)
            if 34 <= fps <= 48:
                break
            iters = max(1, int(iters * fps / 40))
    print(f"RESULT load: {iters} iterations", flush=True)
    if args.capped:
        print(f"RESULT {MW}x{MH} alone, uncapped: {alone(args.exe, MW, MH, iters, 10, result):.1f} fps", flush=True)
        for w, h, cap in ((1706, 960, 24), (1706, 960, 30), (1280, 720, 30), (1280, 720, 40), (1280, 720, 50)):
            CAP[0] = cap
            for filt in ("dlss", "bilinear"):
                game, rate, _, _ = enlarged(args.exe, w, h, iters, 10, result, filt)
                print(f"RESULT {w}x{h} capped {cap} + {filt}: game {game:.1f} fps, "
                      f"picture updated {rate:.1f} /s", flush=True)
        return 0
    print(f"RESULT {MW}x{MH} alone: {alone(args.exe, MW, MH, iters, 10, result):.1f} fps", flush=True)
    for w, h in ((1706, 960), (1280, 720), (852, 480)):
        print(f"RESULT {w}x{h} alone: {alone(args.exe, w, h, iters, 10, result):.1f} fps", flush=True)
        for filt in ("dlss", "bilinear"):
            game, rate, notes, _ = enlarged(args.exe, w, h, iters, 10, result, filt)
            print(f"RESULT {w}x{h} + {filt}: game {game:.1f} fps, picture updated {rate:.1f} /s; {notes[-1:]}",
                  flush=True)
    if args.show:
        _, _, _, miss = enlarged(args.exe, 1280, 720, iters, 8, result, "dlss", show=args.show)
        print(f"RESULT click at the picture's middle misses the small window: {miss}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
