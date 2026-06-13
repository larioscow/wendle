from pathlib import Path

from wendle.capture.hierarchy import node_at, parse_hierarchy

FIX = Path(__file__).parent / "fixtures"


def _nodes():
    return parse_hierarchy((FIX / "hierarchy_login.xml").read_text())


def test_parses_all_nodes_with_attrs():
    nodes = _nodes()
    assert len(nodes) == 5  # root + 4 children
    pw = next(n for n in nodes if n.resource_id == "com.app:id/password")
    assert pw.password is True
    assert pw.bounds == (40, 700, 1040, 820)


def test_node_at_returns_smallest_clickable_containing():
    nodes = _nodes()
    # centre of the login button
    hit = node_at(nodes, 540, 960)
    assert hit.resource_id == "com.app:id/login"
    # centre of the username field
    hit = node_at(nodes, 540, 560)
    assert hit.content_desc == "Username"


def test_node_at_returns_none_outside_any_node():
    nodes = _nodes()
    assert node_at(nodes, 5000, 5000) is None


def test_parse_hierarchy_skips_malformed_bounds():
    xml = (
        "<hierarchy><node class='A' bounds='garbage'/>"
        "<node class='B' bounds='[0,0][10,10]'/></hierarchy>"
    )
    nodes = parse_hierarchy(xml)
    assert [n.cls for n in nodes] == ["B"]


def test_parses_focused_attribute():
    xml = "<hierarchy><node class='android.widget.EditText' bounds='[0,0][10,10]' focused='true'/></hierarchy>"
    nodes = parse_hierarchy(xml)
    assert nodes[0].focused is True


def test_plausible_bind_target_accepts_clickable_and_labeled():
    from wendle.capture.hierarchy import parse_hierarchy, plausible_bind_target
    xml = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
           'content-desc="" text="" bounds="[0,0][1080,2340]">'
           '<node class="android.widget.TextView" resource-id="" clickable="false" '
           'content-desc="" text="Internet" bounds="[40,500][1040,620]"/>'
           '<node class="android.widget.Button" resource-id="app:id/b" clickable="true" '
           'content-desc="" text="" bounds="[40,700][1040,820]"/></node></hierarchy>')
    nodes = parse_hierarchy(xml)
    assert plausible_bind_target(nodes, 500, 560)   # labeled, Compose-style clickable=false
    assert plausible_bind_target(nodes, 500, 760)   # clickable
    assert not plausible_bind_target(nodes, 500, 2000)  # only the bare root contains it


def test_plausible_bind_target_rejects_loading_overlay():
    # THE task-#17a flake shape: a mid-load frame whose only nodes at the point are an
    # unlabeled non-clickable overlay container (rid=loading_container) and a spinner.
    from wendle.capture.hierarchy import parse_hierarchy, plausible_bind_target
    xml = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
           'content-desc="" text="" bounds="[0,0][1080,2340]">'
           '<node class="android.widget.FrameLayout" resource-id="app:id/loading_container" '
           'clickable="false" content-desc="" text="" bounds="[0,200][1080,2200]">'
           '<node class="android.widget.ProgressBar" resource-id="" clickable="false" '
           'content-desc="" text="" bounds="[490,1100][590,1200]"/></node></node></hierarchy>')
    assert not plausible_bind_target(parse_hierarchy(xml), 540, 1150)


def test_plausible_bind_target_follows_node_at_not_any_node():
    # adversarial MEDIUM (gate/binder split): a labeled card AND a smaller unlabeled shimmer
    # rect both cover the point; node_at binds the shimmer, so plausibility must judge THAT
    # node — not be satisfied by the card's label elsewhere in the subtree (else a confident
    # resource_id bind to a loading placeholder).
    from wendle.capture.hierarchy import node_at, parse_hierarchy, plausible_bind_target
    xml = ('<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
           'content-desc="" text="" bounds="[0,0][1080,2340]">'
           '<node class="android.widget.TextView" resource-id="app:id/title" clickable="false" '
           'content-desc="" text="Card Title" bounds="[40,500][1040,620]"/>'
           '<node class="android.view.View" resource-id="app:id/shimmer" clickable="false" '
           'content-desc="" text="" bounds="[40,540][200,600]"/></node></hierarchy>')
    nodes = parse_hierarchy(xml)
    assert node_at(nodes, 120, 560).resource_id == "app:id/shimmer"  # smaller node wins the bind
    assert not plausible_bind_target(nodes, 120, 560)  # ...and it is unlabeled -> implausible
    assert plausible_bind_target(nodes, 600, 560)      # over the title only -> plausible
