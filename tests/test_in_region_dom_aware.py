"""in_region means DOM-DESCENDANT of an adapter region, not merely geometrically inside.

S23 Clock finding: a tab button (global-nav chrome) sits in a floating bottom bar whose pixels
fall INSIDE the content list's region bounds, but it lives in a SEPARATE DOM branch. The old
geometric `_point_in_region` stamped the tab edge in_region=True, so replay's in-region
pre-route reveal-SCROLLED for it (and the DOM-subtree matcher correctly never finds it there)
-> reveal_no_movement / content_drift instead of a direct tab tap. The stamp must be DOM-aware.
"""
from wendle.reveal import node_in_region_subtree

# a list region [0,300]..[1080,2100], a real ROW inside it, and a FLOATING tab bar whose pixels
# overlap the region bottom but which is a SIBLING DOM branch (drawn after the list)
XML = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1080,2340]">'
       '<node class="androidx.recyclerview.widget.RecyclerView" resource-id="app:id/list" '
       'bounds="[0,300][1080,2100]">'
       '<node class="android.view.View" resource-id="app:id/row" clickable="true" '
       'bounds="[0,500][1080,700]"/></node>'
       '<node class="android.widget.LinearLayout" resource-id="app:id/tabbar" '
       'bounds="[0,1900][1080,2100]">'
       '<node class="android.widget.LinearLayout" content-desc="Timer" clickable="true" '
       'bounds="[540,1920][720,2080]"/></node>'
       '</node></hierarchy>')


def test_list_row_inside_the_region_subtree_is_in_region():
    # the row is a DOM descendant of the recycler region -> in_region
    assert node_in_region_subtree(XML, (0, 500, 1080, 700)) is True


def test_floating_tab_over_the_region_is_not_in_region():
    # the tab button's pixels overlap the region bounds, but it is a SIBLING DOM branch
    # (the floating bar) -> NOT in_region (a global-nav tap, not a list item)
    assert node_in_region_subtree(XML, (540, 1920, 720, 2080)) is False


def test_node_outside_all_regions_is_not_in_region():
    assert node_in_region_subtree(XML, (0, 100, 1080, 200)) is False


def test_no_regions_is_not_in_region():
    plain = '<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][1080,2340]"/></hierarchy>'
    assert node_in_region_subtree(plain, (0, 100, 100, 200)) is False
