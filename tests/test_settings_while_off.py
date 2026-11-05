"""With NR off, Boost and the sliders show and save at once.

The main loop applies nothing while the pipeline sleeps and the panel draws the
running values, so a Boost click with NR off did not show until NR came back.

1. a queued change reaches the state, the config and the environment a new
   worker reads, and it is saved, once;
2. the request stays queued, so the worker hears it when the pipeline wakes;
3. nothing is sent to the worker while it sleeps;
4. the main loop does this in the idle branch, before it draws the panel.

Run:  runtime\\python.exe tests\\test_settings_while_off.py
"""
import os
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))


class _NoWorker:
    def __getattr__(self, name):
        raise AssertionError(f"the sleeping worker was touched: {name}")


def main() -> int:
    failures = []
    import pipeline
    import settings_io

    saved = []
    real_save = settings_io.save_menu_layout
    settings_io.save_menu_layout = lambda st: saved.append(st.nr_small) or True
    params = {"intensity": 0.5}
    pending = (0.4, "Natural", params, True)
    st = types.SimpleNamespace(pending_apply=pending, work_scale=0.55, nr_small=False, params={},
                               cfg={"profile": "Strong", "nr_small": False}, worker=_NoWorker())
    try:
        pipeline.commit_queued_while_off(st)
        pipeline.commit_queued_while_off(st)
    finally:
        settings_io.save_menu_layout = real_save
    if st.nr_small is not True or st.cfg["nr_small"] is not True:
        failures.append(f"Boost did not take: {st.nr_small}, cfg {st.cfg['nr_small']}")
    if os.environ.get("NS_NR_SMALL") != "1":
        failures.append("a new worker would not read the new Boost state")
    if st.work_scale != 0.4 or st.cfg["profile"] != "Natural" or st.params != params:
        failures.append(f"the rest did not take: {st.work_scale} {st.cfg} {st.params}")
    if saved != [True]:
        failures.append(f"saved {len(saved)} times, not once: {saved}")
    if st.pending_apply is not pending:
        failures.append("the request left its slot: the worker would never hear it")

    source = (BASE / "main.py").read_text(encoding="utf-8")
    branch = source[source.index("if low_cost_off:"):]
    if branch.index("commit_queued_while_off") > branch.index("_service_idle_overlay()"):
        failures.append("the idle branch draws the panel before taking the change")

    for f in failures:
        print("  FAIL:", f)
    print("PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
