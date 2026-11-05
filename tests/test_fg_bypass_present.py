"""Frame Generation must run on the bypass present path (issue #104).

A user turned Neural Rendering off and left Frame Generation on. The switch
reached the worker (`[fg] UI: on, 2x` is printed by the worker itself) and the
frame counter kept climbing, but not one line of the presenter's life ever
appeared: no `[fg] Init_Ext`, no `[fg] 2x enabled at`, no `[fg] displayed`.
The package is unambiguous - 3 `[fg]` lines, all three the switch state, 0
lifecycle lines, and 0 failure lines as well: it did not try and fail, it
never started.

The cause was in the presenter path, not in the multiplier or the card:

* `PresentBypass()` called `StopFgPresentation()` unconditionally. That joins
  the presenter thread AND clears `g_fg.history` (`frame_generation.inl`), so
  the runtime was torn down on every bypass frame;
* `g_fg_reset` contained `|| bypass`, so even a live presenter was told to
  reset every frame - and `interpolate` is false whenever `g_fg_reset` is.

Checked here, as source facts (no GPU needed):

1. `PresentBypass()` asks `FgRequested()` and hands the frame to `FgPresent()`,
   and it asks BEFORE any `StopFgPresentation()` - otherwise the presenter is
   stopped before it is ever consulted;
2. the `g_fg_reset` assignment has no `bypass` term: a per-frame reset on a
   whole mode is what made the runtime interpolate nothing. The switch between
   sources must still reset, once;
3. `FRAME_FLAG_BYPASS` is still in the `defer_tail` mask. This one guards the
   opposite direction: pulling bypass into the defer-tail token contract would
   make the worker exit with code 9 and send main into a restart loop;
4. `FgPresent()` picks its export source by `bypass` - the screen shows the raw
   capture on that path, so the export must not keep sending the stale neural
   frame.

Each check is pinned to the line that caused the bug, so each one is provable
by mutation: restore the unconditional stop, put `|| bypass` back, drop the
flag from the mask, or hardcode `v.output`, and the matching check goes red.

The HDR presenter carries the same `&& !bypass` term; it is closed by its own
change, and the honest verification of both needs a GPU (a whole session with
NR off and FG on, asserting `[fg] Init_Ext` and `[fg] displayed` appear) - that
end-to-end check lives in test_frame_generation.py.

Run:  runtime\\python.exe tests\\test_fg_bypass_present.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "native" / "dlss5-feed-host64.cpp"
FG = ROOT / "native" / "frame_generation.inl"
HDR = ROOT / "native" / "hdr_present.inl"


def without_comments(text: str) -> str:
    """Drop C++ comments so a term is only found where it is code.

    The comments around these very changes quote the calls they removed
    (`StopFgPresentation()`, `|| bypass`), and reading them as code reports the
    fix as the bug.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def body_after(text: str, marker: str) -> str:
    """The text from `marker` to the next definition of the same kind.

    A slice, not a parse: the checks below are about the order of two calls
    inside one function and about terms inside one initialiser, and both are
    only meaningful inside their own scope.
    """
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker):]
    nxt = re.search(r"\nstatic [A-Za-z_].*\(", rest)
    return rest[:nxt.start()] if nxt else rest


def assignment(text: str, name: str) -> str:
    """The right-hand side of an assignment to `name`, declaration included.

    `framegen` is a `const bool` declared once per call, and that declaration IS
    the rule under test; `g_fg_reset` is declared once and assigned once per
    frame, and there the first occurrence is the declaration. Callers choose
    the helper that reads the occurrence they mean.
    """
    m = re.search(re.escape(name) + r"\s*=(.*?);", text, re.S)
    return m.group(1) if m else ""


def later_assignment(text: str, name: str) -> str:
    """The right-hand side of the LAST assignment - the rule, not the default.

    `static bool g_fg_reset = true;` is the initial value; the per-frame rule
    that decides whether FG resets is the later assignment. Reading the
    declaration would test the wrong line.
    """
    hits = list(re.finditer(re.escape(name) + r"\s*=(.*?);", text, re.S))
    return hits[-1].group(1) if hits else ""


def main() -> int:
    failures = []
    host = without_comments(HOST.read_text(encoding="utf-8", errors="replace"))
    fg = without_comments(FG.read_text(encoding="utf-8", errors="replace"))
    hdr = without_comments(HDR.read_text(encoding="utf-8", errors="replace"))
    if not host or not fg or not hdr:
        print("FAIL: the native sources are not readable")
        return 1

    # --- 1. the bypass path consults FG before stopping it -----------------
    bypass = body_after(host, "static bool PresentBypass(VideoState &v)")
    if not bypass:
        failures.append("PresentBypass() is gone - the bypass path cannot be "
                        "checked at all")
    else:
        asked = bypass.find("FgRequested()")
        stopped = bypass.find("StopFgPresentation()")
        if asked < 0:
            failures.append("PresentBypass() never asks FgRequested(): Frame "
                            "Generation cannot run while NR is off (#104)")
        if "FgPresent(" not in bypass:
            failures.append("PresentBypass() never calls FgPresent(): the "
                            "presenter is not started on the bypass path, so "
                            "no '[fg] Init_Ext' can ever be logged there")
        # A gate is what makes the call reachable; an unconditional stop before
        # it means the presenter is torn down and history is cleared per frame.
        if asked >= 0 and stopped >= 0 and stopped < asked:
            failures.append("PresentBypass() stops the presenter before asking "
                            "whether FG is wanted - the gate is unreachable")
        if not re.search(r"if\s*\(!\s*framegen\s*\)\s*StopFgPresentation\(\)",
                         bypass):
            failures.append("the StopFgPresentation() in PresentBypass() is "
                            "still unconditional: the presenter is joined and "
                            "its history cleared on every bypass frame")

    # --- 2. no per-frame reset on the bypass mode --------------------------
    reset = later_assignment(host, "g_fg_reset")
    if not reset:
        failures.append("the g_fg_reset assignment is gone - the reset rule "
                        "cannot be checked")
    else:
        if re.search(r"\bbypass\b", reset):
            failures.append("g_fg_reset still resets on the bypass MODE: it is "
                            "true on every bypass frame, so FG is told to "
                            "reset every frame and interpolates nothing (#104)")
        if "fg_source_switch" not in reset:
            failures.append("nothing detects the switch between the neural "
                            "result and the raw capture: FG history survives "
                            "the change of source and the first frames after "
                            "NR comes back are garbage")

    # --- 3. defer_tail must keep bypass out of the token contract ----------
    defer = later_assignment(host, "defer_tail")
    if not defer:
        failures.append("the defer_tail decision is gone")
    elif "FRAME_FLAG_BYPASS" not in defer.rsplit("== 0", 1)[0]:
        failures.append("FRAME_FLAG_BYPASS left the defer_tail mask: on bypass "
                        "the presenter would be asked for a token it never "
                        "writes, and 'tail token order failed' exits the "
                        "worker with code 9 (a restart loop)")

    # --- 4. the export follows the presented source ------------------------
    present = body_after(fg, "static bool FgPresent(VideoState &v, ID3D12Resource *color,")
    if not present:
        failures.append("FgPresent() is gone - the export source cannot be "
                        "checked")
    else:
        if "bool bypass" not in present.split("{")[0]:
            failures.append("FgPresent() takes no `bypass`: it cannot tell "
                            "which frame the screen is showing")
        # The choice lives in ExportFgSource, shared by the frame FG takes
        # and the one it holds (#132); both must hand it `bypass`.
        export = body_after(fg, "static void ExportFgSource(VideoState &v, bool bypass)")
        hold = body_after(fg, "static bool FgHold(VideoState &v, bool bypass)")
        if ("v.color.tex : v.output" not in export
                or "ExportFgSource(v, bypass)" not in present
                or "ExportFgSource(v, bypass)" not in hold):
            failures.append("the export source does not follow `bypass`: on the "
                            "bypass path the export keeps sending the stale "
                            "neural frame that the screen is not showing")

    # --- 5. the HDR presenter does not exclude bypass ----------------------
    hdr_path = assignment(hdr, "framegen")
    if not hdr_path:
        failures.append("the HDR presenter no longer computes `framegen` - the "
                        "HDR bypass path cannot be checked")
    elif re.search(r"!\s*bypass", hdr_path):
        failures.append("the HDR presenter still excludes bypass from FG "
                        "(`framegen = FgRequested() && !bypass`): on an HDR "
                        "capture the presenter stays off with NR off")
    if "FgPresent(v, g_hdr_output, D3D12_RESOURCE_STATE_COMMON, bypass)" not in hdr:
        failures.append("the HDR path does not tell the presenter that it is "
                        "presenting the raw capture: the export would keep "
                        "sending the stale neural frame")

    # --- it compiles -------------------------------------------------------
    vswhere = Path(os.environ.get("ProgramFiles(x86)",
                                  r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio/Installer/vswhere.exe"
    try:
        install = subprocess.check_output(
            [str(vswhere), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            text=True, timeout=120).strip()
    except Exception:
        install = ""

    if not install:
        print("SKIP: the compiler was not found; the compile check did not run")
    else:
        vcvars = Path(install) / "VC/Auxiliary/Build/vcvars64.bat"
        # A temp directory, not repo/_work: that folder is gitignored scratch
        # that a disk cleanup may remove, and an object file is exactly what
        # tempfile is for. Writing here needed _work to exist, so the check
        # failed with C1083 on a tree where the cleanup had taken it.
        with tempfile.TemporaryDirectory(prefix="ns-compile-") as scratch:
            out_obj = Path(scratch) / "nvngx_fg_bypass.obj"
            command = (f'"{vcvars}" >nul && cl /nologo /c /O2 /EHsc /W3 /MD '
                       f'/std:c++17 /Iinclude /Isrc "{HOST}" /Fo:"{out_obj}"')
            result = subprocess.run(
                'cmd /d /s /c "' + command + '"', cwd=str(ROOT / "native"),
                capture_output=True, text=True, encoding="cp866",
                errors="replace", timeout=540)
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0 or "error C" in combined:
                failures.append("the worker does not compile:\n" +
                                "\n".join(l for l in combined.splitlines()
                                          if "error" in l.lower())[:500])

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: Frame Generation runs on the bypass path - the presenter is "
          "asked for, not stopped, and its history resets on the source "
          "switch instead of every frame")
    return 0


if __name__ == "__main__":
    sys.exit(main())
