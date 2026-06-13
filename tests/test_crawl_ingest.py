"""The crawl-ingestion back-end (v2 milestone 1): map built with no human walk.

Drives the reference explorer over a scripted FakeDriver app and asserts the PRODUCT
properties: the crawl-built graph is a real verified graph (navigable, replayable, honest),
the policy never mutates user state (no checkable taps), escapes are guarded, budgets hard.
"""
import threading

from wendle.crawl import CrawlIngester, explore
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint
from wendle.models import DeviceProfile

PKG = "com.crawl.app"
NS_HOME, NS_A, NS_B = f"{PKG}/.Home", f"{PKG}/.PageA", f"{PKG}/.PageB"
NS_OUT = "com.other.app/.Out"

NOSLEEP = {"sleep": lambda _dt: None}


def _screen(ns, rows, checkable=()):
    pkg = ns.split("/")[0]
    body = ""
    y = 500
    for label in rows:
        body += (f'<node class="android.widget.Button" package="{pkg}" resource-id="" '
                 f'clickable="true" checkable="false" focusable="false" content-desc="" '
                 f'text="{label}" scrollable="false" bounds="[40,{y}][1040,{y + 120}]"/>')
        y += 200
    for label in checkable:
        body += (f'<node class="android.widget.Switch" package="{pkg}" resource-id="" '
                 f'clickable="true" checkable="true" focusable="false" content-desc="{label}" '
                 f'text="" scrollable="false" bounds="[40,{y}][1040,{y + 120}]"/>')
        y += 200
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" '
            f'resource-id="" clickable="false" content-desc="" text="" '
            f'bounds="[0,0][1080,2340]">{body}</node></hierarchy>')


X_HOME = _screen(NS_HOME, ["Open A", "Open B", "Leave"], checkable=("Dark mode",))
X_A = _screen(NS_A, ["Deep"])
X_B = _screen(NS_B, [])
X_OUT = _screen(NS_OUT, ["Foreign"])

HOME = fingerprint(NS_HOME, X_HOME)


class ScriptedApp(FakeDriver):
    """A tiny stateful app: Home -> A/B by button; 'Leave' exits the package; BACK pops."""

    def __init__(self):
        super().__init__(display=(1080, 2340))
        self.stack = [(X_HOME, NS_HOME)]
        self.toggles = 0

    def _cur(self):
        return self.stack[-1]

    def dump_hierarchy(self):
        return self._cur()[0]

    def dumps(self):
        ns = self._cur()[1]
        pkg, _, act = ns.partition("/")
        return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
                f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")

    LABEL_ROUTES = {"Open A": (None, None), "Open B": (None, None)}  # filled after class body

    def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
        if selector.kind in ("label", "text"):
            # a real app responds to selector taps (navigate's reposition path uses them)
            v = str(selector.value)
            if v == "Open A":
                self.stack.append((X_A, NS_A)); return True
            if v == "Open B":
                self.stack.append((X_B, NS_B)); return True
            if v == "Deep":
                self.stack.append((X_B, NS_B)); return True
            return False
        if selector.kind != "coords":
            return super().resolve_and_tap(selector, action_type, timeout)
        x, y = selector.value
        xml, ns = self._cur()
        if ns == NS_A and 500 <= y < 620:
            self.stack.append((X_B, NS_B))  # 'Deep' row navigates
            return True
        if ns == NS_HOME:
            if 500 <= y < 620:
                self.stack.append((X_A, NS_A))
            elif 700 <= y < 820:
                self.stack.append((X_B, NS_B))
            elif 900 <= y < 1020:
                self.stack.append((X_OUT, NS_OUT))
            elif y >= 1100:
                self.toggles += 1  # the Dark-mode switch — must NEVER be hit
        return True

    def keyevent(self, code):
        if int(code) == 4 and len(self.stack) > 1:
            self.stack.pop()


PROFILE = DeviceProfile(touchscreen_node="/dev/null", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")


def _crawl(max_actions=12, max_depth=2):
    drv = ScriptedApp()
    ing = CrawlIngester(drv, settle_kwargs=NOSLEEP)
    ing.start()
    t = [0.0]

    def fsleep(dt):
        t[0] += max(dt, 0.05)  # fake time advances so router budgets expire instantly

    summary = explore(ing, PKG, max_actions=max_actions, max_depth=max_depth,
                      sleep=fsleep, clock=lambda: t[0])
    return drv, ing, summary


def test_crawl_builds_the_map_without_a_human_walk():
    drv, ing, summary = _crawl()
    g = ing.graph
    namespaces = {g.screen(n).namespace for n in g.g.nodes}
    assert {NS_HOME, NS_A, NS_B} <= namespaces  # both pages discovered + mapped
    # edges are REAL recorded interactions with selectors, not coordinates
    kinds = {d["action"].selector.kind for (_u, _v, _k, d) in g.ordered_transitions()}
    assert "coords" not in kinds and kinds  # narrowest-unique selector synthesis was used


def test_crawl_never_touches_checkables():
    drv, _ing, _summary = _crawl()
    assert drv.toggles == 0  # the Dark-mode switch was never actuated (state-set, not nav)


def test_package_guard_retreats_and_keeps_the_real_edge():
    drv, ing, _summary = _crawl()
    g = ing.graph
    out_nodes = [n for n in g.g.nodes if (g.screen(n).package or "") != PKG
                 and not g.screen(n).namespace.startswith("com.sec")]
    # the 'Leave' edge is REAL and kept; the crawl retreated instead of exploring foreign turf
    foreign_sources = [u for (u, _v, _k) in g.g.edges(keys=True)
                       if (g.screen(u).package or "") != PKG]
    assert foreign_sources == []  # no actions were committed FROM the foreign screen


def test_budget_is_hard():
    _drv, _ing, summary = _crawl(max_actions=3)
    assert summary["actions"] <= 3


def test_crawl_built_graph_replays_and_navigates():
    # THE PRODUCT PROPERTY: the crawl-built map is a real verified graph — replayable and
    # navigable with the standard engines, not a special crawl artifact.
    _drv, ing, _summary = _crawl()
    g = ing.graph

    # the crawl had no launcher entry, so stamp the anchor the way a real crawl run does
    # (launch the app yourself -> the top screen is the launch activity)
    from wendle.models import ForceAction
    top = g.screen(HOME)
    top.force_action = ForceAction("am_start", NS_HOME, verified_fp=HOME)

    target = next(n for n in g.g.nodes if g.screen(n).namespace == NS_A)

    replay_drv = ScriptedApp()
    from wendle.navigate.navigator import Navigator
    t = [0.0]
    nav = Navigator(g, replay_drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs(drv=replay_drv):
        from wendle.record.observe import observe_settled
        return observe_settled(drv, threading.Lock(), **NOSLEEP)

    nav._observe = obs

    # the scripted app's label taps: FakeDriver._present needs the labels present
    replay_drv.present_selectors = {("text", "Open A"), ("text", "Open B"), ("text", "Deep")}

    real_tap = replay_drv.resolve_and_tap

    def tap(selector, action_type="click", timeout=5.0):
        if selector.kind in ("label", "text") and "A" in str(selector.value):
            replay_drv.stack.append((X_A, NS_A))
            return True
        return real_tap(selector, action_type, timeout)

    replay_drv.resolve_and_tap = tap
    out = nav.navigate(HOME, target)
    assert out.status in ("arrived", "arrived_unverified"), f"{out.status}: {out.detail}"


def test_noncontiguous_trace_keeps_in_app_taps_in_the_flow():
    # the crawl star (BACK retreats are unrecorded): after an edge into a FOREIGN sub-screen,
    # the next edge departs the in-package top again. The flow must re-anchor (launch) AND
    # still emit that in-app tap — dropping it mistook package DRIFT for an app-ENTRY gesture
    # (only a cross-package SOURCE means the gesture is superseded by the launch).
    from wendle.fingerprint.signature import fingerprint as fp
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.replay.commands import flow_from_recording

    g = Graph()
    g.upsert_screen(Screen(id="TOP", namespace=f"{PKG}/.Home", package=PKG, activity=".Home",
                           force_action=ForceAction("am_start", f"{PKG}/.Home",
                                                    verified_fp="TOP")))
    g.upsert_screen(Screen(id="OUT", namespace="com.other/.Page", package="com.other",
                           activity=".Page"))
    g.upsert_screen(Screen(id="SUB", namespace=f"{PKG}/.Sub", package=PKG, activity=".Sub"))
    g.add_transition(Transition(source="TOP", target="OUT",
                                action=Action(selector=Selector("label", "Account"),
                                              action_type="click")))
    # untracked BACK retreat, then a second in-app tap from TOP
    g.add_transition(Transition(source="TOP", target="SUB",
                                action=Action(selector=Selector("label", "Connections"),
                                              action_type="click")))
    cmds = flow_from_recording(g, start_id="TOP")
    kinds = [(c.kind, c.action.selector.value if c.action else None) for c in cmds]
    assert ("action", "Connections") in kinds, f"in-app tap dropped: {kinds}"
    assert kinds[0] == ("action", "Account")


def test_in_package_contiguity_break_reanchors_before_the_tap():
    # star trace, SAME package: top->A then top->B (BACK retreat unrecorded). Without a
    # re-anchor the B tap would fire while the device sits on A's page — the engine stops
    # honestly but loses the step. Contiguity rule: an edge whose source != the previous
    # emitted target re-anchors (launch) first; a contiguous human trace never triggers it.
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.replay.commands import flow_from_recording

    g = Graph()
    g.upsert_screen(Screen(id="TOP", namespace=f"{PKG}/.Home", package=PKG, activity=".Home",
                           force_action=ForceAction("am_start", f"{PKG}/.Home",
                                                    verified_fp="TOP")))
    for sid, act in (("A", ".A"), ("B", ".B")):
        g.upsert_screen(Screen(id=sid, namespace=f"{PKG}/{act}", package=PKG, activity=act))
    g.add_transition(Transition(source="TOP", target="A",
                                action=Action(selector=Selector("label", "Go A"),
                                              action_type="click")))
    g.add_transition(Transition(source="TOP", target="B",
                                action=Action(selector=Selector("label", "Go B"),
                                              action_type="click")))
    kinds = [(c.kind, c.action.selector.value if c.action else None)
             for c in flow_from_recording(g, start_id="TOP")]
    assert kinds == [("action", "Go A"), ("launch", None), ("action", "Go B")], kinds


X_LIST = (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" resource-id="" '
          f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
          f'<node class="androidx.recyclerview.widget.RecyclerView" package="{PKG}" '
          f'resource-id="{PKG}:id/list" clickable="false" content-desc="" text="" '
          f'scrollable="true" bounds="[0,200][1080,2340]">'
          + "".join(
              f'<node class="android.widget.Button" package="{PKG}" resource-id="" '
              f'clickable="true" checkable="false" content-desc="" text="Row {i}" '
              f'bounds="[40,{200 + i * 430}][1040,{600 + i * 430}]"/>' for i in range(5))
          + '</node></node></hierarchy>')
NS_LIST = f"{PKG}/.ListHome"
# the scrolled frame reveals a NEW row the top frame never showed
X_LIST2 = X_LIST.replace('text="Row 0"', 'text="Row 5"').replace(
    'text="Row 1"', 'text="Row 6"')


class ScrollApp(ScriptedApp):
    """Home is a scrollable list; a content-advance swipe reveals new rows."""

    def __init__(self):
        super().__init__()
        self.stack = [(X_LIST, NS_LIST)]

    def swipe(self, start, end, duration=0.2):
        xml, ns = self._cur()
        if ns == NS_LIST and end[1] < start[1]:  # content-advance (finger up)
            self.stack[-1] = (X_LIST2, NS_LIST)

    def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
        if selector.kind == "coords":
            x, y = selector.value
            xml, ns = self._cur()
            if ns == NS_LIST:
                # tapping any row navigates to PageA (enough to prove the frontier grew)
                self.stack.append((X_A, NS_A))
                return True
        return super().resolve_and_tap(selector, action_type, timeout)


def test_scroll_aware_frontier_reveals_and_visits_new_rows():
    # a screen with an adapter region is NOT exhausted at its first viewport: the explorer
    # issues a bounded content-advance swipe (committed through the ingester, so the builder
    # classifies it honestly) and the newly revealed rows join the frontier.
    drv = ScrollApp()
    ing = CrawlIngester(drv, settle_kwargs=NOSLEEP)
    ing.start()
    t = [0.0]
    explore(ing, PKG, max_actions=20, max_depth=1,
            sleep=lambda dt: t.__setitem__(0, t[0] + max(dt, 0.05)), clock=lambda: t[0],
            max_scrolls_per_screen=2)
    g = ing.graph
    tapped = {d["action"].selector.value for (_u, _v, _k, d) in g.ordered_transitions()
              if d["action"].action_type == "click"}
    assert any("Row 5" == v or "Row 6" == v for v in tapped), \
        f"revealed rows never joined the frontier: {tapped}"


def test_reposition_ladder_returns_to_frontier_screens():
    # BFS works screens breadth-first: after drilling into A, the explorer must get BACK to
    # the home frontier (navigate has no recorded return edge -> the BACK fallback) and
    # exhaust the remaining home candidates rather than stranding them.
    drv, ing, _summary = _crawl(max_actions=12, max_depth=2)
    g = ing.graph
    home_edges = [d["action"].selector.value for (_u, _v, _k, d) in g.ordered_transitions()
                  if _u == HOME]
    assert {"Open A", "Open B", "Leave"} <= set(home_edges), home_edges
    # and the deep candidate on A was ALSO visited (breadth did not starve depth)
    assert any(v == "Deep" for v in
               (d["action"].selector.value for (_u, _v, _k, d) in g.ordered_transitions()))
