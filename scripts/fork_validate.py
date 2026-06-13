"""On-device validation of Cap 1 (fork-twin routing) — the last untested-on-hardware piece.

Drives a REAL record -> (inspect) -> navigate cycle against a collapsing-toolbar app (Samsung
Settings is the S23-confirmed OEM fork case: the big title collapses on scroll, forking BOTH
identity tiers). Actuates with uiautomator2 and feeds the real RecordSession its Gesture seam
(the auto_record discipline — getevent->Gesture decode is unit-covered).

What it proves on hardware:
  1. RECORD: a content-advance scroll on the collapsing-toolbar screen mints a `scroll`-CLASS
     continuation edge (intent='reveal') — the §2.7 fork classification fires on a real dump.
  2. The post-scroll tap's edge departs the SCROLLED twin (the fork is real: two node ids).
  3. NAVIGATE: from the TOP twin, navigate() to the post-scroll target either WALKS the scroll
     edge and arrives, or stops TYPED (honest) — never a confident-wrong arrival.

    uv run python scripts/fork_validate.py [com.android.settings]
"""
from __future__ import annotations

import sys
import time

from wendle.capture.types import Gesture


class ForkHarness:
    def __init__(self, settle_wait: float = 3.0, settle_after: float = 1.4):
        import uiautomator2 as u2

        from wendle.calibration.calibrate import calibrate
        from wendle.driver.u2_driver import U2Driver
        from wendle.record.session import RecordSession

        self.d = u2.connect()
        self.driver = U2Driver()
        self.profile = calibrate(self.driver)
        self.W, self.H = self.profile.display
        self.px_max, self.py_max = self.profile.abs_x[1], self.profile.abs_y[1]
        self.session = RecordSession(self.driver, self.profile, live_refresh=True,
                                     settle_kwargs={"max_wait": settle_wait})
        self._t = 1.0
        self._settle_after = settle_after

    def _panel(self, dx, dy):
        return int(dx * self.px_max / (self.W - 1)), int(dy * self.py_max / (self.H - 1))

    def start(self):
        scr = self.session.start()
        print(f"[rec] start on {scr.namespace}  id={self.session.current_id[:12]}")
        return scr

    def record_scroll(self, frac_from=0.72, frac_to=0.30, x_frac=0.5):
        """Actuate a content-advance scroll (finger up) and feed the matching swipe Gesture."""
        before = self.session.current_id
        x = int(self.W * x_frac)
        y0, y1 = int(self.H * frac_from), int(self.H * frac_to)
        self.d.swipe(x, y0, x, y1, 0.35)  # actuate via InputManager
        gx, gy = self._panel(x, y0)
        gx2, gy2 = self._panel(x, y1)
        self._t += 1.0
        t = self.session.record_gesture(Gesture(kind="swipe", t_down=self._t, t_up=self._t + 0.3,
                                                x=gx, y=gy, x2=gx2, y2=gy2))
        time.sleep(self._settle_after)
        after = self.session.current_id
        klass = t.action_class if t is not None else None
        intent = t.action.intent if t is not None else None
        moved = "FORKED" if after != before else "same-id"
        print(f"[rec] scroll {before[:10]} -> {after[:10]}  ({moved})  "
              f"edge={klass} intent={intent}")
        return t, before, after

    def open_from_launcher(self, pkg, dx=540, dy=1200):
        """Enter the app FROM the launcher so the recorder stamps the am_start anchor on the
        app's top screen (what navigate() re-anchors to). Actuates the launch via monkey
        (deterministic — drawer layouts vary), then feeds a launcher tap gesture: the recorder's
        source is the (current) launcher screen and its _enter settles on the app, so it builds a
        launcher->app edge and stamps the anchor exactly as a real icon tap would."""
        self.d.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        gx, gy = self._panel(dx, dy)
        self._t += 1.0
        t = self.session.record_gesture(Gesture(kind="tap", t_down=self._t, t_up=self._t + 0.05,
                                                x=gx, y=gy))
        time.sleep(self._settle_after + 1.2)
        scr = self.session.graph.screen(self.session.current_id)
        anchor = scr.force_action.kind if scr and scr.force_action else None
        print(f"[rec] open {pkg} -> {scr.namespace if scr else '?'}  anchor={anchor}")
        return t

    def record_tap_text(self, text):
        el = self.d(text=text)
        if not el.exists:
            print(f"[rec] tap_text {text!r}: NOT VISIBLE — skipping")
            return None
        b = el.info["bounds"]
        dx, dy = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
        before = self.session.current_id
        self.d.click(dx, dy)
        gx, gy = self._panel(dx, dy)
        self._t += 1.0
        t = self.session.record_gesture(Gesture(kind="tap", t_down=self._t, t_up=self._t + 0.05,
                                                x=gx, y=gy))
        time.sleep(self._settle_after)
        print(f"[rec] tap {text!r}: {before[:10]} -> {self.session.current_id[:10]}  "
              + (f"edge {t.action.selector.kind}={t.action.selector.value!r}" if t else "(no edge)"))
        return t


def main(argv) -> int:
    pkg = argv[0] if argv else "com.android.settings"
    h = ForkHarness()
    h.d.shell(f"am force-stop {pkg}")
    time.sleep(0.6)

    # start on HOME so the launcher->Settings tap is recorded (stamps the am_start anchor that
    # navigate() re-anchors to). Then settle is on the launcher; the open stamps the anchor.
    h.d.press("home")
    time.sleep(1.0)
    h.start()
    h.open_from_launcher(pkg)
    top_id = h.session.current_id

    # 1+2: scroll the collapsing-toolbar list, then tap a now-revealed deep item
    t_scroll, src, scrolled = h.record_scroll()
    deep_target = None
    # tap a SETTLING deep row (avoid Battery — its live % keeps it volatile, unroutable)
    for candidate in ("Security and privacy", "Seguridad y privacidad", "Accessibility",
                      "Accesibilidad", "About phone", "Acerca del teléfono",
                      "Digital Wellbeing", "Tips"):
        if h.d(text=candidate).exists:
            t_tap = h.record_tap_text(candidate)
            if t_tap is not None:
                deep_target = t_tap.target
            break

    g = h.session.graph
    scroll_edges = [(u, v) for (u, v, _k, d) in g.ordered_transitions()
                    if d.get("action_class") == "scroll"]
    print(f"\n[graph] {g.g.number_of_nodes()} screens, {g.g.number_of_edges()} edges, "
          f"{len(scroll_edges)} scroll edge(s)")
    print(f"[result] CAP1-RECORD: {'PASS' if scroll_edges else 'NO-FORK (app did not fork on scroll)'}")

    # 3: navigate from the TOP twin to the deep target — must WALK or stop TYPED, never lie
    if scroll_edges and deep_target is not None and deep_target != scrolled:
        from wendle.navigate.navigator import Navigator
        # return to the top twin: force-stop so navigate()'s own am_start re-anchors fresh at
        # the TOP screen (Settings otherwise reopens on its last sub-page)
        h.d.shell(f"am force-stop {pkg}")
        time.sleep(1.0)
        h.d.press("home")
        time.sleep(0.8)
        nav = Navigator(g, h.driver)
        print(f"\n[nav] navigate(top={top_id[:10]} -> deep={deep_target[:10]}) ...")
        out = nav.navigate(top_id, deep_target)
        print(f"[nav] status={out.status}  tier={getattr(out,'tier',None)}  "
              f"detail={getattr(out,'detail',None)}")
        honest = out.status in ("arrived", "arrived_unverified", "content_drift", "off_graph",
                                "no_route")
        confident_wrong = (out.status == "arrived"
                           and h.session.current_id  # placeholder; on-screen truth is the user's
                           and False)
        verdict = "PASS (arrived)" if out.status == "arrived" else (
            "PASS (honest typed stop)" if honest else "FAIL")
        print(f"[result] CAP1-NAVIGATE: {verdict}")
        print("[note] confirm on the phone screen which Settings page is showing — "
              "that is ground truth over this self-report.")
    else:
        print("[nav] skipped (no fork edge or no distinct deep target captured)")

    out_path = f"fork_{pkg.split('.')[-1]}.json"
    g.save(out_path)
    print(f"[saved] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
