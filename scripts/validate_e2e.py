"""Progressive end-to-end validation: record a real flow autonomously, then REPLAY it on-device
and report honestly. Pinned to the S23 by model. The FOUNDATION test (record -> replay reproduces
the capture) across a difficulty ladder.

    ANDROID_SERIAL=192.168.1.192:45307 uv run python scripts/validate_e2e.py <tier>
"""
from __future__ import annotations

import sys
import time

REQUIRED_MODEL = "918"


def _guard(d):
    m = str(d.device_info.get("model", ""))
    assert REQUIRED_MODEL in m, f"ABORT: device is {m!r}, not the S23"
    return m


def open_drawer(d):
    d.press("home"); time.sleep(0.8)
    d.swipe(0.5, 0.9, 0.5, 0.2, 0.2); time.sleep(1.5)


def scroll_to_icon(d, label) -> bool:
    # Samsung's app drawer paginates HORIZONTALLY — swipe left through pages, then fall back to a
    # vertical scroll (other launchers). Bounded so a missing icon can't loop forever.
    if d(text=label).exists:
        return True
    for _ in range(6):
        d.swipe(0.85, 0.5, 0.15, 0.5, 0.15); time.sleep(0.6)
        if d(text=label).exists:
            return True
    try:
        d(scrollable=True).scroll.to(text=label)
    except Exception:
        pass
    return d(text=label).exists


def record(name, pkg, icon_label, flow, settle_wait=3.0, settle_after=1.6):
    """Launch `pkg` fresh from the drawer (icon_label), record the in-app flow. Returns (graph, path)."""
    from scripts.auto_record import AutoRecorder
    rec = AutoRecorder(settle_wait=settle_wait, settle_after_tap=settle_after)
    _guard(rec.d)
    if pkg:
        rec.d.shell(f"am force-stop {pkg}")
    open_drawer(rec.d)
    if not scroll_to_icon(rec.d, icon_label):
        print(f"  [skip] icon {icon_label!r} not found in drawer")
        return None, None
    rec.start()                              # anchor on the launcher/drawer (homescreen)
    rec.step(("open", icon_label))           # launcher -> app first screen: am_start anchor (RULE 2)
    for s in flow:
        try:
            rec.step(s)
        except Exception as e:
            print(f"  [flow] step {s} failed: {type(e).__name__} — stopping flow early")
            break
    path = f"/tmp/e2e_{name}.json"
    g = rec.session.graph
    g.save(path)
    anc = [g.screen(a).namespace for a in g.anchors()]
    print(f"  recorded: {g.g.number_of_nodes()} screens, {g.g.number_of_edges()} edges, anchors={anc}")
    return g, path


def replay(name, path, pkg):
    """Cold-start the app and faithfully replay the recording via the engine + U2Driver."""
    import uiautomator2 as u2
    from wendle.driver.u2_driver import U2Driver
    from wendle.graph import Graph
    from wendle.replay.engine import ReplayEngine
    d = u2.connect(); _guard(d)
    if pkg:
        d.shell(f"am force-stop {pkg}")
    d.press("home"); time.sleep(1.2)
    out = ReplayEngine(Graph.from_json(open(path).read()), U2Driver()).run()
    steps = [f"{s.kind}:{'ok' if s.ok else (s.error or 'fail')}" for s in out.steps]
    print(f"  replay: {out.status}  steps=[{', '.join(steps)}]")
    return out


# ---------------- the progressive ladder ----------------

def tier1():
    # SIMPLE: standalone View apps, am_start launch, stable text selectors.
    print("\n=== TIER 1: simple standalone apps ===")
    cases = [
        ("settings", "com.android.settings", "Ajustes",
         [("tap_text", "Conexiones"), ("tap_text", "Wi-Fi")]),
        ("clock", "com.sec.android.app.clockpackage", "Reloj",
         [("tap_text", "Cronómetro")]),
        ("calc", "com.sec.android.app.popupcalculator", "Calculadora",
         [("tap_text", "1"), ("tap_text", "+")]),
    ]
    return _run_cases(cases)


def tier2():
    # IN-BETWEEN: dynamic content / WebView / list-driven.
    print("\n=== TIER 2: in-between dynamic apps ===")
    cases = [
        # single-Activity tabbed app: tabs are content-desc, content-driven selectors
        ("clock", "com.sec.android.app.clockpackage", "Reloj",
         [("open", "Temporizador"), ("open", "Alarma")]),
        # list-driven single-Activity
        ("contacts", "com.samsung.android.app.contacts", "Contactos",
         [("tap_text", "Buscar")]),
        # WebView / dynamic chrome
        ("chrome", "com.android.chrome", "Chrome", [("open", "Buscar o escribir URL")]),
    ]
    return _run_cases(cases)


def tier3():
    # HARD: shared-package launch (Gemini), feeds, Compose.
    print("\n=== TIER 3: hard apps ===")
    cases = [
        # shared-package launch taxonomy: Gemini lives in the Google app; am_start is refused,
        # so the LaunchLadder must fall to the recorded IconTap rung — the hard launch case.
        ("gemini", None, "Gemini", [("back",)]),
        # dynamic FEED (volatile screens): honest behavior under churn, no confident-wrong.
        ("instagram", "com.instagram.android", "Instagram", [("back",)]),
        # volatile MEDIA (the seekbar volatile-widget fix): a player screen must not self-collide.
        ("youtube", "com.google.android.youtube", "YouTube", [("back",)]),
    ]
    return _run_cases(cases)


def _run_cases(cases):
    results = {}
    for name, pkg, icon, flow in cases:
        print(f"\n-- {name} ({icon}) --")
        try:
            g, path = record(name, pkg, icon, flow)
            if g is None:
                results[name] = "skipped"; continue
            out = replay(name, path, pkg)
            results[name] = out.status
        except Exception as e:
            print(f"  [error] {type(e).__name__}: {e}")
            results[name] = f"error:{type(e).__name__}"
    return results


def main(argv) -> int:
    tier = argv[0] if argv else "1"
    fn = {"1": tier1, "2": tier2, "3": tier3}.get(tier)
    if fn is None:
        print("usage: validate_e2e.py <1|2|3>"); return 2
    res = fn()
    print(f"\n[tier {tier}] results: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
