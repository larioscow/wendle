"""Autonomous recorder — drive a real recording with NO human touches (emulator / CI).

WHY THIS EXISTS: a real recording is built from kernel touch events the recorder reads via
`getevent`. ADB `input tap` bypasses that device, and on the emulator's `virtio_input_multi_touch`
device `sendevent` writes ARE visible to `getevent` but are NOT dispatched by InputFlinger (the
device has no INPUT_PROP_DIRECT), so synthesized touches never actuate the UI. uiautomator2 clicks
DO actuate (they go through InputManager). So we ACTUATE with u2 and feed the REAL RecordSession its
gesture seam directly — the `getevent -> Gesture` decode path is already covered by unit tests
(test_*stream*, test recorder), so feeding Gestures is faithful for the layers this exercises:
real screen identity, transitions, launch anchors, redaction.

A "flow" is a list of steps; each ACTUATES through u2 and RECORDS the matching Gesture:
  ("open", "<content-desc>")  tap a launcher icon by its label (a launcher->app entry => anchor)
  ("tap_text", "<text>")      tap a visible element by its text
  ("tap", (dx, dy))           tap display coordinates
  ("back",)                   BACK keyevent (actuate only — recorder reconciles the screen change)

    uv run python scripts/auto_record.py        # runs the built-in demo flow -> out.json
"""
from __future__ import annotations

import sys
import time

from wendle.capture.types import Gesture


def _center(el) -> tuple[int, int]:
    b = el.info["bounds"]
    return (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2


class AutoRecorder:
    def __init__(self, settle_wait: float = 3.0, settle_after_tap: float = 1.2):
        import uiautomator2 as u2

        from wendle.calibration.calibrate import calibrate
        from wendle.driver.u2_driver import U2Driver
        from wendle.record.session import RecordSession

        self.d = u2.connect()
        self.driver = U2Driver()
        self.profile = calibrate(self.driver)
        self.W, self.H = self.profile.display
        self.px_max = self.profile.abs_x[1]
        self.py_max = self.profile.abs_y[1]
        # live_refresh=True (as the real recorder runs): a background dump thread keeps the
        # current-screen view fresh, so an untracked navigation like BACK is reconciled before
        # the next tap is attributed (without it, a post-back tap mis-binds to the stale screen).
        self.session = RecordSession(self.driver, self.profile, live_refresh=True,
                                     settle_kwargs={"max_wait": settle_wait})
        self._t = 1.0
        self._settle_after_tap = settle_after_tap

    def start(self):
        screen = self.session.start()
        print(f"[rec] start on {screen.namespace}")
        return screen

    def _record_tap(self, dx: int, dy: int):
        # actuate (InputManager) then feed the matching Gesture in PANEL coords (the recorder
        # scales raw->display via the profile, so we pre-scale display->panel here).
        # FEED IMMEDIATELY — record_gesture's _enter does its own settle. A sleep BEFORE the
        # feed loses the race against the live refresher: it reconciles current to the NEW
        # screen first, and the tap then mis-attributes as a same-screen probe (the real
        # getevent stream feeds at touch time, so a human recording never has this gap).
        self.d.click(dx, dy)
        gx = int(dx * self.px_max / (self.W - 1))
        gy = int(dy * self.py_max / (self.H - 1))
        self._t += 1.0
        t = self.session.record_gesture(
            Gesture(kind="tap", t_down=self._t, t_up=self._t + 0.05, x=gx, y=gy))
        time.sleep(self._settle_after_tap)  # post-feed: let the refresher catch up
        cur = self.session.graph.screen(self.session.current_id)
        print(f"[rec] tap ({dx},{dy}) -> now {cur.namespace}"
              + (f"  edge {t.action.selector.kind}={t.action.selector.value!r}" if t else "  (no edge)"))
        return t

    def step(self, step):
        kind = step[0]
        if kind == "open":
            el = self.d(description=step[1])
            if not el.exists:
                el = self.d(text=step[1])
            dx, dy = _center(el)
            return self._record_tap(dx, dy)
        if kind == "tap_text":
            el = self.d(text=step[1])
            dx, dy = _center(el)
            return self._record_tap(dx, dy)
        if kind == "tap":
            return self._record_tap(*step[1])
        if kind == "back":
            self.d.press("back")
            time.sleep(self._settle_after_tap)
            print("[rec] back")
            return None
        raise ValueError(f"unknown step {kind!r}")

    def run(self, flow, out: str):
        self.start()
        for s in flow:
            self.step(s)
        self.session.graph.save(out)
        g = self.session.graph
        print(f"\n[rec] saved {out}: {g.g.number_of_nodes()} screens, {g.g.number_of_edges()} edges, "
              f"anchors={g.anchors()}")
        return g


def _demo_flow():
    # drawer -> Settings (am_start anchor) -> a LINEAR drill-down. Selector-driven replay
    # reproduces this without needing to distinguish the single-Activity .SubSettings twins.
    return [
        ("open", "Settings"),
        ("tap_text", "Network & internet"),
        ("tap_text", "Internet"),
    ]


def main(argv) -> int:
    out = argv[0] if argv else "out.json"
    rec = AutoRecorder()
    # deterministic launcher entry: force-stop the target so the drawer-tap opens it FRESH at
    # its top page (Settings otherwise reopens on its last-visited sub-page).
    rec.d.shell("am force-stop com.android.settings")
    rec.d.press("home")
    time.sleep(1.0)
    rec.d.swipe(0.5, 0.9, 0.5, 0.2, 0.2)  # open the app drawer (u2 actuation only)
    time.sleep(1.5)
    rec.run(_demo_flow(), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
