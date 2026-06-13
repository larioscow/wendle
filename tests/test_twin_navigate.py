"""Task #17b-5: the navigator/verify resolve REFINED twins. A refined twin's id is
refined_id(coarse_fp, chrome_digest), so the EXACT comparator must reproduce it from the live
coarse fingerprint + live chrome digest. A refined twin is value-bearing (unique in the graph),
so it is confident on sight — EXCEPT on adapter-list-dominant pages where the digest reduces to
the title and a same-titled UNRECORDED sibling could collide (HW2): there it needs corroboration.
"""
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import (
    chrome_digest,
    fingerprint,
    has_collapsing_list,
    refined_id,
    structure_id,
)
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator
from wendle.navigate.verify import Tier, verify_match

NS = "com.app/.SubSettings"
FOCUS = "com.app"


def _page(title, n_rows=1):
    title = title.replace("&", "&amp;")
    rows = "".join(
        f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row{i}" '
        f'clickable="true" content-desc="" text="item {i}" bounds="[0,{400+i*80}][1080,{480+i*80}]"/>'
        for i in range(n_rows))
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/title" '
            f'clickable="false" content-desc="{title}" text="" bounds="[40,40][800,160]"/>'
            f'{rows}</node></hierarchy>')


def _list_page(title, n_rows=12):
    # adapter-list-DOMINANT: a toolbar title over a RecyclerView whose rows are most of the leaves.
    title = title.replace("&", "&amp;")
    rows = "".join(
        f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row" '
        f'clickable="true" content-desc="" text="item {i}" bounds="[0,{400+i*80}][1080,{480+i*80}]"/>'
        for i in range(n_rows))
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/title" '
            f'clickable="false" content-desc="{title}" text="" bounds="[40,40][800,160]"/>'
            '<node class="androidx.recyclerview.widget.RecyclerView" package="com.app" '
            f'resource-id="com.app:id/list" clickable="false" content-desc="" text="" '
            f'bounds="[0,400][1080,2300]">{rows}</node></node></hierarchy>')


def _refined_twin(xml):
    F = fingerprint(NS, xml, None, FOCUS)
    d = chrome_digest(xml, None, FOCUS)
    return Screen(id=refined_id(F, d), namespace=NS, structure_id=structure_id(NS, xml, FOCUS),
                  package="com.app", activity=".SubSettings", profile_name="view",
                  chrome_digest=d, coarse_id=F,
                  adapter_dominant=has_collapsing_list(xml, focus_pkg=FOCUS))  # as _enter records


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def test_verify_match_refined_exact_on_the_matching_twin_only():
    net, conn = _page("Network"), _page("Connected devices")
    t_net = _refined_twin(net)
    drv = FakeDriver()
    # observing Network reproduces t_net's refined id -> EXACT
    assert verify_match(net, NS, t_net, drv, FOCUS) == Tier.EXACT
    # observing Connected does NOT match t_net's id; same skeleton -> STRUCTURE (not EXACT)
    assert verify_match(conn, NS, t_net, drv, FOCUS) == Tier.STRUCTURE


def test_actual_node_resolves_the_right_twin_by_chrome():
    net, conn = _page("Network"), _page("Connected devices")
    t_net, t_conn = _refined_twin(net), _refined_twin(conn)
    g = Graph()
    g.upsert_screen(t_net)
    g.upsert_screen(t_conn)
    nav = Navigator(g, FakeDriver())
    G = g.routable_subgraph()
    assert nav._actual_node(net, NS, FOCUS, G) == t_net.id   # chrome picks Network's twin
    assert nav._actual_node(conn, NS, FOCUS, G) == t_conn.id  # ...and Connected's


def test_fingerprint_ambiguous_false_for_a_refined_twin():
    net, conn = _page("Network"), _page("Connected devices")
    t_net, t_conn = _refined_twin(net), _refined_twin(conn)
    g = Graph()
    g.upsert_screen(t_net)
    g.upsert_screen(t_conn)
    nav = Navigator(g, FakeDriver())
    # the two twins share a structure_id, but a refined id is value-bearing -> NOT ambiguous
    assert nav._fingerprint_ambiguous(t_net, g.routable_subgraph()) is False


def _nav_to_twin(target_xml, observe_xml, n_rows=1, list_page=False):
    """Anchor --click--> the refined twin (built from target_xml); the device shows observe_xml."""
    target = _refined_twin(target_xml)
    g = Graph()
    anc_xml = ('<hierarchy><node class="android.widget.LinearLayout" package="com.app" '
               'resource-id="com.app:id/anchor" clickable="false" content-desc="" text="" '
               'bounds="[0,0][1080,200]"/></hierarchy>')
    anc = Screen(id=fingerprint("com.app/.Home", anc_xml, None, FOCUS), namespace="com.app/.Home",
                 structure_id=structure_id("com.app/.Home", anc_xml, FOCUS), package="com.app",
                 activity=".Home", profile_name="view",
                 force_action=ForceAction("am_start", "com.app/.Home", verified_fp="anc"))
    g.upsert_screen(anc)
    g.upsert_screen(target)
    g.add_transition(Transition(source=anc.id, target=target.id,
                                action=Action(selector=Selector("text", "go"), action_type="click")))
    t = [0.0]
    nav = Navigator(g, FakeDriver(present_selectors={("text", "go")}),
                    clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    nav._observe = lambda: (observe_xml, NS, FOCUS, True)
    return nav.navigate(anc.id, target.id), target


def test_navigate_confident_on_a_refined_twin_non_adapter_page():
    # a refined twin on a NON-adapter page is value-bearing -> confident EXACT on sight.
    net = _page("Network", n_rows=1)
    out, target = _nav_to_twin(net, net)
    assert out.status == "arrived" and out.tier == "EXACT"


def test_navigate_unverified_on_a_wrong_twin_observation():
    # the device shows a SIBLING (Connected) while we target Network's twin -> never confident.
    net, conn = _page("Network", 1), _page("Connected devices", 1)
    out, target = _nav_to_twin(net, conn)
    assert out.status != "arrived"  # honest: the chrome doesn't match the target twin


def test_adapter_dominant_refined_twin_is_not_confident_on_sight_HW2():
    # HW2: on an adapter-list-dominant page the digest reduces to the title, so a refined twin
    # must NOT be claimed on sight — it needs corroboration. With no walkable corroboration in
    # this fixture, the honest outcome is arrived_unverified, NEVER a confident 'arrived'.
    net_list = _list_page("Network", n_rows=12)
    out, target = _nav_to_twin(net_list, net_list, list_page=True)
    assert out.status == "arrived_unverified"  # value-on-sight withheld on an adapter-dominant page
    assert out.status != "arrived"


def test_hw2_uses_recorded_adapter_dominance_not_live_row_count():
    # CRITICAL adversarial finding: _value_bearing_on_sight keyed on the LIVE observed row count,
    # so a twin RECORDED adapter-dominant, OBSERVED with an empty/sparse list, was claimed
    # confident-EXACT on sight (a same-titled unrecorded sibling reproduces its id). HW2 must key
    # on an IDENTITY-CLASS property (recorded adapter_dominant), not the transient live dump.
    net_list = _list_page("Network", n_rows=12)
    target = _refined_twin(net_list)
    target.adapter_dominant = True  # recorded as adapter-dominant
    nav = Navigator(Graph(), FakeDriver())
    # observed with an EMPTY list (adapter_list_dominant(observed) is False) — must STILL withhold
    assert nav._value_bearing_on_sight(target, _list_page("Network", n_rows=0), FOCUS) is False
    # a NON-adapter twin (recorded) stays confident on sight
    rich = _refined_twin(_page("Network", n_rows=1))
    rich.adapter_dominant = False
    assert nav._value_bearing_on_sight(rich, _page("Network", n_rows=1), FOCUS) is True


def test_recorded_adapter_dominance_is_row_count_independent():
    # CRITICAL re-verification finding: adapter-dominance computed from the live row count made a
    # twin recorded SPARSE (1 row) get adapter_dominant=False, so T_old inherited a False flag and
    # was claimed confident-on-sight despite being a genuine list page. The recorded signal must be
    # ROW-COUNT-INDEPENDENT: a page that HAS a collapsing adapter list is flagged whether it shows
    # 0, 1, or 30 rows.
    from wendle.fingerprint.signature import has_collapsing_list  # the recorded signal
    def adom(xml):
        return has_collapsing_list(xml, focus_pkg=FOCUS)
    assert adom(_list_page("Network", n_rows=1)) is True    # 1 row -> still a list page
    assert adom(_list_page("Network", n_rows=30)) is True
    assert adom(_list_page("Network", n_rows=0)) is True     # 0 rows -> still a list page
    assert adom(_page("Network", 1)) is False                # a form, no list -> rich chrome
