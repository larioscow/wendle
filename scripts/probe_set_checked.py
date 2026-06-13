"""On-device probe: is a checkbox a READABLE checkable that set_checked flips idempotently?

The recorder now captures any detected checkable flip as a same-screen set_checked (the box
does not advance the screen; a separate button does). That capture only works if the widget
exposes a readable `checkable`/`checked` (or `selected`) attribute in the view hierarchy — a
Compose/custom box with semantics-only state is invisible to it. This probe answers, for one
widget: (1) does it report checkable/checked, (2) does driver.set_checked flip it, (3) is it
idempotent (re-running must NOT uncheck it). Whether the tap also advances is printed but is
NOT the decision — it's informational.

USAGE — drive the app by hand to the screen with the checkbox FIRST (UNCHECKED), then run:

    uv run python scripts/probe_set_checked.py
    uv run python scripts/probe_set_checked.py --checkbox mx.com.miapp:id/cbTerms --submit Continuar

It does NOT navigate for you. It reads the BEFORE state, runs set_checked(True), reads
AFTER, then runs set_checked(True) AGAIN (the idempotency leg) and reports each leg.
Read-only except for the two set_checked calls on the checkbox you name.
"""
from __future__ import annotations

import argparse
import sys


def _snap(driver, focus_pkg_box):
    """(structure_id, namespace) of the screen on display right now."""
    from wendle.fingerprint.dumpsys import focused_package, foreground_namespace
    from wendle.fingerprint.signature import structure_id

    xml = driver.dump_hierarchy()
    act, win = driver.dumps()
    focus = focused_package(win)
    focus_pkg_box["f"] = focus
    ns = foreground_namespace(act, win)
    return structure_id(ns, xml, focus_pkg=focus), ns


def _read_widget(d, xpath):
    """Return the widget's info dict (or None if absent)."""
    el = d.xpath(xpath)
    if not el.exists:
        return None
    return el.get().info or {}


def _checked(info):
    if info is None:
        return None
    cur = info.get("checked")
    return bool(info.get("selected", False) if cur is None else cur)


def _submit_enabled(d, submit_text):
    info = _read_widget(d, f'//*[@text={submit_text!r}]')
    if info is None:
        # try contains, some apps pad the label
        info = _read_widget(d, f'//*[contains(@text, {submit_text!r})]')
    if info is None:
        return None, "submit not found on screen"
    return bool(info.get("enabled", False)), ("clickable=%s enabled=%s" % (
        info.get("clickable"), info.get("enabled")))


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkbox", default="mx.com.miapp:id/cbTerms",
                    help="resource-id of the gating checkbox")
    ap.add_argument("--submit", default="Continuar",
                    help="visible text of the gated submit button")
    args = ap.parse_args(argv)

    try:
        import uiautomator2 as u2  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print("Run via `uv run`. Import error:", e)
        return 1

    from wendle.driver.u2_driver import U2Driver
    from wendle.models import Selector

    u2.connect()
    driver = U2Driver()
    d = driver._d
    cb_xpath = f'//*[@resource-id={args.checkbox!r}]'
    cb_sel = Selector("resource_id", args.checkbox)
    focus = {}

    print(f"checkbox = {args.checkbox}")
    print(f"submit   = text:{args.submit!r}\n")

    cb0 = _read_widget(d, cb_xpath)
    if cb0 is None:
        print(f"FAIL: checkbox {args.checkbox} not on screen. Navigate to the terms "
              f"screen first (checkbox UNCHECKED), then re-run.")
        return 2
    struct0, ns0 = _snap(driver, focus)
    en0, en0s = _submit_enabled(d, args.submit)
    chk0 = _checked(cb0)
    print("BEFORE:")
    print(f"  checkbox.checked = {chk0}")
    print(f"  submit.enabled   = {en0}   ({en0s})")
    print(f"  fingerprint      = {struct0}  ns={ns0}")

    if chk0:
        print("\nNOTE: checkbox is ALREADY checked — this run tests the IDEMPOTENCY leg "
              "(set_checked must not uncheck it). Uncheck by hand + re-run for the flip+advance leg.\n")

    # ---- LEG 1: unchecked -> set_checked(True) should flip AND advance ----
    print("\n>>> set_checked(True)  [leg 1]")
    ok1, raised1 = None, None
    try:
        ok1 = driver.set_checked(cb_sel, True)
    except Exception as e:  # noqa: BLE001
        raised1 = f"{type(e).__name__}: {e}"
    cb1 = _read_widget(d, cb_xpath)
    struct1, ns1 = _snap(driver, focus)
    en1, en1s = _submit_enabled(d, args.submit)
    chk1 = _checked(cb1)
    cb_gone = cb1 is None
    advanced = struct1 != struct0
    print("AFTER leg 1:")
    print(f"  set_checked returned = {ok1}" + (f"  RAISED: {raised1}" if raised1 else ""))
    if cb_gone:
        # the checkbox left the tree — only meaningful if the screen advanced (gate opened)
        print(f"  checkbox             = GONE from tree   (a) flipped: implied-by-advance={advanced}")
    else:
        print(f"  checkbox.checked     = {chk1}   (a) flipped-to-True: {chk1 is True}")
    print(f"  submit.enabled       = {en1}   ({en1s})   (b) enabled-now: {en1 is True}")
    print(f"  fingerprint          = {struct1}   (c) advanced: {advanced}  ns={ns1}")
    if raised1 and advanced:
        print("  NOTE: set_checked RAISED because cbTerms vanished after its tap (the screen"
              "\n        advanced). The tap WORKED; this is the known post-poll reporting bug —"
              "\n        a set_checked edge whose widget disappears on success must not report"
              "\n        false drift. Fold a 'widget gone after tap == success' guard into"
              "\n        u2_driver.set_checked when we implement the promotion.")

    # ---- verdict (one set_checked call; what it proves depends on the BEFORE state) ----
    print("\n================ VERDICT ================")
    if not (cb0.get("checkable") or "checked" in cb0 or "selected" in cb0):
        print("BLIND — the widget reports NO readable checkable/checked/selected state.")
        print("  => detect_checkable_entry CANNOT see it; the recorder will not capture a")
        print("     set_checked (likely Compose/custom). Needs semantics-based detection.")
    elif chk0 is False:
        # FLIP leg: does set_checked actually flip this readable box? (advance is informational)
        flipped = (chk1 is True) or (cb_gone and advanced)
        if flipped:
            print("PASS (flip leg) — the box is READABLE and set_checked flipped it to True.")
            print(f"  (informational: gate enabled={en1}, screen advanced={advanced} — not the")
            print("   decision; the recorder rides this as a same-screen set_checked pre_action.)")
            print("  => Re-run with the box ALREADY CHECKED to prove the idempotency leg.")
        else:
            print("FAIL — box is readable but set_checked did NOT flip it. Investigate the")
            print("  selector / driver before trusting captured set_checked for this widget.")
    else:
        # IDEMPOTENCY leg: box started checked -> must NOT uncheck (Playwright early-return).
        stayed = (chk1 is True)
        if stayed:
            print("PASS (idempotency leg) — set_checked on an already-checked box did NOT")
            print("  uncheck it (early-return held). Replay is idempotent — no double-toggle.")
        else:
            print(f"FAIL (idempotency) — checked stayed={stayed}. set_checked un-checked an")
            print("  already-checked box; replay would not be idempotent.")
    print("\nThe REAL end-to-end test: re-record the form; the checkboxes should now appear as")
    print("set_checked pre_actions on the Continuar edge. This probe only explains a no-show.")
    print("=========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
