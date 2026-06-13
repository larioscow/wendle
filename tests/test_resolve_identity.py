"""Task #17b-3: the resolve_identity gate — the ONE ordered ladder that mints screen identity,
refining structure twins apart ONLY after observing a collision (APE's CEGAR shape). Tests every
leaf plus the adversarial-review findings: empty digest never refines (F1), a large family is NOT
coarsened away by size alone (F3), and a split returns the rename so the caller can re-read a
stale source (lifecycle blocker)."""
from wendle.fingerprint.compose import VIEW_PROFILE
from wendle.fingerprint.signature import fingerprint, refined_id, structure_id
from wendle.graph import Graph
from wendle.models import Screen
from wendle.record.identity import FAMILY_MAX, resolve_identity

NS = "com.app/.SubSettings"
CFG = VIEW_PROFILE


def _h(title, *rows):
    title = title.replace("&", "&amp;")
    body = "".join(
        f'<node class="android.widget.TextView" resource-id="com.app:id/row" clickable="true" '
        f'content-desc="" text="{r}" bounds="[0,{400+i*100}][1080,{500+i*100}]"/>'
        for i, r in enumerate(rows))
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.TextView" resource-id="com.app:id/title" clickable="false" '
            f'content-desc="{title}" text="" bounds="[40,40][800,160]"/>'
            '<node class="androidx.recyclerview.widget.RecyclerView" resource-id="com.app:id/list" '
            f'clickable="false" content-desc="" text="" bounds="[0,400][1080,2200]">{body}</node>'
            "</node></hierarchy>")


def _resolve(g, xml, settled=True, focus="com.app"):
    # The gate decides; _enter is the SOLE minter. This helper simulates _enter: mint the
    # returned-id node (if absent) carrying the decision's chrome_digest/coarse_id, so that a
    # later visit's collision detection has node F (with its digest) to compare against.
    dec = resolve_identity(g, NS, xml, focus, settled, CFG)
    if g.screen(dec.id) is None:
        pkg, _, act = NS.partition("/")
        g.upsert_screen(Screen(
            id=dec.id, namespace=NS, structure_id=structure_id(NS, xml, focus),
            package=pkg or None, activity=act or None,
            chrome_digest=dec.chrome_digest, coarse_id=dec.coarse_id,
            volatile=not settled, profile_name="view" if settled else "volatile",
            fingerprint_confidence="high" if settled else "low"))
    return dec


def test_unsettled_is_volatile_never_refined():
    g = Graph()
    dec = _resolve(g, _h("Network", "Wi-Fi"), settled=False)
    assert dec.id.startswith("V") and dec.node_remap is None


def test_new_screen_stores_its_digest_under_the_coarse_fingerprint():
    g = Graph()
    xml = _h("Network", "Wi-Fi")
    dec = _resolve(g, xml)
    F = fingerprint(NS, xml, CFG, "com.app")
    assert dec.id == F  # first sighting -> coarse id, no refinement yet
    assert g.screen(F).chrome_digest is not None and g.screen(F).coarse_id is None


def test_revisit_same_chrome_is_the_same_node():
    g = Graph()
    xml = _h("Network", "Wi-Fi")
    first = _resolve(g, xml).id
    again = _resolve(g, _h("Network", "Other rows")).id  # same title, different list body
    assert again == first  # adapter content differs -> still the same screen


def test_observed_collision_splits_and_returns_the_rename():
    # THE task: visit Network (coarse F), then visit Connected-devices (same skeleton, different
    # title) -> a collision is OBSERVED -> split. The existing node F is rekeyed to its refined id
    # and the new twin is minted; the rename is returned so a caller holding `source == F` can fix it.
    g = Graph()
    xa, xb = _h("Network", "Wi-Fi"), _h("Connected devices", "Bluetooth")
    F = fingerprint(NS, xa, CFG, "com.app")
    da = g.screen(F) if False else None
    first = _resolve(g, xa).id
    assert first == F
    digest_a = g.screen(F).chrome_digest
    dec = _resolve(g, xb)
    T_old = refined_id(F, digest_a)        # Network's refined id
    T_new = refined_id(F, g.screen(dec.id).chrome_digest)
    assert dec.id == T_new and dec.id != T_old
    assert F not in g.g.nodes               # coarse node was rekeyed away
    assert T_old in g.g.nodes and g.screen(T_old).coarse_id == F  # Network became refined
    assert g.screen(T_new).coarse_id == F
    assert dec.node_remap == {F: T_old}      # the rename for the caller (split-while-source)


def test_revisit_a_refined_twin_by_its_chrome():
    g = Graph()
    xa, xb = _h("Network", "Wi-Fi"), _h("Connected devices", "Bluetooth")
    F = fingerprint(NS, xa, CFG, "com.app")
    _resolve(g, xa)
    digest_a = g.screen(F).chrome_digest    # capture before the split rekeys F away
    _resolve(g, xb)                          # split happened
    T_network = refined_id(F, digest_a)
    again = _resolve(g, _h("Network", "fresh rows")).id  # back to Network, new list body
    assert again == T_network                # re-found the same refined twin by its chrome
    assert g.screen(again).coarse_id == F    # and it is a refined member of the family


def _ht(title):
    # chrome in TEXT (which structure_id ignores via include_text=False), so an empty title
    # keeps the SAME coarse fingerprint while emptying the digest — the realistic F1 shape
    # (a text toolbar that renders late / blanks between states).
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.TextView" resource-id="com.app:id/title" clickable="false" '
            f'content-desc="" text="{title}" bounds="[40,40][800,160]"/>'
            '<node class="androidx.recyclerview.widget.RecyclerView" resource-id="com.app:id/list" '
            'clickable="false" content-desc="" text="" bounds="[0,400][1080,2200]"/>'
            "</node></hierarchy>")


def test_empty_digest_never_splits_a_coarse_node_F1():
    # F1 cardinal-sin guard: a coarse node with a stored digest, then a chrome-LESS visit at the
    # SAME coarse fingerprint (text toolbar blanked). An empty digest is NOT value-bearing, so it
    # must NOT split (else any other chrome-less same-skeleton screen reproduces a confident id).
    g = Graph()
    titled, blank = _ht("Network"), _ht("")
    assert structure_id(NS, titled, "com.app") == structure_id(NS, blank, "com.app")  # same skeleton
    F = _resolve(g, titled).id
    assert g.screen(F).chrome_digest is not None
    dec = _resolve(g, blank)
    assert dec.id == F and dec.node_remap is None          # empty -> stays coarse, no refinement
    assert g.screen(F).coarse_id is None                # F was NOT turned into a refined twin


def test_large_family_is_not_coarsened_away_by_size_F3():
    # F3: Settings has ~15-20 sub-pages sharing one skeleton. Refining 10 distinct twins must NOT
    # trip a coarsen-and-blacklist (a family-SIZE cap would un-fix the motivating app). FAMILY_MAX
    # is an order of magnitude above plausible real families.
    assert FAMILY_MAX >= 32
    g = Graph()
    ids = set()
    for i in range(10):
        ids.add(_resolve(g, _h(f"Page {i}", f"row {i}")).id)
    F = fingerprint(NS, _h("Page 0", "row 0"), CFG, "com.app")
    assert len(ids) == 10                    # ten distinct refined twins, all kept
    assert not g.is_twin_exhausted(F)        # family NOT blacklisted


def test_runaway_churn_coarsens_back_and_blacklists():
    # the bound: a screen whose chrome churns every visit mints ghost twins; past FAMILY_MAX the
    # family is coarsened back to one node and blacklisted -> converges to today's behavior.
    g = Graph()
    last = None
    for i in range(FAMILY_MAX + 2):
        last = _resolve(g, _h(f"Churn {i}", "row")).id
    F = fingerprint(NS, _h("Churn 0", "row"), CFG, "com.app")
    assert g.is_twin_exhausted(F)            # blacklisted after the runaway
    assert g.screen(last) is not None and g.screen(last).coarse_id is None  # coarse again


def test_exhausted_family_stays_coarse():
    g = Graph()
    xa = _h("Network", "Wi-Fi")
    F = fingerprint(NS, xa, CFG, "com.app")
    g.mark_twin_exhausted(F)
    dec = _resolve(g, xa)
    assert dec.id == F and dec.node_remap is None  # blacklist honored: coarse, never refined
    assert g.screen(F).coarse_id is None


def test_launcher_namespace_skips_refinement():
    g = Graph()
    L = "com.sec.android.app.launcher/.Home"
    xml = '<hierarchy><node class="android.widget.FrameLayout" content-desc="" text="" clickable="false"/></hierarchy>'
    dec = resolve_identity(g, L, xml, "com.sec.android.app.launcher", True, CFG)
    assert dec.id == fingerprint(L, xml) and dec.node_remap is None  # the L id, no digest path
