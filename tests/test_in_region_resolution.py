"""In-region actions resolve REGION-BOUND, never via global xpath (S23 decoy finding).

Samsung Settings' search plate rotates suggested queries through setting names as real
TEXT — lexically identical to the recorded row label. When the row is below the fold, a
global resolve binds the chrome decoy and confidently taps the search box (ok=True ->
SearchActivity). The recorded action knows better: in_region=True means the element lived
INSIDE the adapter region, so replay must resolve it through the reveal machinery's
in-container matcher (check+act from one settled dump, scrolling as needed) — the same L5
discipline the rung already enforces — and never through a global xpath.
"""
import threading

from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator

PKG = "com.app"
NS = f"{PKG}/.List"
NS_DET = f"{PKG}/.Detail"
NOSLEEP = {"sleep": lambda _dt: None}

# chrome SEARCH PLATE carries the decoy text OUTSIDE the region; the real row is INSIDE
X_LIST = (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" resource-id="" '
          f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
          f'<node class="android.widget.TextView" package="{PKG}" '
          f'resource-id="{PKG}:id/search_suggestion" clickable="true" content-desc="" '
          f'text="Lock screen" bounds="[100,80][980,200]"/>'
          f'<node class="androidx.recyclerview.widget.RecyclerView" package="{PKG}" '
          f'resource-id="{PKG}:id/list" clickable="false" content-desc="" text="" '
          f'scrollable="true" bounds="[0,300][1080,2340]">'
          + "".join(
              f'<node class="android.view.View" package="{PKG}" resource-id="" clickable="true" '
              f'content-desc="" text="" bounds="[0,{300 + i * 410}][1080,{700 + i * 410}]">'
              f'<node class="android.widget.TextView" package="{PKG}" resource-id="" '
              f'clickable="false" content-desc="" text="{lab}" '
              f'bounds="[40,{320 + i * 410}][1000,{420 + i * 410}]"/></node>'
              for i, lab in enumerate(["Sound", "Display", "Lock screen", "Battery"]))
          + '</node></node></hierarchy>')
X_DET = (f'<hierarchy><node class="android.widget.LinearLayout" package="{PKG}" '
         f'resource-id="{PKG}:id/detail" clickable="false" content-desc="" text="" '
         f'bounds="[0,0][1080,2340]"/></hierarchy>')

LIST_ID = fingerprint(NS, X_LIST)
DET_ID = fingerprint(NS_DET, X_DET)


class DecoyDriver(FakeDriver):
    """Tapping the real row (by coords inside its bounds) navigates; resolving the label
    GLOBALLY 'succeeds' too (the decoy is present) — but navigates to the WRONG place."""

    def __init__(self):
        super().__init__(display=(1080, 2340))
        self.on_detail = False
        self.decoy_taps = 0

    def dump_hierarchy(self):
        return X_DET if self.on_detail else X_LIST

    def dumps(self):
        ns = NS_DET if self.on_detail else NS
        pkg, _, act = ns.partition("/")
        return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
                f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")

    def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
        if selector.kind == "coords":
            x, y = selector.value
            if y < 300:
                self.decoy_taps += 1  # tapped the chrome search plate — the WRONG element
            elif 1120 <= y <= 1520:   # the real 'Lock screen' row band
                self.on_detail = True
            return True
        if selector.kind in ("label", "text") and str(selector.value) == "Lock screen":
            # a GLOBAL resolve finds the decoy first (deepest unique? both present -> the
            # fake models the hardware outcome: the chrome match wins)
            self.decoy_taps += 1
            return True
        return False


def _graph():
    g = Graph()
    g.upsert_screen(Screen(id=LIST_ID, namespace=NS, package=PKG, activity=".List",
                           structure_id=structure_id(NS, X_LIST),
                           force_action=ForceAction("am_start", NS, verified_fp=LIST_ID)))
    g.upsert_screen(Screen(id=DET_ID, namespace=NS_DET, package=PKG, activity=".Detail",
                           structure_id=structure_id(NS_DET, X_DET)))
    g.add_transition(Transition(
        source=LIST_ID, target=DET_ID,
        action=Action(selector=Selector("label", "Lock screen"), action_type="click",
                      in_region=True, bounds=(0, 1120, 1080, 1520))))
    return g


def test_in_region_tap_resolves_region_bound_never_the_chrome_decoy():
    g = _graph()
    drv = DecoyDriver()
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs():
        from wendle.record.observe import observe_settled
        return observe_settled(drv, threading.Lock(), **NOSLEEP)

    nav._observe = obs
    out = nav.navigate(LIST_ID, DET_ID)
    assert drv.decoy_taps == 0, "an in-region tap must NEVER bind the chrome decoy"
    assert out.status == "arrived", f"{out.status}: {out.detail}"
    assert drv.on_detail  # the REAL row was acted on, bounds-anchored from the settled dump


def test_engine_in_region_tap_also_resolves_region_bound():
    # the same decoy through the REPLAY engine: the in-region command must route through
    # the rung (region-bound), never the global wait+act that the decoy satisfies.
    from wendle.replay.engine import ReplayEngine
    g = _graph()
    drv = DecoyDriver()
    drv.present_selectors = {("text", "Lock screen")}  # global wait WOULD pass (the decoy)
    t = [0.0]
    eng = ReplayEngine(g, drv, clock=lambda: t[0],
                       sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs():
        from wendle.record.observe import observe_settled
        return observe_settled(drv, threading.Lock(), **NOSLEEP)

    eng._observe = obs
    out = eng.run()
    assert drv.decoy_taps == 0, "the engine must never bind the chrome decoy"
    assert out.status == "completed" and drv.on_detail
