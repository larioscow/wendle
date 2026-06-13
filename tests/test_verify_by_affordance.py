"""VERIFY-BY-AFFORDANCE — content-independent confirmation that tapping a global-nav affordance
A reached A's section, for content-DRIFTING sections (a live-clock tab) no fingerprint can match.
The 0/3 adversarial review found 5 confident-wrong paths in the first sketch; every one is a RED
test here asserting an HONEST verdict (never a confident 'arrived'). The helper is a PURE function
over (pre_tap_xml, post_tap_settled_xml, affordance_value, focus_pkg, target_pkg).

Verdict: 'arrived' (affordance-confirmed) | 'unverified' (in-app, present, no affirmative selected)
| 'no' (not on A's section). NEVER claims EXACT — the caller maps 'arrived' to a sub-EXACT tier.
"""
from wendle.navigate.affordance_verify import verify_by_affordance

PKG = "com.x"
NS = f"{PKG}/.Main"


def _bar(active, items=("Alarm", "World", "Timer"), pkg=PKG):
    return "".join(
        f'<node class="android.widget.LinearLayout" package="{pkg}" content-desc="{n}" '
        f'clickable="{"false" if i == active else "true"}" '
        f'selected="{"true" if i == active else "false"}" '
        f'bounds="[{300 + i*200},2800][{480 + i*200},2980]"/>'
        for i, n in enumerate(items))


def _screen(active, body, pkg=PKG, items=("Alarm", "World", "Timer")):
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" '
            f'bounds="[0,0][1440,3120]">'
            f'<node class="android.widget.TextView" package="{pkg}" text="{body}" '
            f'bounds="[40,200][1040,400]"/>'
            f'<node class="androidx.recyclerview.widget.RecyclerView" package="{pkg}" '
            f'resource-id="{pkg}:id/content" bounds="[0,420][1080,2700]"/>'
            f'<node class="com.google.android.material.tabs.TabLayout" package="{pkg}" '
            f'resource-id="{pkg}:id/tabs" bounds="[270,2800][1170,3000]">{_bar(active, items, pkg)}'
            f'</node></node></hierarchy>')


def test_true_positive_affordance_confirmed():
    # tapped World; post-tap World is selected, content changed, in-app -> arrived (sub-EXACT)
    pre = _screen(0, "Alarm content")
    post = _screen(1, "World content — live 12:34")
    assert verify_by_affordance(pre, post, "World", PKG, PKG) == "arrived"


def test_body_label_clickable_false_does_not_falsely_verify():
    # CONFIDENT-WRONG #1: tap goes to Alarm, whose BODY has a 'World' label (clickable=false,
    # NOT in the nav bar). The clickable=false leak must NOT fire — require affirmative selected
    # on a NAV-CONTAINER member.
    pre = _screen(0, "Alarm content")
    post = (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" '
            f'bounds="[0,0][1440,3120]">'
            f'<node class="android.widget.TextView" package="{PKG}" content-desc="World" '
            f'clickable="false" bounds="[40,500][1040,600]"/>'  # a body label, NOT a tab
            f'<node class="com.google.android.material.tabs.TabLayout" package="{PKG}" '
            f'resource-id="{PKG}:id/tabs" bounds="[270,2800][1170,3000]">{_bar(0)}</node>'
            f'</node></hierarchy>')  # the tab bar still shows ALARM selected
    assert verify_by_affordance(pre, post, "World", PKG, PKG) != "arrived"


def test_duplicate_desc_multi_match_refuses():
    # CONFIDENT-WRONG #2: post-tap has TWO 'World' nodes (a tab + a body element both selected)
    post = _screen(1, "World").replace(
        '</node></hierarchy>',
        f'<node class="android.view.View" package="{PKG}" content-desc="World" selected="true" '
        f'clickable="true" bounds="[40,700][200,800]"/></node></hierarchy>')
    # the body 'World' is NOT in the nav container, but assert the unique-in-container gate holds
    pre = _screen(0, "Alarm")
    # add a second World INSIDE the bar to force ambiguity within the container
    post2 = post.replace('content-desc="Timer"', 'content-desc="World"')
    assert verify_by_affordance(pre, post2, "World", PKG, PKG) != "arrived"


def test_open_drawer_selected_without_content_change_refuses():
    # CONFIDENT-WRONG #3/#4: the affordance is selected but the CONTENT pane is unchanged
    # (drawer still open / optimistic selection before load)
    pre = _screen(0, "Alarm content")
    post = _screen(1, "Alarm content")  # bar flipped to World-selected but body UNCHANGED
    assert verify_by_affordance(pre, post, "World", PKG, PKG) != "arrived"


def test_foreign_app_with_matching_desc_refuses():
    # CONFIDENT-WRONG #7: post-tap is a DIFFERENT package that happens to render 'World'
    pre = _screen(0, "Alarm content")
    post = _screen(1, "World", pkg="com.other")
    assert verify_by_affordance(pre, post, "World", PKG, PKG) != "arrived"  # focus != target pkg


def test_active_tab_only_clickable_false_no_selected_is_unverified_not_arrived():
    # the app marks the active tab clickable=false but NEVER sets selected=true -> no affirmative
    # signal -> honest 'unverified' (in-app, A present), NEVER a confident 'arrived'
    bar = "".join(
        f'<node class="android.widget.LinearLayout" package="{PKG}" content-desc="{n}" '
        f'clickable="{"false" if i==1 else "true"}" '  # World active (clickable=false), no selected
        f'bounds="[{300+i*200},2800][{480+i*200},2980]"/>' for i, n in enumerate(("Alarm","World","Timer")))
    pre = _screen(0, "Alarm content")
    post = (f'<hierarchy><node class="android.widget.FrameLayout" package="{PKG}" bounds="[0,0][1440,3120]">'
            f'<node class="android.widget.TextView" package="{PKG}" text="World content live" bounds="[40,200][1040,400]"/>'
            f'<node class="com.google.android.material.tabs.TabLayout" package="{PKG}" '
            f'resource-id="{PKG}:id/tabs" bounds="[270,2800][1170,3000]">{bar}</node></node></hierarchy>')
    v = verify_by_affordance(pre, post, "World", PKG, PKG)
    assert v in ("unverified", "no") and v != "arrived"
