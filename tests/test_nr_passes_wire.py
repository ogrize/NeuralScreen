r"""The NR cascade: the count reaches the worker, and lands in the right buffer.

Three separate things can silently undo the cascade, and none of them shows up
as an error:

1. THE WIRE. The pass count rides in bits 2-4 of the RNSZ flags word - a field
   squeezed into an existing struct because the layout mirrors the D5V3 header
   and a hundred tests build it by position. Both ends have to agree on the
   shift, the mask and the ceiling, and they are written out twice: once in
   protocol.py and once in the worker's C++.

2. THE HAND-OFF. Every RNSZ has to carry the count, and the stream header has
   no room for one at all - so a saved `nr_passes` reaches the worker only
   when something sends a resize. A config asking for two passes showed "2" in
   the panel while one pass ran, because nothing sent an RNSZ at startup
   (audit 20.09).

3. WHERE THE LAST PASS LANDS. Everything downstream - the residual composite,
   the scale-up, the present - reads `v.nr_out`. The cascade ping-pongs
   between two scratch buffers, so pass 0 has to start on whichever one makes
   the parity come out right. The other way to get there is to let it land
   wherever and swap the two pointers afterwards, and that is a trap: it
   changes WHICH resource v.nr_out is from frame to frame, and both composites
   cache their descriptors on exactly that pointer. An even pass count would
   rewrite a shader-visible descriptor heap every frame while up to two
   earlier frames are still reading it.

Run:  runtime\python.exe tests\test_nr_passes_wire.py
"""
from __future__ import annotations

import io
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))  # the modules live in app/

import protocol  # noqa: E402


class _Sink:
    """A worker that only remembers what was written to it."""

    def __init__(self):
        self.blob = b""
        self.stdin = self

    def write(self, data):
        self.blob += data

    def flush(self):
        pass


PARAMS = {"style": 1, "auto_mask": 1, "intensity": 0.5, "local_tone": 0.0,
          "local_structure": 0.0, "skin_structure": 0.0}


def _flags_for(passes, small=False, direct=False) -> int:
    w = _Sink()
    protocol.send_resize(w, PARAMS, 1920, 1080, 1, 2560, 1440,
                         nr_small=small, nr_direct=direct, nr_passes=passes)
    return struct.unpack(protocol.RESIZE_FMT, w.blob)[4]


def main() -> int:
    failures: list[str] = []

    # 1. The field itself, and what it does to its neighbours.
    shift = protocol.RESIZE_FLAG_NR_PASSES_SHIFT
    mask = ((protocol.NR_MAX_PASSES << shift)
            | ((protocol.NR_MAX_PASSES - 1) << shift))
    for asked, expect in ((1, 1), (2, 2), (3, 3), (4, 4),
                          # Outside the range, clamped rather than wrapped: a
                          # zero read as "no passes" would stop the network,
                          # and a five would index past g_nr_pass[].
                          (0, 1), (-3, 1), (9, 4)):
        got = (_flags_for(asked) & mask) >> shift
        if got != expect:
            failures.append(f"nr_passes={asked} went out as {got}, not {expect}")

    for small in (False, True):
        for direct in (False, True):
            flags = _flags_for(3, small, direct)
            if bool(flags & protocol.RESIZE_FLAG_NR_SMALL) != small:
                failures.append("the pass count trampled the NR_SMALL bit")
            if bool(flags & protocol.RESIZE_FLAG_NR_DIRECT) != direct:
                failures.append("the pass count trampled the NR_DIRECT bit")

    # 2. Both ends of the wire agree. The worker is the only reader of these
    # bits and it was written from the same three numbers, by hand.
    cpp = io.open(ROOT / "native" / "dlss5-feed-host64.cpp",
                  encoding="utf-8", errors="surrogateescape").read()

    def _const(name: str) -> int | None:
        m = re.search(r"%s\s*=\s*(0x[0-9A-Fa-f]+|\d+)u?;" % name, cpp)
        return int(m.group(1), 0) if m else None

    c_shift = _const("RESIZE_FLAG_NR_PASSES_SHIFT")
    c_mask = _const("RESIZE_FLAG_NR_PASSES_MASK")
    c_max = _const("NR_MAX_PASSES")
    if c_shift != shift:
        failures.append(f"the worker shifts the pass count by {c_shift}, "
                        f"protocol.py by {shift}")
    if c_max != protocol.NR_MAX_PASSES:
        failures.append(f"the worker caps the cascade at {c_max}, "
                        f"protocol.py at {protocol.NR_MAX_PASSES}")
    if c_mask is not None and c_shift is not None and c_max is not None:
        # The mask has to be wide enough for the ceiling and no wider - a mask
        # one bit short reads 4 passes as 0, which is one pass.
        if (c_max << c_shift) & c_mask != (c_max << c_shift):
            failures.append(f"the worker's mask 0x{c_mask:X} cannot hold "
                            f"{c_max} passes at shift {c_shift}")

    # 3. Every live RNSZ carries the count. Two call sites, and a third added
    # later without the argument would quietly pin the worker at one pass.
    pipe = io.open(ROOT / "app" / "pipeline.py", encoding="utf-8").read()
    calls = [m for m in re.finditer(r"send_resize\((?:[^()]|\([^()]*\))*\)",
                                    pipe, re.S)]
    if not calls:
        failures.append("no send_resize call found in pipeline.py - this test "
                        "has stopped looking at anything")
    for m in calls:
        if "nr_passes" not in m.group(0):
            line = pipe[:m.start()].count("\n") + 1
            failures.append(f"pipeline.py:{line}: this RNSZ does not carry the "
                            f"pass count, so it resets the cascade to one")

    # 4. And the saved count reaches a worker that has just started. The stream
    # header has no field for it, so this is the only way it can.
    mainsrc = io.open(ROOT / "main.py", encoding="utf-8").read()
    if not re.search(r"nr_passes[^\n]*>\s*1", mainsrc):
        failures.append("main.py never sends the saved pass count: the worker "
                        "starts at one pass and the panel would show another")
    # And to EVERY worker, not only the first. Sending it once at startup left
    # every restart - a revive after a crash, a manual revive, a rebuild -
    # running one pass while the panel still said four, with no line about it.
    # Measured 20.09.2026: killing the worker of a four-pass session took the
    # rate from 28 to 91 fps and the log said nothing at all. The hand-off has
    # to be keyed on the worker's identity, not fired once.
    if "nr_passes_pid" not in mainsrc:
        failures.append(
            "main.py does not key the cascade hand-off on the worker: a "
            "restarted worker comes back at one pass and nothing says so")
    if not re.search(r"nr_passes_pid[^\n]*!=\s*st\.worker\.pid", mainsrc):
        failures.append(
            "the hand-off is not compared against the live worker's pid, so "
            "it cannot notice a new worker")

    # 5. The parity, not a swap. Read as source because no Python test can see
    # a D3D12 descriptor being rewritten under a frame that is still running.
    if re.search(r"v\.nr_out\s*=\s*v\.nr_alt", cpp):
        failures.append(
            "the worker swaps v.nr_out and v.nr_alt: that moves the pointer "
            "the composites cache their descriptors on, and an even pass "
            "count then rewrites a shader-visible heap every frame while "
            "earlier frames are still reading it")
    # Interlace's weave lands its pass in nr_out too: the same parity, one more case.
    if ("((passes & 1u) != 0u) ? v.nr_out : v.nr_alt" not in cpp
            and "((passes & 1u) != 0u || weave) ? v.nr_out : v.nr_alt" not in cpp):
        failures.append(
            "the cascade no longer picks its first buffer by parity - the "
            "last pass has to land in v.nr_out without anything moving")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the pass count survives the wire, both ends agree on the bits, "
          "every resize carries it, and the last pass lands in nr_out without "
          "a pointer moving")
    return 0


if __name__ == "__main__":
    sys.exit(main())
