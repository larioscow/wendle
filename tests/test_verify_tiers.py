import hashlib

from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.models import Action, Screen, Selector
from wendle.navigate.verify import Tier, verify_match

NS = "com.app/.AActivity"


def _xml(rid="ok", text="Go", extra=""):
    return (
        '<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
        'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="com.app" resource-id="com.app:id/{rid}" '
        f'clickable="true" content-desc="" text="{text}" bounds="[40,500][1040,620]"/>'
        f"{extra}</node></hierarchy>"
    )


_EXTRA = (
    '<node class="android.widget.TextView" package="com.app" resource-id="" '
    'clickable="false" content-desc="" text="" bounds="[0,700][100,760]"/>'
)


def _vid(ns):
    return "V" + hashlib.sha1(ns.encode()).hexdigest()[:15]


def _settled_screen(xml, ns=NS, actions=None):
    return Screen(
        id=fingerprint(ns, xml),
        namespace=ns,
        structure_id=structure_id(ns, xml),
        actions=actions or [],
    )


def _listxml(items):
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


def test_exact_tier():
    xml = _xml()
    assert verify_match(xml, NS, _settled_screen(xml), FakeDriver()) == Tier.EXACT


def test_structure_tier_for_volatile_target():
    # volatile target (id="V…") can never match EXACT, but a stable structure_id does
    xml = _xml()
    s = Screen(id=_vid(NS), namespace=NS, structure_id=structure_id(NS, xml), profile_name="volatile")
    assert verify_match(xml, NS, s, FakeDriver()) == Tier.STRUCTURE


def test_unverifiable_when_adapter_list_dominant():
    recorded, observed = _listxml(3), _listxml(7)
    s = Screen(id=_vid(NS), namespace=NS, structure_id=structure_id(NS, recorded), profile_name="volatile")
    # structure collapses to the same id, but a list-dominant screen could be any
    # sibling -> must NOT claim confident arrival
    assert verify_match(observed, NS, s, FakeDriver()) == Tier.UNVERIFIABLE


def test_weak_tier_namespace_plus_resolving_probe():
    s = _settled_screen(
        _xml(),
        actions=[Action(selector=Selector("resource_id", "com.app:id/ok"), action_type="click")],
    )
    drv = FakeDriver(present_selectors={("resource_id", "com.app:id/ok")})
    # EXACT + STRUCTURE fail (extra node), ns matches, resource_id probe resolves
    assert verify_match(_xml(extra=_EXTRA), NS, s, drv) == Tier.WEAK


def test_unverifiable_when_no_resource_id_probe_regression_verify_py_56():
    # ns matches, structure differs, target has only a text selector (no rid probe)
    s = _settled_screen(
        _xml(), actions=[Action(selector=Selector("text", "Go"), action_type="click")]
    )
    assert verify_match(_xml(extra=_EXTRA), NS, s, FakeDriver()) == Tier.UNVERIFIABLE


def test_mismatch_wrong_namespace():
    xml = _xml()
    assert verify_match(xml, "com.other/.X", _settled_screen(xml), FakeDriver()) == Tier.MISMATCH


def test_mismatch_when_probe_absent():
    s = _settled_screen(
        _xml(),
        actions=[Action(selector=Selector("resource_id", "com.app:id/ok"), action_type="click")],
    )
    drv = FakeDriver(present_selectors=set())  # probe NOT on screen
    assert verify_match(_xml(extra=_EXTRA), NS, s, drv) == Tier.MISMATCH


def test_reveal_intent_selector_is_not_used_as_probe():
    # a scroll (reveal) action's resource_id must NOT back a presence probe
    s = _settled_screen(
        _xml(),
        actions=[
            Action(selector=Selector("resource_id", "com.app:id/scroller"),
                   action_type="swipe", intent="reveal")
        ],
    )
    drv = FakeDriver(present_selectors={("resource_id", "com.app:id/scroller")})
    # no navigate-intent probe -> UNVERIFIABLE, not WEAK
    assert verify_match(_xml(extra=_EXTRA), NS, s, drv) == Tier.UNVERIFIABLE


def test_tier_is_ordered():
    assert Tier.EXACT > Tier.STRUCTURE > Tier.WEAK > Tier.UNVERIFIABLE > Tier.MISMATCH
