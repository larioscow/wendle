"""The shared ActionExecutor: one handler per action type, caller policy as context flags, typed
results. Covers the honesty bug it fixes — a navigator-style context (which used to lack swipe and
keyevent branches) now swipes and keyevents instead of tapping an element center / crashing.
"""
from wendle import actions
from wendle.actions import ActionContext, execute
from wendle.driver.fake import FakeDriver
from wendle.models import Action, Selector

USER = Selector("resource_id", "app:id/user")


def _replay_ctx(drv, **kw):
    t = [0.0]
    return ActionContext(drv, clock=lambda: t[0], sleep=lambda dt: t.__setitem__(0, t[0] + dt),
                         reproduce_coords=True, faithful_text=True, verify_text=True, **kw)


def _navigate_ctx(drv, **kw):
    # the navigator's policy: refuse coordinate-only taps, atomic text, no verify
    return ActionContext(drv, reproduce_coords=False, faithful_text=False, verify_text=False, **kw)


# ---- the honesty bug fix: a navigator-style context now handles swipe + keyevent ----

def test_navigate_context_swipes_a_coords_swipe_not_refuses():
    swipe = Action(selector=Selector("coords", (500, 1500)), action_type="swipe", end=(500, 400))
    drv = FakeDriver()
    res = execute(swipe, _navigate_ctx(drv))
    assert res.ok and res.reason == actions.OK
    assert drv.swipes == [((500, 1500), (500, 400))]  # SWIPED, not refused / tapped-center


def test_navigate_context_reconstructs_semantic_swipe_start():
    swipe = Action(selector=Selector("content_desc", "List"), action_type="swipe", end=(540, 300))
    drv = FakeDriver(present_selectors={("content_desc", "List")})
    drv.element_centers[("content_desc", "List")] = (540, 1500)
    res = execute(swipe, _navigate_ctx(drv))
    assert res.ok and drv.swipes == [((540, 1500), (540, 300))]


def test_navigate_context_issues_keyevent():
    key = Action(selector=Selector("keyevent", 4), action_type="keyevent")  # BACK
    drv = FakeDriver()
    res = execute(key, _navigate_ctx(drv))
    assert res.ok and drv.keyevents == [4]


def test_set_checked_vanished_is_typed_honest_not_drift():
    # the widget disappeared AFTER the tap (self-dismissing gate): the flip is UNVERIFIABLE, so
    # the executor must surface the typed vanish — never ok=True (unverified claim) and never
    # plain drift ("did not flip"), which is a confident-wrong verdict for a tap that may have
    # worked (review finding 15).
    chk = Action(selector=Selector("resource_id", "app:id/terms"),
                 action_type="set_checked", value={"checked": True})
    drv = FakeDriver()
    drv.checked_vanishes.add("app:id/terms")
    res = execute(chk, _replay_ctx(drv))
    assert not res.ok and res.reason == actions.CHECKBOX_VANISHED


def test_keyevent_without_a_code_is_typed_honest_not_a_crash():
    # a malformed/decayed keyevent edge (no keyevent selector, no value code) must surface a
    # typed honest result like every other handler — int(None) used to crash the replay loop.
    key = Action(selector=Selector("text", "Back"), action_type="keyevent")
    drv = FakeDriver()
    res = execute(key, _navigate_ctx(drv))
    assert not res.ok and res.reason == actions.UNSUPPORTED
    assert drv.keyevents == []


# ---- coords-tap policy differs by context ----

def test_coords_tap_reproduced_in_replay_refused_in_navigate():
    tap = Action(selector=Selector("coords", (500, 800)), action_type="click")
    rdrv = FakeDriver()
    r = execute(tap, _replay_ctx(rdrv))
    assert r.ok and r.low_confidence and ("coords", (500, 800), "click") in rdrv.taps

    ndrv = FakeDriver()
    n = execute(tap, _navigate_ctx(ndrv))
    assert not n.ok and n.reason == actions.COORDINATE_REFUSED and ndrv.taps == []


# ---- tap + decay fallback ----

def test_tap_resolves_then_decay_fallback():
    drv = FakeDriver(present_selectors={("text", "Alice")})
    ok = Action(selector=Selector("text", "Alice"), action_type="click")
    assert execute(ok, _replay_ctx(drv)).ok

    # recorded label decayed; stable leading segment still matches via tap_contains
    decayed = Action(selector=Selector("text", "Alice, sent 5m ago"), action_type="click")
    res = execute(decayed, _replay_ctx(drv))
    assert res.ok  # tap_contains('Alice') hit
    assert ("text", "contains:Alice", "click") in drv.taps


def test_tap_unresolved_is_not_resolved():
    miss = Action(selector=Selector("text", "Ghost"), action_type="click")
    res = execute(miss, _replay_ctx(FakeDriver()))
    assert not res.ok and res.reason == actions.NOT_RESOLVED


# ---- set_text: faithful (replay) vs atomic (navigate); credential; verify ----

def test_set_text_faithful_focus_types_and_verifies():
    drv = FakeDriver(present_selectors={("resource_id", "app:id/user")})
    a = Action(selector=USER, action_type="set_text", value={"text": "alice"})
    res = execute(a, _replay_ctx(drv))
    assert res.ok
    assert drv.text_sets[-1] == ("resource_id", "app:id/user", "alice", "focus_and_type")


def test_set_text_atomic_in_navigate_context():
    drv = FakeDriver()
    a = Action(selector=USER, action_type="set_text", value={"text": "alice"})
    res = execute(a, _navigate_ctx(drv))
    assert res.ok
    assert drv.text_sets[-1] == ("resource_id", "app:id/user", "alice")  # atomic set_text, no verify


def test_set_text_verify_mismatch_is_text_not_landed():
    drv = FakeDriver(present_selectors={("resource_id", "app:id/user")})
    drv.verify_fail.add(("resource_id", "app:id/user"))
    a = Action(selector=USER, action_type="set_text", value={"text": "alice"})
    res = execute(a, _replay_ctx(drv))
    assert not res.ok and res.reason == actions.TEXT_NOT_LANDED


def test_set_text_credential_required_names_param_not_secret():
    pw = Action(selector=Selector("resource_id", "app:id/pw"), action_type="set_text",
                value={"param": "pw"}, sensitive=True)
    res = execute(pw, _replay_ctx(FakeDriver(), params={}))
    assert not res.ok and res.reason == actions.CREDENTIAL_REQUIRED
    assert res.error == "credential required: pw" and "secret" not in (res.error or "")


def test_set_text_on_coords_is_unsupported():
    a = Action(selector=Selector("coords", (10, 10)), action_type="set_text", value={"text": "x"})
    res = execute(a, _replay_ctx(FakeDriver()))
    assert not res.ok and res.reason == actions.UNSUPPORTED


# ---- set_checked ----

def test_set_checked_flip_and_drift():
    drv = FakeDriver()
    a = Action(selector=Selector("resource_id", "app:id/terms"), action_type="set_checked",
               value={"checked": True})
    assert execute(a, _replay_ctx(drv)).ok and drv.checked_sets

    coords = Action(selector=Selector("coords", (1, 1)), action_type="set_checked", value={"checked": True})
    res = execute(coords, _replay_ctx(FakeDriver()))
    assert not res.ok and res.reason == actions.UNSUPPORTED
