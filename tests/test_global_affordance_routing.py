"""Global-affordance recovery — routing single-Activity tabbed / bottom-nav / drawer apps.

S23 Clock finding: a single-Activity tabbed app's cold-launch tab is HISTORY-DEPENDENT, so
the navigator (and the crawler's reposition) lands on a tab the recorded edges don't depart
from -> honest off_graph, zero coverage. The tab bar, though, is GLOBAL chrome: the target
tab's own entry button is present on every tab. Rule: when no route exists, try the TARGET's
recorded inbound nav affordance if its selector resolves here; the arrival gate judges the
result (a wrong attempt is an honest non-arrival, NEVER a confident landing).
"""
import threading

from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator

PKG = "com.tabs"
NOSLEEP = {"sleep": lambda _dt: None}


# each tab has a STRUCTURALLY DISTINCT body (real tabbed apps differ per tab: an alarm list
# vs a stopwatch's buttons vs a timer dial), so they are NOT structure twins -> _actual_node
# resolves the landed tab correctly and the recovery rung (not a false best-guess) does the work.
_BODY = {
    "Home":     '<node class="android.widget.TextView" package="{p}" text="Home dashboard" bounds="[40,200][1040,400]"/>',
    "World":    ('<node class="android.widget.ListView" package="{p}" resource-id="{p}:id/cities" bounds="[0,200][1080,2100]">'
                 '<node class="android.widget.TextView" package="{p}" text="London" bounds="[40,220][1040,320]"/>'
                 '<node class="android.widget.TextView" package="{p}" text="Tokyo" bounds="[40,340][1040,440]"/></node>'),
    "Timer":    ('<node class="android.widget.EditText" package="{p}" resource-id="{p}:id/dial" bounds="[200,300][880,900]"/>'
                 '<node class="android.widget.Button" package="{p}" content-desc="Start" clickable="true" bounds="[300,1000][780,1140]"/>'),
    "Settings": ('<node class="android.widget.ScrollView" package="{p}" resource-id="{p}:id/prefs" bounds="[0,200][1080,2100]">'
                 '<node class="android.widget.Switch" package="{p}" content-desc="Vibrate" checkable="true" clickable="true" bounds="[900,220][1040,320]"/>'
                 '<node class="android.widget.TextView" package="{p}" text="About" bounds="[40,360][1040,460]"/></node>'),
}


def _tab(name, body_text=None):
    # SAME tab bar (global chrome) on every tab + a DISTINCT body per tab (distinct structure)
    bar = "".join(
        f'<node class="android.widget.Button" package="{PKG}" resource-id="{PKG}:id/tab_{t}" '
        f'clickable="true" checkable="false" content-desc="{t}" text="" '
        f'bounds="[{i*270},2200][{(i+1)*270},2340]"/>'
        for i, t in enumerate(("Home", "World", "Timer", "Settings")))
    body = (body_text and f'<node class="android.widget.TextView" package="{PKG}" '
            f'text="{body_text}" bounds="[40,200][1040,400]"/>') or _BODY[name].format(p=PKG)
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" '
            f'resource-id="" clickable="false" content-desc="" text="" '
            f'bounds="[0,0][1080,2340]">{body}{bar}</node></hierarchy>')


NS = f"{PKG}/.MainActivity"
TABS = {t: _tab(t) for t in ("Home", "World", "Timer", "Settings")}
IDS = {t: fingerprint(NS, x) for t, x in TABS.items()}


def _graph():
    g = Graph()
    for t, x in TABS.items():
        g.upsert_screen(Screen(id=IDS[t], namespace=NS, package=PKG, activity=".MainActivity",
                               structure_id=structure_id(NS, x),
                               force_action=(ForceAction("am_start", NS, verified_fp=IDS[t])
                                             if t == "Home" else None)))
    # the crawl recorded tab-switches only from HOME (the tab it happened to start on):
    # Home -> World, Home -> Timer, Home -> Settings. NOT World->Settings etc.
    for t in ("World", "Timer", "Settings"):
        g.add_transition(Transition(source=IDS["Home"], target=IDS[t],
                                    action=Action(selector=Selector("content_desc", t),
                                                  action_type="click")))
    return g


class TabApp(FakeDriver):
    """A single-Activity tabbed app. am_start lands on the LAST tab (history-dependent); the
    tab bar switches content within the one Activity."""

    def __init__(self, launch_tab="Timer"):
        super().__init__(display=(1080, 2340))
        self.tab = launch_tab

    def dump_hierarchy(self):
        return TABS[self.tab]

    def dumps(self):
        return (f"topResumedActivity: ActivityRecord{{x u0 {NS} t1}}",
                f"mCurrentFocus=Window{{x u0 {NS}}}")

    def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
        self.taps.append((selector.kind, selector.value, action_type))
        name = str(selector.value).replace(f"{PKG}:id/tab_", "")
        if selector.kind in ("content_desc", "resource_id") and name in TABS:
            self.tab = name
            return True
        return False


def _nav(drv):
    t = [0.0]
    nav = Navigator(_graph(), drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs():
        from wendle.record.observe import observe_settled
        xml, ns, settled, focus = observe_settled(drv, threading.Lock(), **NOSLEEP)
        return xml, ns, focus, settled  # navigator order

    nav._observe = obs
    return nav


def test_global_affordance_reaches_target_tab_with_no_recorded_route_from_here():
    # cold launch lands on Timer (history); the recorded edges depart only from Home, so
    # there is NO route Timer->Settings. The Settings TAB button is global chrome present on
    # Timer -> the recovery taps it and the arrival gate verifies Settings (value-bearing).
    drv = TabApp(launch_tab="Timer")
    out = _nav(drv).navigate(IDS["Home"], IDS["Settings"])
    assert out.status == "arrived", f"{out.status}: {out.detail}"
    assert drv.tab == "Settings"  # actually on the target tab


def test_affordance_recovery_is_honest_when_target_button_is_absent():
    # a target whose entry affordance is NOT globally present (a deep screen) -> the recovery
    # finds nothing to tap and the honest off_graph stands; never a confident-wrong landing.
    g = _graph()
    deep = fingerprint(NS, _tab("Home", "a deep modal with no tab bar entry"))
    # a deep node reachable only from World, whose inbound selector is NOT on other screens
    g.upsert_screen(Screen(id=deep, namespace=NS, package=PKG, activity=".MainActivity",
                           structure_id=structure_id(NS, _tab("Home", "deep"))))
    g.add_transition(Transition(source=IDS["World"], target=deep,
                                action=Action(selector=Selector("content_desc", "DeepOnlyInWorld"),
                                              action_type="click")))
    drv = TabApp(launch_tab="Timer")
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs():
        from wendle.record.observe import observe_settled
        xml, ns, settled, focus = observe_settled(drv, threading.Lock(), **NOSLEEP)
        return xml, ns, focus, settled  # navigator order

    nav._observe = obs
    out = nav.navigate(IDS["Home"], deep)
    assert out.status in ("off_graph", "arrived_unverified")  # honest, never 'arrived'
    assert drv.tab != "Home"  # never confidently claimed the deep target


def test_affordance_recovery_does_not_fire_when_a_normal_route_exists():
    # starting on Home (which DOES route to Settings), the normal planner walks the recorded
    # edge — the recovery rung must not pre-empt or double-tap.
    drv = TabApp(launch_tab="Home")
    out = _nav(drv).navigate(IDS["Home"], IDS["Settings"])
    assert out.status == "arrived"
    assert drv.taps and drv.taps[-1][1] == "Settings"


# ---- first-class global edge: a DRIFTED target (fingerprint matches nothing) reached by
#      affordance + VERIFY-BY-AFFORDANCE (the World-tab case) ----

def _tabbar(active, items=("Home", "World", "Timer"), pkg="com.drift"):
    return "".join(
        f'<node class="android.widget.LinearLayout" package="{pkg}" content-desc="{n}" '
        f'clickable="{"false" if i==active else "true"}" selected="{"true" if i==active else "false"}" '
        f'bounds="[{300+i*200},2800][{480+i*200},2980]"/>' for i, n in enumerate(items))


def _driftscreen(active, body, pkg="com.drift"):
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" bounds="[0,0][1440,3120]">'
            f'<node class="android.widget.TextView" package="{pkg}" text="{body}" bounds="[40,200][1040,400]"/>'
            f'<node class="androidx.recyclerview.widget.RecyclerView" package="{pkg}" '
            f'resource-id="{pkg}:id/content" bounds="[0,420][1080,2700]"/>'
            f'<node class="com.google.android.material.tabs.TabLayout" package="{pkg}" '
            f'resource-id="{pkg}:id/tabs" bounds="[270,2800][1170,3000]">{_tabbar(active, items=("Home","World","Timer"), pkg=pkg)}'
            f'</node></node></hierarchy>')


def test_global_edge_reaches_a_drifted_target_via_verify_by_affordance():
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.navigate.navigator import Navigator
    PKG2 = "com.drift"; NS2 = f"{PKG2}/.Main"
    # recorded World screen (with the OLD live body); live World will have a DIFFERENT body+id
    rec_home = _driftscreen(0, "Home content")
    rec_world = _driftscreen(1, "World 09:00 London 10:00")
    HOME = fingerprint(NS2, rec_home); WORLD = fingerprint(NS2, rec_world)
    g = Graph()
    g.upsert_screen(Screen(id=HOME, namespace=NS2, package=PKG2, activity=".Main",
                           structure_id=structure_id(NS2, rec_home),
                           force_action=ForceAction("am_start", NS2, verified_fp=HOME)))
    g.upsert_screen(Screen(id=WORLD, namespace=NS2, package=PKG2, activity=".Main",
                           structure_id=structure_id(NS2, rec_world)))
    g.add_transition(Transition(source=HOME, target=WORLD,
                                action=Action(selector=Selector("content_desc", "World"),
                                              action_type="click", bounds=(500, 2800, 680, 2980)),
                                global_affordance=True))

    # the LIVE app: cold launch lands on Home; tapping World shows a DRIFTED World (new times ->
    # a body the recorded WORLD id will never match)
    class DriftApp(FakeDriver):
        def __init__(self):
            super().__init__(display=(1440, 3120)); self.tab = 0
        def dump_hierarchy(self):
            if self.tab == 1:
                # the LIVE World tab: structurally DIFFERENT from the recorded World (extra city
                # rows) so no fingerprint/structure match -> actual=None (the real drift case)
                rows = "".join(
                    f'<node class="android.widget.TextView" package="com.drift" '
                    f'text="City {i} {i}{i}:0{i}" bounds="[40,{500+i*120}][1040,{600+i*120}]"/>'
                    for i in range(5))
                return ('<hierarchy><node class="android.widget.FrameLayout" package="com.drift" '
                        'bounds="[0,0][1440,3120]">'
                        '<node class="android.widget.TextView" package="com.drift" text="World live" '
                        'bounds="[40,200][1040,400]"/>' + rows +
                        '<node class="com.google.android.material.tabs.TabLayout" package="com.drift" '
                        'resource-id="com.drift:id/tabs" bounds="[270,2800][1170,3000]">' +
                        _tabbar(1, items=("Home","World","Timer"), pkg="com.drift") +
                        '</node></node></hierarchy>')
            body = {0: "Home content", 2: "Timer"}[self.tab]
            return _driftscreen(self.tab, body)
        def dumps(self):
            return (f"topResumedActivity: ActivityRecord{{x u0 {NS2} t1}}",
                    f"mCurrentFocus=Window{{x u0 {NS2}}}")
        def resolve_and_tap(self, selector, action_type="click", timeout=5.0):
            self.taps.append((selector.kind, selector.value, action_type))
            idx = {"Home": 0, "World": 1, "Timer": 2}.get(str(selector.value))
            if idx is not None and selector.kind in ("content_desc", "label", "coords"):
                self.tab = idx; return True
            return False

    drv = DriftApp()
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))

    def obs():
        from wendle.record.observe import observe_settled
        x, ns, settled, focus = observe_settled(drv, __import__("threading").Lock(), **NOSLEEP)
        return x, ns, focus, settled
    nav._observe = obs
    out = nav.navigate(HOME, WORLD)
    assert out.status == "arrived" and out.tier == "AFFORDANCE", f"{out.status}/{out.tier}: {out.detail}"
    assert drv.tab == 1  # actually on the World tab


def test_deep_screen_with_global_inbound_edge_does_not_falsely_arrive_on_the_section():
    # ADVERSARIAL re-review break: a DEEP screen (not the tab's landing) carries a
    # global_affordance inbound edge value "World". On the World LANDING (World tab selected
    # but NOT the deep screen), the "already on section" check must NOT confidently arrive at
    # the deep node — the affordance proves only the SECTION, not the node. arrived_unverified
    # / honest, NEVER a confident 'arrived'.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.navigate.navigator import Navigator
    P = "com.deep"; NS3 = f"{P}/.Main"

    def scr(active, body, extra=""):
        bar = "".join(
            f'<node class="android.widget.LinearLayout" package="{P}" content-desc="{n}" '
            f'clickable="{"false" if i==active else "true"}" selected="{"true" if i==active else "false"}" '
            f'bounds="[{300+i*200},2800][{480+i*200},2980]"/>' for i, n in enumerate(("Home","World")))
        return (f'<hierarchy><node class="android.widget.FrameLayout" package="{P}" bounds="[0,0][1440,3120]">'
                f'<node class="android.widget.TextView" package="{P}" text="{body}" bounds="[40,200][1040,400]"/>{extra}'
                f'<node class="com.google.android.material.tabs.TabLayout" package="{P}" '
                f'resource-id="{P}:id/tabs" bounds="[270,2800][1170,3000]">{bar}</node></node></hierarchy>')

    world_landing = scr(1, "World cities list")
    deep_save = scr(1, "Save city form", extra=f'<node class="android.widget.EditText" package="{P}" resource-id="{P}:id/name" bounds="[40,600][1040,720]"/>')
    HOME = fingerprint(NS3, scr(0, "Home")); WLAND = fingerprint(NS3, world_landing); DEEP = fingerprint(NS3, deep_save)
    g = Graph()
    for sid, x, act in ((HOME, scr(0, "Home"), ".Main"), (WLAND, world_landing, ".Main"), (DEEP, deep_save, ".Main")):
        g.upsert_screen(Screen(id=sid, namespace=NS3, package=P, activity=act,
                               structure_id=structure_id(NS3, x),
                               force_action=(ForceAction("am_start", NS3, verified_fp=HOME) if sid == HOME else None)))
    # World landing is the tab's landing (reached by the global tab tap)
    g.add_transition(Transition(source=HOME, target=WLAND,
                                action=Action(selector=Selector("content_desc", "World"),
                                              action_type="click", bounds=(500, 2800, 680, 2980)),
                                global_affordance=True))
    # DEEP is a genuine deeper screen reached by a CONTENT tap from the landing (non-global),
    # but ALSO (spuriously / app-state) carries a global "World" inbound edge — the break's premise
    g.add_transition(Transition(source=WLAND, target=DEEP,
                                action=Action(selector=Selector("content_desc", "Add city"),
                                              action_type="click", bounds=(40, 600, 200, 700))))
    g.add_transition(Transition(source=HOME, target=DEEP,
                                action=Action(selector=Selector("content_desc", "World"),
                                              action_type="click", bounds=(500, 2800, 680, 2980)),
                                global_affordance=True))

    class App(FakeDriver):
        def __init__(self): super().__init__(display=(1440, 3120)); self.x = world_landing
        def dump_hierarchy(self): return self.x
        def dumps(self): return (f"topResumedActivity: ActivityRecord{{x u0 {NS3} t1}}", f"mCurrentFocus=Window{{x u0 {NS3}}}")
        def resolve_and_tap(self, sel, at="click", to=5.0):
            self.taps.append((sel.kind, sel.value, at)); return True
    drv = App(); t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    def obs():
        from wendle.record.observe import observe_settled
        x, ns, s, f = observe_settled(drv, __import__("threading").Lock(), **NOSLEEP); return x, ns, f, s
    nav._observe = obs
    out = nav.navigate(HOME, DEEP)
    # we are on the World LANDING, target is the DEEP save form -> must NOT confidently arrive
    assert out.status != "arrived" or out.tier != "AFFORDANCE", \
        f"CONFIDENT-WRONG: claimed {out.status}/{out.tier} on the section landing, not the deep node"
