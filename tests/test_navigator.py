from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.models import DeviceProfile
from wendle.navigate.navigator import MAX_RESTARTS, navigate
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(
    touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
    display=(1080, 2340), touch_protocol="type_b",
)
NOSLEEP = {"sleep": lambda _dt: None}
L = ("com.sec.android.app.launcher", ".activities.LauncherActivity")
A = ("com.app", ".AActivity")
B = ("com.app", ".BActivity")


def _screen(pkg, act, rid="ok", extra=""):
    return (
        f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{rid}" '
        f'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/>{extra}</node></hierarchy>'
    )


def _dumpsys(pkg, act):
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _tap():
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560)


def _record_home_a_b():
    """Record launcher -> A -> B. A is entered from home, so it gets an am_start anchor."""
    drv = FakeDriver(
        hierarchies=[_screen(*L)] * 3 + [_screen(*A)] * 3 + [_screen(*B)] * 3,
        dumpsys_pairs=[_dumpsys(*L)] * 3 + [_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
        display=(1080, 2340),
    )
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap())  # L -> A (A becomes am_start anchor)
    s.record_gesture(_tap())  # A -> B
    g = s.graph
    a_id = next(n for n in g.g.nodes if g.screen(n).namespace == "com.app/.AActivity")
    b_id = next(n for n in g.g.nodes if g.screen(n).namespace == "com.app/.BActivity")
    return g, a_id, b_id


def _fake_clock(nav_kwargs=None):
    """Injectable clock/sleep so the ladder's launch timeouts elapse at zero wall-time."""
    t = [0.0]
    kw = dict(nav_kwargs or {})
    kw.update(clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    return kw


def _gem_graph():
    """Launcher -> Gemini (shared-package launcher entry): the icon tap is the ONLY reach."""
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    GPKG = "com.google.android.googlequicksearchbox"
    GNS = f"{GPKG}/.GeminiAlias"
    BNS = f"{GPKG}/.GeminiChat"
    gxml = _screen(GPKG, ".GeminiAlias", rid="gem")
    bxml = _screen(GPKG, ".GeminiChat", rid="chat")
    gid, bid = fingerprint(GNS, gxml), fingerprint(BNS, bxml)
    g = Graph()
    g.upsert_screen(Screen(id="L", namespace=f"{L[0]}/{L[1]}", package=L[0], activity=L[1],
                           screen_type="homescreen",
                           force_action=ForceAction("keyevent", "3", verified_fp="L")))
    g.upsert_screen(Screen(id=gid, namespace=GNS, structure_id=structure_id(GNS, gxml),
                           package=GPKG, activity=".GeminiAlias",
                           force_action=ForceAction("am_start", GNS, verified_fp=gid)))
    g.upsert_screen(Screen(id=bid, namespace=BNS, structure_id=structure_id(BNS, bxml),
                           package=GPKG, activity=".GeminiChat"))
    g.add_transition(Transition(source="L", target=gid,
                                action=Action(selector=Selector("content_desc", "Gemini"), action_type="click")))
    g.add_transition(Transition(source=gid, target=bid,
                                action=Action(selector=Selector("text", "Go"), action_type="click")))
    return g, gid, bid, gxml, bxml, GPKG, GNS


def test_app_entered_from_home_gets_am_start_anchor():
    g, a_id, _ = _record_home_a_b()
    fa = g.screen(a_id).force_action
    assert fa is not None and fa.kind == "am_start" and fa.value == "com.app/.AActivity"
    assert a_id in g.anchors()


def test_self_routing_anchor_launches_by_package_never_the_activity():
    # BanCoppel: the recorder DEFERRED the anchor past a splash (provenance=self_routing,
    # RULE 2 re-points `value` at the observed first screen), so the ladder must never
    # `am start -n` the recorded activity — the package default routes the splash in.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    nsA, nsB = "com.app/.AActivity", "com.app/.BActivity"
    xmlA, xmlB = _screen(*A), _screen(*B)
    aid, bid = fingerprint(nsA, xmlA), fingerprint(nsB, xmlB)
    g = Graph()  # no launcher edge -> icon_tap is not applicable; package_default lands
    g.upsert_screen(Screen(id=aid, namespace=nsA, structure_id=structure_id(nsA, xmlA),
                           package="com.app", activity=".AActivity",
                           force_action=ForceAction("am_start", nsA, verified_fp=aid,
                                                    provenance="self_routing")))
    g.upsert_screen(Screen(id=bid, namespace=nsB, structure_id=structure_id(nsB, xmlB),
                           package="com.app", activity=".BActivity"))
    g.add_transition(Transition(source=aid, target=bid,
                                action=Action(selector=Selector("text", "Go"), action_type="click")))
    W = ("com.other", ".Splash")
    drv = FakeDriver(hierarchies=[_screen(*W)] * 3 + [xmlA] * 3 + [xmlB] * 3,
                     dumpsys_pairs=[_dumpsys(*W)] * 3 + [_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
                     present_selectors={("text", "Go")}, display=(1080, 2340))
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "arrived"
    assert drv.app_starts == [("com.app", None, True)]  # package only; activity NEVER am-started


def test_no_confident_exact_arrival_on_volatile_twin():
    # Cardinal-sin guard (BanCoppel welcome): the app's launch/welcome screen is recorded
    # VOLATILE, and the post-flow target is a SETTLED screen with the SAME text-free skeleton
    # -> structural twins. Cold-launching lands on the welcome, which fingerprints to the
    # target's id, so the navigator must NOT claim 'arrived EXACT' (it never ran the flow) —
    # a volatile twin means the EXACT id is not unique -> honest arrived_unverified.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    ns_w = "com.app/.WelcomeActivity"
    w = _screen("com.app", ".WelcomeActivity", rid="welcome")
    struct = structure_id(ns_w, w)
    t_id = fingerprint(ns_w, w)               # settled target fingerprint (post-flow welcome)
    twin_id = "V" + struct[1:]                # volatile launch/welcome twin, same skeleton

    g = Graph()
    twin = Screen(id=twin_id, namespace=ns_w, structure_id=struct, package="com.app",
                  activity=".WelcomeActivity", volatile=True,
                  force_action=ForceAction("am_start", "com.app/.WelcomeActivity", verified_fp=twin_id),
                  actions=[Action(selector=Selector("text", "Go"), action_type="click")])
    target = Screen(id=t_id, namespace=ns_w, structure_id=struct, package="com.app",
                    activity=".WelcomeActivity")
    g.upsert_screen(twin)
    g.upsert_screen(target)
    g.add_transition(Transition(source=twin_id, target=t_id,
                                action=Action(selector=Selector("text", "Go"), action_type="click")))
    drv = FakeDriver(hierarchies=[w] * 4, dumpsys_pairs=[_dumpsys(*("com.app", ".WelcomeActivity"))] * 4,
                     present_selectors={("text", "Go")}, display=(1080, 2340))
    out = navigate(g, twin_id, t_id, drv, settle_kwargs=NOSLEEP)
    # the live welcome EXACT-matches the target id, but the volatile twin makes it ambiguous
    assert out.status == "arrived_unverified"
    assert out.status != "arrived"  # NEVER a confident wrong arrival


def _arrival_case(target_id, screens, edges, here_xml, here_ns, seed_from):
    """Build a graph + a Navigator whose live screen (_observe) is fixed, and navigate to
    target_id. Returns the NavOutcome. Shared scaffolding for the twin/false-arrival matrix."""
    from wendle.graph import Graph
    from wendle.navigate.navigator import Navigator

    g = Graph()
    for s in screens:
        g.upsert_screen(s)
    for e in edges:
        g.add_transition(e)
    nav = Navigator(g, FakeDriver(hierarchies=[here_xml], dumpsys_pairs=[(here_ns, here_ns)],
                                  present_selectors={("text", "go"), ("text", "Entendido")}))
    nav._observe = lambda: (here_xml, here_ns, here_ns.split("/")[0], True)
    return nav.navigate(seed_from, target_id)


def test_no_confident_arrival_on_skeleton_drift_volatile_twin():
    # Opus review #1: a volatile launch/welcome recorded WITH a transient spinner has a
    # structure_id (Su) that DIFFERS from the one it settles into (Ss == the target's). Keying
    # ambiguity on structure_id alone misses it; a volatile node in the target's ACTIVITY is a
    # settle-collision risk, so a bare am_start onto the welcome must NOT be a confident arrival.
    from wendle.fingerprint.compose import VIEW_PROFILE
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    nsw = "mx.com.miapp/.ui.welcome.WelcomeActivity"
    settled = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
               'content-desc=""><node class="android.widget.TextView" resource-id="app:id/t" '
               'clickable="false" content-desc="" text="Bienvenido"/><node class="android.widget.Button" '
               'resource-id="app:id/ok" clickable="true" content-desc="" text="Entendido"/></node></hierarchy>')
    unsettled = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
                 'content-desc=""><node class="android.widget.TextView" resource-id="app:id/t" clickable="false" '
                 'content-desc="" text=""/><node class="android.widget.ProgressBar" resource-id="app:id/spin" '
                 'clickable="false" content-desc=""/></node></hierarchy>')
    ss, su = structure_id(nsw, settled), structure_id(nsw, unsettled)
    assert ss != su  # skeleton drift is the whole point
    t_id = fingerprint(nsw, settled, VIEW_PROFILE)
    w = Screen(id="V" + su[1:], namespace=nsw, structure_id=su, package="mx.com.miapp",
               activity=".ui.welcome.WelcomeActivity", profile_name="volatile", volatile=True,
               force_action=ForceAction("am_start", nsw, verified_fp="V" + su[1:]))
    t = Screen(id=t_id, namespace=nsw, structure_id=ss, package="mx.com.miapp",
               activity=".ui.welcome.WelcomeActivity", profile_name="view")
    out = _arrival_case(t_id, [w, t],
                        [Transition(source=w.id, target=t.id,
                                    action=Action(selector=Selector("text", "Entendido"), action_type="click"))],
                        settled, nsw, w.id)
    assert out.status == "arrived_unverified"  # NOT a confident arrival on a bare launch


def test_no_confident_arrival_on_cross_profile_settled_twin():
    # Opus review #3: a SETTLED Compose sibling B shares the text-free structure_id of a
    # text-free target A; under A's view profile B's text drops -> fingerprint(B,view)==A.id.
    # Sitting on B must NOT be a confident EXACT arrival on A (the EXACT branch must honor the
    # structure twin, not only volatile twins).
    from wendle.fingerprint.compose import COMPOSE_PROFILE, VIEW_PROFILE
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    ns = "app/.Welcome"
    xml_a = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
             'content-desc=""><node class="android.widget.TextView" resource-id="app:id/t" clickable="false" '
             'content-desc="" text="Bienvenido"/></node></hierarchy>')
    xml_b = xml_a.replace("Bienvenido", "Hola")
    anc_xml = ('<hierarchy><node class="android.widget.LinearLayout" resource-id="app:id/anchor" '
               'clickable="false" content-desc=""/></hierarchy>')
    a_id = fingerprint(ns, xml_a, VIEW_PROFILE)
    b_id = fingerprint(ns, xml_b, COMPOSE_PROFILE)
    assert fingerprint(ns, xml_b, VIEW_PROFILE) == a_id and a_id != b_id  # B view-collides with A
    anc = Screen(id=fingerprint(ns, anc_xml, VIEW_PROFILE), namespace=ns, structure_id=structure_id(ns, anc_xml),
                 package="app", activity=".Welcome", profile_name="view",
                 force_action=ForceAction("am_start", ns, verified_fp="anc"))
    a = Screen(id=a_id, namespace=ns, structure_id=structure_id(ns, xml_a), package="app",
               activity=".Welcome", profile_name="view")
    b = Screen(id=b_id, namespace=ns, structure_id=structure_id(ns, xml_b), package="app",
               activity=".Welcome", profile_name="compose")
    go = Action(selector=Selector("text", "go"), action_type="click")
    out = _arrival_case(a_id, [anc, a, b],
                        [Transition(source=anc.id, target=a.id, action=go),
                         Transition(source=anc.id, target=b.id, action=go)],
                        xml_b, ns, anc.id)
    assert out.status == "arrived_unverified"  # on B, not A — never a confident wrong arrival


def test_compose_target_with_volatile_twin_stays_confident():
    # Opus review #2 (the over-downgrade guard): a Compose target whose id folds in greeting
    # text is GENUINELY unique — a text-free volatile twin can never carry it — so a real
    # arrival must stay confident EXACT, NOT be downgraded by the twin's mere existence.
    from wendle.fingerprint.compose import COMPOSE_PROFILE, VIEW_PROFILE
    from wendle.fingerprint.signature import (
        fingerprint,
        outside_region_value_bearing,
        structure_id,
    )
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    ns = "app/.ChatActivity"
    host = "androidx.compose.ui.platform.AndroidComposeView"
    settled = (f'<hierarchy><node class="{host}" resource-id="" clickable="false" content-desc="">'
               '<node class="android.view.View" resource-id="" clickable="false" content-desc="" text="Hola Christian"/>'
               '<node class="android.view.View" resource-id="" clickable="true" content-desc="" text="Send"/></node></hierarchy>')
    loading = settled.replace("Hola Christian", "").replace("Send", "")
    nsa = "app/.Home"
    anc_xml = '<hierarchy><node class="android.widget.LinearLayout" resource-id="app:id/a" clickable="false" content-desc=""/></hierarchy>'
    t_id = fingerprint(ns, settled, COMPOSE_PROFILE)  # folds in "Hola Christian"
    su = structure_id(ns, loading)
    anc = Screen(id=fingerprint(nsa, anc_xml, VIEW_PROFILE), namespace=nsa, structure_id=structure_id(nsa, anc_xml),
                 package="app", activity=".Home", profile_name="view",
                 force_action=ForceAction("am_start", nsa, verified_fp="anc"))
    w = Screen(id="V" + su[1:], namespace=ns, structure_id=su, package="app", activity=".ChatActivity",
               profile_name="volatile", volatile=True)  # benign volatile twin (loading chat)
    # L3 migration: confidence now keys on the RECORDED value-evidence bit. The greeting text
    # lives outside any adapter region, so the bit is True by construction — computed exactly
    # as the recorder computes it, not asserted by fiat.
    t_vb = outside_region_value_bearing(settled, COMPOSE_PROFILE)
    assert t_vb is True
    t = Screen(id=t_id, namespace=ns, structure_id=structure_id(ns, settled), package="app",
               activity=".ChatActivity", profile_name="compose", value_bearing=t_vb)
    assert w.structure_id == t.structure_id  # they share the text-free skeleton
    out = _arrival_case(t_id, [anc, w, t],
                        [Transition(source=anc.id, target=t.id,
                                    action=Action(selector=Selector("text", "go"), action_type="click"))],
                        settled, ns, anc.id)
    assert out.status == "arrived" and out.tier == "EXACT"  # genuinely unique -> stays confident


def test_navigate_arrives():
    g, a_id, b_id = _record_home_a_b()
    drv = FakeDriver(
        hierarchies=[_screen(*A)] * 3 + [_screen(*B)] * 3,
        dumpsys_pairs=[_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"
    assert ("label", "Go", "click") in drv.taps


def test_dynamic_anchor_verifies_via_structure_tier():
    # A dynamic Compose anchor: its title text changes at replay so EXACT (text-
    # sensitive Compose profile) won't reproduce, but the text-free structure_id does
    # -> the STRUCTURE verify tier accepts it (replaces the old no-probe namespace hack).
    from wendle.fingerprint.compose import COMPOSE_PROFILE
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    def compose(label):
        return (
            '<hierarchy><node class="androidx.compose.ui.platform.AndroidComposeView" '
            'package="com.app" resource-id="" clickable="false" content-desc="" text="" '
            'bounds="[0,0][1080,2340]">'
            f'<node class="android.view.View" package="com.app" resource-id="com.app:id/cta" '
            f'clickable="true" content-desc="" text="{label}" bounds="[40,500][1040,620]"/>'
            "</node></hierarchy>"
        )

    morning, afternoon = compose("Good morning"), compose("Good afternoon")
    ns_a, ns_b = "com.app/.AActivity", "com.app/.BActivity"
    b_xml = _screen(*B)
    a_id = fingerprint(ns_a, morning, COMPOSE_PROFILE)
    g = Graph()
    a = Screen(
        id=a_id,
        namespace=ns_a,
        structure_id=structure_id(ns_a, morning),
        package="com.app",
        activity=".AActivity",
        profile_name="compose",
        force_action=ForceAction("am_start", "com.app/.AActivity", verified_fp=a_id),
        actions=[Action(selector=Selector("resource_id", "com.app:id/cta"), action_type="click")],
    )
    b = Screen(id=fingerprint(ns_b, b_xml), namespace=ns_b, structure_id=structure_id(ns_b, b_xml))
    g.upsert_screen(a)
    g.upsert_screen(b)
    g.add_transition(Transition(
        source=a.id, target=b.id,
        action=Action(selector=Selector("resource_id", "com.app:id/cta"), action_type="click"),
    ))
    drv = FakeDriver(
        hierarchies=[afternoon] * 3 + [_screen(*B)] * 3,
        dumpsys_pairs=[_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
        present_selectors={("resource_id", "com.app:id/cta")},
        display=(1080, 2340),
    )
    out = navigate(g, a.id, b.id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"  # EXACT failed on changed title, STRUCTURE accepted


def test_force_failed_on_wrong_namespace():
    g, a_id, b_id = _record_home_a_b()
    WRONG = ("com.other", ".Splash")
    drv = FakeDriver(
        hierarchies=[_screen(*WRONG)] * 3,
        dumpsys_pairs=[_dumpsys(*WRONG)] * 3,
        display=(1080, 2340),
    )
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "force_failed"


def test_off_graph_stops_and_reports():
    # snap-back relaunches LAND (the anchor works) but the screen keeps drifting to an
    # UNKNOWN screen IN THE SAME APP -> bounded recovery -> honest off_graph (the loop is
    # provably not getting closer; it never claims arrival).
    g, a_id, b_id = _record_home_a_b()
    WRONG = ("com.app", ".WrongActivity")
    h, d = [_screen(*A)] * 3, [_dumpsys(*A)] * 3
    for _ in range(MAX_RESTARTS + 1):
        h += [_screen(*WRONG)] * 3 + [_screen(*A)] * 3
        d += [_dumpsys(*WRONG)] * 3 + [_dumpsys(*A)] * 3
    drv = FakeDriver(
        hierarchies=h + [_screen(*WRONG)] * 6,
        dumpsys_pairs=d + [_dumpsys(*WRONG)] * 6,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "off_graph"
    assert out.expected_id == b_id


def test_wrong_package_gate_recovers_then_arrives():
    # first observation lands in a FOREIGN app; the gate re-forces the target-package
    # anchor, then the route proceeds normally.
    g, a_id, b_id = _record_home_a_b()
    FOREIGN = ("com.other", ".Splash")
    drv = FakeDriver(
        hierarchies=[_screen(*FOREIGN)] * 3 + [_screen(*A)] * 3 + [_screen(*B)] * 3,
        dumpsys_pairs=[_dumpsys(*FOREIGN)] * 3 + [_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
        present_selectors={("text", "Go")},
        display=(1080, 2340),
    )
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "arrived"
    # ONE gated re-force (observe-first: the foreign screen triggered recovery, nothing else)
    assert drv.app_starts == [("com.app", ".AActivity", True)]


def test_content_drift_when_outbound_selector_vanishes():
    # we are confidently on A (EXACT), but the recorded A->B selector no longer resolves
    g, a_id, b_id = _record_home_a_b()
    drv = FakeDriver(
        hierarchies=[_screen(*A)] * 30,
        dumpsys_pairs=[_dumpsys(*A)] * 30,
        present_selectors=set(),  # the "Go" selector is gone
        display=(1080, 2340),
    )
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "content_drift"
    assert out.expected_id == b_id


def test_adapter_list_target_arrives_unverified():
    # target is a list-dominant volatile screen; structure matches but the navigator
    # must NOT claim a confident arrival (could be any sibling) -> arrived_unverified
    import hashlib

    from wendle.fingerprint.signature import structure_id
    from wendle.graph import Graph
    from wendle.models import ForceAction, Screen

    def listxml(items):
        rows = "".join(
            f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row" '
            f'clickable="true" content-desc="" text="item {i}" bounds="[0,{i*100}][1080,{i*100+100}]"/>'
            for i in range(items)
        )
        return (
            '<hierarchy><node class="androidx.recyclerview.widget.RecyclerView" package="com.app" '
            'resource-id="com.app:id/list" clickable="false" content-desc="" text="" '
            f'bounds="[0,0][1080,2340]">{rows}</node></hierarchy>'
        )

    ns = "com.app/.FeedActivity"
    vid = "V" + hashlib.sha1(ns.encode()).hexdigest()[:15]
    g = Graph()
    feed = Screen(
        id=vid, namespace=ns, structure_id=structure_id(ns, listxml(3)),
        package="com.app", activity=".FeedActivity", profile_name="volatile", volatile=True,
        force_action=ForceAction("am_start", "com.app/.FeedActivity", verified_fp=vid),
    )
    g.upsert_screen(feed)
    drv = FakeDriver(
        hierarchies=[listxml(7)] * 3,
        dumpsys_pairs=[_dumpsys(*ns.split("/"))] * 3,
        display=(1080, 2340),
    )
    out = navigate(g, feed.id, feed.id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived_unverified"


def test_merge_screens_redirects_edges():
    g, a_id, b_id = _record_home_a_b()
    from wendle.models import Action, Screen, Selector, Transition

    g.upsert_screen(Screen(id="b2dup", namespace="com.app/.BActivity"))
    g.add_transition(Transition(source="b2dup", target=a_id,
                                action=Action(selector=Selector("text", "Home"), action_type="click")))
    g.merge_screens(b_id, "b2dup")
    assert "b2dup" not in g.g.nodes
    assert g.g.has_edge(b_id, a_id)


def test_navigate_routes_a_swipe_edge_and_swipes_not_taps():
    # The honesty bug the shared ActionExecutor fixes: the navigator used to have no swipe branch,
    # so routing over a recorded SWIPE edge tapped the element CENTER (a confident-wrong action).
    # Now it actually swipes.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    nsA, nsB = "com.app/.A", "com.app/.B"
    xmlA, xmlB = _screen("com.app", ".A", rid="a"), _screen("com.app", ".B", rid="b")
    aid, bid = fingerprint(nsA, xmlA), fingerprint(nsB, xmlB)
    g = Graph()
    g.upsert_screen(Screen(id=aid, namespace=nsA, structure_id=structure_id(nsA, xmlA), package="com.app",
                           activity=".A", force_action=ForceAction("am_start", nsA, verified_fp=aid)))
    g.upsert_screen(Screen(id=bid, namespace=nsB, structure_id=structure_id(nsB, xmlB),
                           package="com.app", activity=".B"))
    g.add_transition(Transition(
        source=aid, target=bid, action_class="swipe",
        action=Action(selector=Selector("coords", (500, 1500)), action_type="swipe", end=(500, 400))))
    drv = FakeDriver(
        hierarchies=[xmlA] * 3 + [xmlB] * 3,
        dumpsys_pairs=[_dumpsys("com.app", ".A")] * 3 + [_dumpsys("com.app", ".B")] * 3,
        display=(1080, 2340),
    )
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"
    assert drv.swipes == [((500, 1500), (500, 400))]  # SWIPED the recorded edge (the bug fix)
    assert drv.taps == []                              # never tapped an element center


def _ab(action, pre_actions=None):
    """A 2-screen graph A --action--> B (A is the am_start anchor), plus the A/B hierarchies."""
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import ForceAction, Screen, Transition

    nsA, nsB = "com.app/.A", "com.app/.B"
    xmlA, xmlB = _screen("com.app", ".A", rid="a"), _screen("com.app", ".B", rid="b")
    aid, bid = fingerprint(nsA, xmlA), fingerprint(nsB, xmlB)
    g = Graph()
    g.upsert_screen(Screen(id=aid, namespace=nsA, structure_id=structure_id(nsA, xmlA), package="com.app",
                           activity=".A", force_action=ForceAction("am_start", nsA, verified_fp=aid)))
    g.upsert_screen(Screen(id=bid, namespace=nsB, structure_id=structure_id(nsB, xmlB),
                           package="com.app", activity=".B"))
    g.add_transition(Transition(source=aid, target=bid, action=action, pre_actions=pre_actions or []))
    return g, aid, bid, xmlA, xmlB


def test_navigate_routes_a_keyevent_edge():
    # the keyevent crash fix end-to-end: routing a keyevent edge issues the key (does not crash).
    from wendle.models import Action, Selector
    g, aid, bid, xmlA, xmlB = _ab(Action(selector=Selector("keyevent", 4), action_type="keyevent"))
    drv = FakeDriver(hierarchies=[xmlA] * 3 + [xmlB] * 3,
                     dumpsys_pairs=[_dumpsys("com.app", ".A")] * 3 + [_dumpsys("com.app", ".B")] * 3,
                     display=(1080, 2340))
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived" and drv.keyevents == [4]


def test_failing_pre_action_stops_before_the_submit_edge():
    # honesty fix: a pre_action that fails (a checkbox that won't flip) STOPS with content_drift and
    # does NOT fire the submit tap (the old loop swallowed an (ok=False, error=None) pre_action).
    from wendle.models import Action, Selector
    g, aid, bid, xmlA, _ = _ab(
        Action(selector=Selector("text", "Submit"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "app:id/box"), action_type="set_checked",
                            value={"checked": True})])
    drv = FakeDriver(hierarchies=[xmlA] * 4, dumpsys_pairs=[_dumpsys("com.app", ".A")] * 4,
                     present_selectors={("text", "Submit")}, display=(1080, 2340))
    drv.checked_fail.add("app:id/box")
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)
    assert out.status == "content_drift"
    assert ("text", "Submit", "click") not in drv.taps  # submit NEVER fired


def test_routed_set_checked_edge_drift_is_content_drift():
    # a routed set_checked edge whose target vanished -> content_drift (was a mislabeled refusal).
    from wendle.models import Action, Selector
    g, aid, bid, xmlA, _ = _ab(Action(selector=Selector("resource_id", "app:id/box"),
                                       action_type="set_checked", value={"checked": True}))
    drv = FakeDriver(hierarchies=[xmlA] * 4, dumpsys_pairs=[_dumpsys("com.app", ".A")] * 4, display=(1080, 2340))
    drv.checked_raises.add("app:id/box")
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)
    assert out.status == "content_drift"


def test_coords_tap_edge_refused_end_to_end():
    from wendle.models import Action, Selector
    g, aid, bid, xmlA, _ = _ab(Action(selector=Selector("coords", (500, 800)), action_type="click"))
    drv = FakeDriver(hierarchies=[xmlA] * 4, dumpsys_pairs=[_dumpsys("com.app", ".A")] * 4, display=(1080, 2340))
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)
    assert out.status == "coordinate_only_refused" and drv.taps == []


def test_sensitive_pre_action_without_param_is_credential_required():
    from wendle.models import Action, Selector
    g, aid, bid, xmlA, _ = _ab(
        Action(selector=Selector("text", "Login"), action_type="click"),
        pre_actions=[Action(selector=Selector("resource_id", "app:id/pw"), action_type="set_text",
                            value={"param": "pw"}, sensitive=True)])
    drv = FakeDriver(hierarchies=[xmlA] * 4, dumpsys_pairs=[_dumpsys("com.app", ".A")] * 4,
                     present_selectors={("text", "Login")}, display=(1080, 2340))
    out = navigate(g, aid, bid, drv, settle_kwargs=NOSLEEP)  # no params provided
    assert out.status == "credential_required"
    assert ("text", "Login", "click") not in drv.taps  # submit never fired


def test_navigate_to_anchor_target_forces_it_directly():
    # to-node IS an anchor (e.g. launcher) -> force it directly (deferred HomePress),
    # never walk recorded edges backward.
    g, a_id, b_id = _record_home_a_b()
    launcher_id = next(n for n in g.g.nodes if g.screen(n).screen_type == "homescreen")
    drv = FakeDriver(
        hierarchies=[_screen(*B)] * 3 + [_screen(*L)] * 3,
        dumpsys_pairs=[_dumpsys(*B)] * 3 + [_dumpsys(*L)] * 3,
        display=(1080, 2340),
    )
    out = navigate(g, b_id, launcher_id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"
    assert drv.keyevents == [3]  # pressed home, did not walk a recorded edge
    assert drv.taps == []


def test_actual_node_strict_rejects_what_the_arrival_gate_would():
    # STRICT mode (hook keying) mirrors navigate()'s own arrival discipline (adversarial
    # blocker 2): an EXACT match is trusted only when NO twin could carry the fingerprint —
    # a VOLATILE same-namespace sibling could settle into a text-free id, so strict returns
    # None rather than bind injected code to a guess. The navigator's non-strict best-guess
    # routing is unchanged.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Screen
    from wendle.navigate.navigator import Navigator
    from wendle.navigate.verify import config_for

    xml = _screen(*A)
    ns = "com.app/.AActivity"
    g = Graph()
    t = Screen(id="tmp", namespace=ns, package="com.app", activity=".AActivity",
               structure_id=structure_id(ns, xml))
    t.id = fingerprint(ns, xml, config_for(t), "com.app")  # a genuinely EXACT-matchable id
    g.upsert_screen(t)
    nav = Navigator(g, FakeDriver())
    # unique text-free screen -> strict resolves it (EXACT, unambiguous)
    assert nav._actual_node(xml, ns, "com.app", g.routable_subgraph(), strict=True) == t.id
    # a volatile same-namespace sibling makes the EXACT id non-unique -> strict refuses
    g.upsert_screen(Screen(id="Vx", namespace=ns, package="com.app", activity=".AActivity",
                           volatile=True, structure_id="Vdifferent"))
    G2 = g.routable_subgraph()
    assert nav._actual_node(xml, ns, "com.app", G2, strict=True) is None
    assert nav._actual_node(xml, ns, "com.app", G2) == t.id  # best-guess path preserved


def test_navigator_reaches_shared_package_app_via_icon_tap():
    # THE task-#6 case: Gemini's recorded component is non-exported and the package default
    # opens Google Search — only the recorded icon GESTURE reaches the entry. The navigator,
    # now on the ladder, gets that reach (it used to package-launch and never arrive).
    g, gid, bid, gxml, bxml, GPKG, GNS = _gem_graph()
    W = ("com.other", ".Splash")
    drv = FakeDriver(
        hierarchies=[_screen(*W)] * 3 + [gxml] * 3 + [bxml] * 3,
        dumpsys_pairs=[_dumpsys(*W)] * 3 + [_dumpsys(GPKG, ".GeminiAlias")] * 3
                      + [_dumpsys(GPKG, ".GeminiChat")] * 3,
        present_selectors={("content_desc", "Gemini"), ("text", "Go")},
        display=(1080, 2340),
    )
    drv.app_start_raises.add((GPKG, ".GeminiAlias"))  # non-exported -> am start -n refused
    out = navigate(g, gid, bid, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "arrived"
    assert ("content_desc", "Gemini", "click") in drv.taps   # entered via the recorded icon
    assert drv.app_starts == [(GPKG, ".GeminiAlias", True)]  # refused try only; NO package default
    assert 3 in drv.keyevents                                # icon rung pressed HOME first


def test_ladder_exhaustion_maps_to_force_failed():
    # The contract: an exhausted ladder is an honest force_failed (with the ladder's reason),
    # never a silent re-force of the same broken launch.
    g, a_id, b_id = _record_home_a_b()
    WRONG = ("com.other", ".Splash")
    drv = FakeDriver(hierarchies=[_screen(*WRONG)] * 40, dumpsys_pairs=[_dumpsys(*WRONG)] * 40,
                     display=(1080, 2340))
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "force_failed"
    assert "exhausted" in out.detail


def test_navigate_does_not_force_stop_when_already_in_app():
    # Observe-first: navigating within an app we are ALREADY inside must not cold-stop it.
    g, a_id, b_id = _record_home_a_b()
    drv = FakeDriver(hierarchies=[_screen(*A)] * 3 + [_screen(*B)] * 3,
                     dumpsys_pairs=[_dumpsys(*A)] * 3 + [_dumpsys(*B)] * 3,
                     present_selectors={("text", "Go")}, display=(1080, 2340))
    out = navigate(g, a_id, b_id, drv, settle_kwargs=NOSLEEP)
    assert out.status == "arrived"
    assert drv.app_starts == [] and drv.keyevents == []  # no prologue force-stop


def test_winning_rung_cache_survives_a_mid_route_restart():
    # First force lands Gemini via the icon (component refused). A mid-route hijack to a
    # foreign app triggers a re-force: the cache must go straight to the icon — the refused
    # component (whose stop=True kills the shared package's state) is NEVER re-issued.
    g, gid, bid, gxml, bxml, GPKG, GNS = _gem_graph()
    W, F = ("com.other", ".Splash"), ("com.foreign", ".Hijack")
    drv = FakeDriver(
        hierarchies=[_screen(*W)] * 3 + [gxml] * 3 + [_screen(*F)] * 3 + [gxml] * 3 + [bxml] * 3,
        dumpsys_pairs=[_dumpsys(*W)] * 3 + [_dumpsys(GPKG, ".GeminiAlias")] * 3
                      + [_dumpsys(*F)] * 3 + [_dumpsys(GPKG, ".GeminiAlias")] * 3
                      + [_dumpsys(GPKG, ".GeminiChat")] * 3,
        present_selectors={("content_desc", "Gemini"), ("text", "Go")},
        display=(1080, 2340),
    )
    drv.app_start_raises.add((GPKG, ".GeminiAlias"))
    out = navigate(g, gid, bid, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "arrived"
    assert drv.app_starts == [(GPKG, ".GeminiAlias", True)]            # ONE refused try, ever
    assert drv.taps.count(("content_desc", "Gemini", "click")) == 2    # icon used both times


def _toolbar_list(title, rows):
    """A toolbar + RecyclerView screen: VIEW-profile fingerprints drop the text and collapse
    the adapter rows, so two VISIBLY different folders (Inbox vs Archive) share one id —
    the unrecorded-twin shape (single-Activity app) the adversarial review demonstrated."""
    items = "".join(
        f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row" '
        f'clickable="true" content-desc="" text="{t}" bounds="[0,{200 + i * 100}][1080,{300 + i * 100}]"/>'
        for i, t in enumerate(rows))
    return ('<hierarchy><node class="android.widget.LinearLayout" package="com.app" '
            'resource-id="com.app:id/toolbar" clickable="false" content-desc="" text="">'
            f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/title" '
            f'clickable="false" content-desc="" text="{title}"/></node>'
            '<node class="androidx.recyclerview.widget.RecyclerView" package="com.app" '
            f'resource-id="com.app:id/list" clickable="false" content-desc="" text="" '
            f'bounds="[0,200][1080,2200]">{items}</node></hierarchy>')


def _single_activity_graph():
    """HOME --tap'Inbox'--> INBOX, every screen in ONE namespace (the modern single-Activity
    norm). Returns (graph, home_id, inbox_id, home_xml, inbox_xml, archive_xml) where ARCHIVE
    is an UNRECORDED twin carrying INBOX's text-free fingerprint."""
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition

    NS = "com.app/.MainActivity"
    home_xml = _screen("com.app", ".MainActivity", rid="home").replace('text="Go"', 'text="Inbox"')
    inbox_xml = _toolbar_list("Inbox", ["hello", "world"])
    archive_xml = _toolbar_list("Archive", ["old", "older", "oldest"])
    assert fingerprint(NS, inbox_xml) == fingerprint(NS, archive_xml)  # the twin premise
    hid, iid = fingerprint(NS, home_xml), fingerprint(NS, inbox_xml)
    g = Graph()
    g.upsert_screen(Screen(id=hid, namespace=NS, structure_id=structure_id(NS, home_xml),
                           package="com.app", activity=".MainActivity",
                           force_action=ForceAction("am_start", NS, verified_fp=hid)))
    g.upsert_screen(Screen(id=iid, namespace=NS, structure_id=structure_id(NS, inbox_xml),
                           package="com.app", activity=".MainActivity"))
    g.add_transition(Transition(source=hid, target=iid,
                                action=Action(selector=Selector("text", "Inbox"), action_type="click")))
    return g, hid, iid, home_xml, inbox_xml, archive_xml


def test_parked_unrecorded_twin_is_never_a_zero_evidence_confident_arrival():
    # Adversarial finding (HIGH): parked on an UNRECORDED same-namespace twin of the target
    # (Archive vs Inbox), the loop must NOT claim 'arrived' from the observation alone — a
    # text-free match could equally be a sibling. It must CORROBORATE: re-anchor (gated
    # launch) and WALK the recorded edge, arriving at the REAL target.
    from wendle.navigate.navigator import Navigator

    g, hid, iid, home_xml, inbox_xml, archive_xml = _single_activity_graph()
    NS = "com.app/.MainActivity"
    drv = FakeDriver(present_selectors={("text", "Inbox")})
    nav = Navigator(g, drv, **_fake_clock())

    def obs():
        if ("text", "Inbox", "click") in drv.taps:
            return (inbox_xml, NS, "com.app", True)
        if drv.app_starts:
            return (home_xml, NS, "com.app", True)
        return (archive_xml, NS, "com.app", True)  # parked on the unrecorded twin

    nav._observe = obs
    out = nav.navigate(hid, iid)
    assert out.status == "arrived"
    assert drv.app_starts == [("com.app", ".MainActivity", True)]  # corroborating re-anchor
    assert drv.taps == [("text", "Inbox", "click")]                # walked the recorded edge
    # zero-interaction confident arrival (the exploit) is structurally impossible now
    assert drv.app_starts or drv.taps


def test_uncorroborated_twin_arrival_degrades_to_unverified_when_reanchor_cannot_verify():
    # Same twin, but the seed anchor lives in ANOTHER activity and the app never relaunches:
    # every corroboration attempt exhausts, the post-exhaust world keeps fingerprinting as the
    # target. The loop must report arrived_unverified — NEVER a confident 'arrived', and NEVER
    # a blind action fired on the unverified screen.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.navigate.navigator import Navigator

    NS = "com.app/.MainActivity"
    ANS = "com.app/.EntryActivity"
    anchor_xml = _screen("com.app", ".EntryActivity", rid="entry")
    inbox_xml = _toolbar_list("Inbox", ["hello", "world"])
    archive_xml = _toolbar_list("Archive", ["old", "older", "oldest"])
    aid, iid = fingerprint(ANS, anchor_xml), fingerprint(NS, inbox_xml)
    g = Graph()
    g.upsert_screen(Screen(id=aid, namespace=ANS, structure_id=structure_id(ANS, anchor_xml),
                           package="com.app", activity=".EntryActivity",
                           force_action=ForceAction("am_start", ANS, verified_fp=aid)))
    g.upsert_screen(Screen(id=iid, namespace=NS, structure_id=structure_id(NS, inbox_xml),
                           package="com.app", activity=".MainActivity"))
    g.add_transition(Transition(source=aid, target=iid,
                                action=Action(selector=Selector("text", "Inbox"), action_type="click")))
    drv = FakeDriver(present_selectors={("text", "Inbox")})
    nav = Navigator(g, drv, **_fake_clock())
    nav._observe = lambda: (archive_xml, NS, "com.app", True)  # pinned on the twin, forever
    out = nav.navigate(aid, iid)
    assert out.status == "arrived_unverified"
    assert out.status != "arrived"  # NEVER confident on zero corroboration
    assert drv.taps == []           # and nothing was blindly fired


def test_exhausted_ladder_continues_from_a_recognized_screen():
    # Adversarial finding (HIGH): anchor-namespace drift (recorded logged-OUT Login anchor;
    # device logged-IN, every launch self-routes to Main). The gate rightly never passes and
    # the ladder exhausts — but the device IS on a known routable node. The loop must keep
    # routing from it (old-loop parity), not return a terminal force_failed.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.navigate.navigator import Navigator

    LNS, MNS, SNS = "com.app/.Login", "com.app/.Main", "com.app/.Sett"
    lx, mx, sx = (_screen("com.app", ".Login", rid="login"), _screen("com.app", ".Main", rid="main"),
                  _screen("com.app", ".Sett", rid="sett"))
    lid, mid, sid = fingerprint(LNS, lx), fingerprint(MNS, mx), fingerprint(SNS, sx)
    g = Graph()
    g.upsert_screen(Screen(id=lid, namespace=LNS, structure_id=structure_id(LNS, lx), package="com.app",
                           activity=".Login", force_action=ForceAction("am_start", LNS, verified_fp=lid)))
    g.upsert_screen(Screen(id=mid, namespace=MNS, structure_id=structure_id(MNS, mx),
                           package="com.app", activity=".Main"))
    g.upsert_screen(Screen(id=sid, namespace=SNS, structure_id=structure_id(SNS, sx),
                           package="com.app", activity=".Sett"))
    g.add_transition(Transition(source=lid, target=mid,
                                action=Action(selector=Selector("text", "Next"), action_type="click")))
    g.add_transition(Transition(source=mid, target=sid,
                                action=Action(selector=Selector("text", "Open"), action_type="click")))
    drv = FakeDriver(present_selectors={("text", "Next"), ("text", "Open")})
    nav = Navigator(g, drv, **_fake_clock())

    def obs():
        if ("text", "Open", "click") in drv.taps:
            return (sx, SNS, "com.app", True)
        if drv.app_starts:                      # ANY launch self-routes to logged-in Main
            return (mx, MNS, "com.app", True)
        return (_screen("com.other", ".X"), "com.other/.X", "com.other", True)  # start off-app

    nav._observe = obs
    out = nav.navigate(lid, sid)
    assert out.status == "arrived"
    assert drv.app_starts                        # the ladder genuinely tried (and exhausted)
    assert ("text", "Open", "click") in drv.taps  # then ROUTED from the recognized Main


def test_unrecognized_first_contact_recovers_via_gated_launch_never_blind_taps():
    # Adversarial finding (HIGH): single-Activity app parked on an unrecorded DEEP screen D
    # (same namespace as the seed). The loop must NOT bind D to the seed and fire the seed's
    # recorded tap on it — it recovers via ONE gated launch, then walks. Exactly one tap,
    # fired only after the verified launch.
    from wendle.fingerprint.signature import fingerprint, structure_id
    from wendle.graph import Graph
    from wendle.models import Action, ForceAction, Screen, Selector, Transition
    from wendle.navigate.navigator import Navigator

    NS = "com.app/.OneActivity"
    home_xml = _screen("com.app", ".OneActivity", rid="home")
    b_xml = _toolbar_list("Detail", ["row"])
    deep_xml = ('<hierarchy><node class="android.widget.GridLayout" package="com.app" resource-id='
                '"com.app:id/deep" clickable="false" content-desc="" text="">'
                '<node class="android.widget.Button" package="com.app" resource-id="com.app:id/x" '
                'clickable="true" content-desc="" text="Go" bounds="[0,0][100,100]"/></node></hierarchy>')
    hid, bid = fingerprint(NS, home_xml), fingerprint(NS, b_xml)
    g = Graph()
    g.upsert_screen(Screen(id=hid, namespace=NS, structure_id=structure_id(NS, home_xml),
                           package="com.app", activity=".OneActivity",
                           force_action=ForceAction("am_start", NS, verified_fp=hid)))
    g.upsert_screen(Screen(id=bid, namespace=NS, structure_id=structure_id(NS, b_xml),
                           package="com.app", activity=".OneActivity"))
    g.add_transition(Transition(source=hid, target=bid,
                                action=Action(selector=Selector("text", "Go"), action_type="click")))
    drv = FakeDriver(present_selectors={("text", "Go")})
    nav = Navigator(g, drv, **_fake_clock())

    def obs():
        if ("text", "Go", "click") in drv.taps:
            return (b_xml, NS, "com.app", True)
        if drv.app_starts:
            return (home_xml, NS, "com.app", True)
        return (deep_xml, NS, "com.app", True)  # parked deep, same namespace, 'Go' present

    nav._observe = obs
    out = nav.navigate(hid, bid)
    assert out.status == "arrived"
    assert drv.app_starts == [("com.app", ".OneActivity", True)]  # ONE gated recovery launch
    assert drv.taps == [("text", "Go", "click")]  # exactly one tap — never blind-fired on D


def test_no_route_from_current_reanchors_at_seed():
    # Recorded A->B only; navigate(B->A) has no recorded path from B. The loop must re-anchor
    # at the seed (A's am_start) and arrive — NOT report off_graph from a reachable state.
    # (Under force-as-prologue this worked by accident; now it's an explicit recovery rung.)
    g, a_id, b_id = _record_home_a_b()
    drv = FakeDriver(hierarchies=[_screen(*B)] * 3 + [_screen(*A)] * 3,
                     dumpsys_pairs=[_dumpsys(*B)] * 3 + [_dumpsys(*A)] * 3,
                     present_selectors={("text", "Go")}, display=(1080, 2340))
    out = navigate(g, b_id, a_id, drv, settle_kwargs=NOSLEEP, **_fake_clock())
    assert out.status == "arrived"
    assert drv.app_starts == [("com.app", ".AActivity", True)]  # the ladder's component rung landed
