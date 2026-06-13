"""Multi-app routing (the last honest cap): a RECORDED cross-package edge is walkable.

routable_subgraph always KEPT cross-package edges ("dropping them would erase the only
recorded path into a share/OAuth-only target") — but the navigator's wrong-app gate fired
BEFORE pathfinding, so they were never planned: standing on a recognized node in app A with
a recorded route to the app-B target refused cross_app_boundary (the S23 Settings→Battery
case, package com.samsung.android.lool, no own anchor). The rule: a foreign package is only
"lost" when there is NO recorded route from the recognized current node; the re-anchor /
typed refusal remains the fallback for unrecognized or route-less states.
"""
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, structure_id
from wendle.graph import Graph
from wendle.models import Action, ForceAction, Screen, Selector, Transition
from wendle.navigate.navigator import Navigator

PKG_A, PKG_B = "com.app.settings", "com.oem.battery"
NS_A1, NS_A2 = f"{PKG_A}/.Top", f"{PKG_A}/.Sub"
NS_B = f"{PKG_B}/.BatteryActivity"

X_A1 = (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG_A}" resource-id="" '
        f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
        f'<node class="android.widget.Button" package="{PKG_A}" resource-id="{PKG_A}:id/go" '
        f'clickable="true" content-desc="" text="Connections" bounds="[40,500][1040,620]"/>'
        f'</node></hierarchy>')
X_A2 = X_A1.replace('text="Connections"', 'text="Battery"').replace(":id/go", ":id/bat")
X_B = (f'<hierarchy><node class="android.widget.LinearLayout" package="{PKG_B}" '
       f'resource-id="{PKG_B}:id/root" clickable="false" content-desc="" text="" '
       f'bounds="[0,0][1080,2340]"><node class="android.widget.TextView" package="{PKG_B}" '
       f'resource-id="" clickable="false" content-desc="" text="Battery usage" '
       f'bounds="[40,200][1040,300]"/></node></hierarchy>')

A1, A2, B = fingerprint(NS_A1, X_A1), fingerprint(NS_A2, X_A2), fingerprint(NS_B, X_B)


def _graph(with_b_anchor=False):
    g = Graph()
    g.upsert_screen(Screen(id=A1, namespace=NS_A1, package=PKG_A, activity=".Top",
                           structure_id=structure_id(NS_A1, X_A1),
                           force_action=ForceAction("am_start", NS_A1, verified_fp=A1)))
    g.upsert_screen(Screen(id=A2, namespace=NS_A2, package=PKG_A, activity=".Sub",
                           structure_id=structure_id(NS_A2, X_A2)))
    g.upsert_screen(Screen(id=B, namespace=NS_B, package=PKG_B, activity=".BatteryActivity",
                           structure_id=structure_id(NS_B, X_B),
                           force_action=(ForceAction("am_start", NS_B, verified_fp=B)
                                         if with_b_anchor else None)))
    g.add_transition(Transition(source=A1, target=A2,
                                action=Action(selector=Selector("label", "Connections"),
                                              action_type="click")))
    g.add_transition(Transition(source=A2, target=B,  # the recorded CROSS-PACKAGE edge
                                action=Action(selector=Selector("label", "Battery"),
                                              action_type="click")))
    return g


def _nav(g, frames_fn, present):
    drv = FakeDriver(present_selectors=set(present))
    t = [0.0]
    nav = Navigator(g, drv, clock=lambda: t[0],
                    sleep=lambda dt: t.__setitem__(0, t[0] + dt))
    nav._observe = lambda: frames_fn(drv)
    return nav, drv


def _obs(drv):
    taps = [v for (_k, v, _a) in drv.taps]
    if "Battery" in taps:
        return (X_B, NS_B, PKG_B, True)
    if "Connections" in taps:
        return (X_A2, NS_A2, PKG_A, True)
    return (X_A1, NS_A1, PKG_A, True)


def test_recorded_cross_package_edge_is_walked_to_an_anchorless_target():
    # the S23 Settings->Battery shape: target app has NO anchor, but a recorded route exists
    # from the recognized current node — walk it (two selector taps), arrive verified.
    nav, drv = _nav(_graph(), _obs, {("text", "Connections"), ("text", "Battery")})
    out = nav.navigate(A1, B)
    assert out.status == "arrived", f"got {out.status}: {out.detail}"
    assert [v for (_k, v, _a) in drv.taps] == ["Connections", "Battery"]


def test_routeless_anchorless_foreign_target_still_refuses_typed():
    # no recorded path to B and B has no anchor -> the honest cross_app_boundary refusal stays
    g = _graph()
    g.g.remove_edge(A2, B)
    nav, drv = _nav(g, _obs, {("text", "Connections"), ("text", "Battery")})
    out = nav.navigate(A1, B)
    assert out.status in ("cross_app_boundary", "no_route", "off_graph")
    assert "Battery" not in [v for (_k, v, _a) in drv.taps]  # never guessed a tap


def test_unrecognized_foreign_screen_still_reanchors_via_target_anchor():
    # standing on an UNRECOGNIZED screen in the wrong app: the recorded-route arm does not
    # apply (no recognized node) — the target-app re-anchor recovery is preserved
    g = _graph(with_b_anchor=True)
    unknown = X_A1.replace('text="Connections"', 'text="Else"').replace(":id/go", ":id/x")

    def obs(drv):
        if drv.app_starts and drv.app_starts[-1][0] == PKG_B:
            return (X_B, NS_B, PKG_B, True)
        return (unknown, f"{PKG_A}/.Other", PKG_A, True)

    nav, drv = _nav(g, obs, {("text", "Battery")})
    out = nav.navigate(A1, B)
    assert out.status in ("arrived", "arrived_unverified")
    assert any(s[0] == PKG_B for s in drv.app_starts)  # recovered via B's anchor
