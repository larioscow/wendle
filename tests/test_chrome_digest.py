"""chrome_digest (task #17b): the collision-refinement signal — short text/content-desc values
OUTSIDE adapter lists, no depth bound, empty -> None, overlay/denylist/SystemUI stripped. Plus
refined_id, the coarse_fp + digest composition that distinguishes structure twins.

Grounded on the real Settings-twin corpus: outside-adapter-list + no-depth-bound is what
generalizes (the depth<6 spec was a Pixel-Settings-only constant — adversarial finding F2);
empty -> None is the cardinal-sin guard (an empty set must never be a value-bearing id — F1).
"""
from wendle.fingerprint.compose import VIEW_PROFILE
from wendle.fingerprint.signature import chrome_digest, refined_id


def _h(*children):
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            + "".join(children) + "</node></hierarchy>")


def _toolbar(title):
    # a desc-only collapsing-toolbar title nested deep (depth ~5+), like real Settings/WhatsApp.
    # Real dumps XML-escape the desc (& -> &amp;); mirror that so the fixture is valid XML.
    title = title.replace("&", "&amp;")
    return ('<node class="android.widget.LinearLayout" resource-id="com.app:id/app_bar" clickable="false" '
            'content-desc="" text="" bounds="[0,0][1080,400]">'
            '<node class="android.widget.LinearLayout" resource-id="com.app:id/collapsing" clickable="false" '
            'content-desc="" text="" bounds="[0,0][1080,400]">'
            '<node class="android.widget.LinearLayout" resource-id="com.app:id/bar" clickable="false" '
            'content-desc="" text="" bounds="[0,0][1080,200]">'
            f'<node class="android.widget.TextView" resource-id="com.app:id/title" clickable="false" '
            f'content-desc="{title}" text="" bounds="[40,40][800,160]"/>'
            "</node></node></node>")


def _list(*rows):
    body = "".join(
        f'<node class="android.widget.TextView" resource-id="com.app:id/row" clickable="true" '
        f'content-desc="" text="{r}" bounds="[0,{400+i*100}][1080,{500+i*100}]"/>'
        for i, r in enumerate(rows))
    return ('<node class="androidx.recyclerview.widget.RecyclerView" resource-id="com.app:id/list" '
            f'clickable="false" content-desc="" text="" bounds="[0,400][1080,2200]">{body}</node>')


def test_digest_is_deterministic_and_distinguishes_titles():
    a = chrome_digest(_h(_toolbar("Network & internet"), _list("Wi-Fi", "SIM")), focus_pkg="com.app")
    b = chrome_digest(_h(_toolbar("Connected devices"), _list("Bluetooth")), focus_pkg="com.app")
    assert a is not None and b is not None and a != b      # different titles -> different digests
    assert a == chrome_digest(_h(_toolbar("Network & internet"), _list("x")), focus_pkg="com.app")


def test_adapter_list_rows_contribute_nothing():
    # two same-title screens whose LIST bodies differ entirely must share a digest (the body is
    # adapter content, not chrome) — the whole reason structure twins exist.
    one = chrome_digest(_h(_toolbar("Storage"), _list("Photos", "Videos", "Apps")), focus_pkg="com.app")
    two = chrome_digest(_h(_toolbar("Storage"), _list("Music")), focus_pkg="com.app")
    assert one == two and one is not None


def test_no_depth_bound_captures_a_deep_title():
    # the F2 fix: the title sits well below depth 6 (real apps: depth 11-22); it must still be
    # captured. A depth<6 bound would miss it and digest empty.
    assert chrome_digest(_h(_toolbar("Deep Title")), focus_pkg="com.app") is not None


def test_empty_chrome_is_none_never_a_value_bearing_digest():
    # the F1 cardinal-sin guard: a screen whose only content is inside the list (a full-bleed
    # search/feed) has NO chrome -> None, NOT sha1("") -> never a confident refined id.
    assert chrome_digest(_h(_list("result a", "result b")), focus_pkg="com.app") is None
    assert chrome_digest(_h(), focus_pkg="com.app") is None


def test_long_values_are_dropped():
    # DroidBot's label-vs-content rule: a >50-char value is content, not a label.
    long = "x" * 60
    assert chrome_digest(_h(_toolbar(long)), focus_pkg="com.app") is None


def test_set_semantics_collapse_duplicate_values():
    # duplicate-count jitter (the launcher evidence) must not change the digest — values, not counts.
    one = chrome_digest(_h(_toolbar("Tabs"),
                           '<node class="android.widget.TextView" resource-id="com.app:id/a" '
                           'clickable="false" content-desc="New" text="" bounds="[0,0][50,50]"/>'),
                        focus_pkg="com.app")
    two = chrome_digest(_h(_toolbar("Tabs"),
                           '<node class="android.widget.TextView" resource-id="com.app:id/a" '
                           'clickable="false" content-desc="New" text="" bounds="[0,0][50,50]"/>'
                           '<node class="android.widget.TextView" resource-id="com.app:id/b" '
                           'clickable="false" content-desc="New" text="" bounds="[60,0][110,50]"/>'),
                        focus_pkg="com.app")
    assert one == two and one is not None  # the duplicate "New" collapsed


def test_systemui_status_chrome_is_stripped_with_focus():
    # the F2/F6 load-bearing requirement: with focus_pkg set, the SystemUI status bar
    # (battery/signal) is stripped, so it can't pollute or destabilize the digest.
    su = ('<node class="android.widget.FrameLayout" package="com.android.systemui" resource-id="" '
          'clickable="false" content-desc="Battery 100 percent." text="" bounds="[900,0][1080,60]"/>')
    with_su = chrome_digest(_h(_toolbar("Battery"), su, _list("x")), focus_pkg="com.app")
    without = chrome_digest(_h(_toolbar("Battery"), _list("x")), focus_pkg="com.app")
    assert with_su == without  # the SystemUI desc did not enter the digest


def test_malformed_xml_is_none():
    assert chrome_digest("", focus_pkg="com.app") is None
    assert chrome_digest("<not closed", focus_pkg="com.app") is None


def test_refined_id_composes_coarse_and_digest():
    # refined_id distinguishes twins by digest while sharing the coarse fp; deterministic and
    # recomputable from (coarse_fp, digest) alone — so a live dump and a stored Screen converge.
    F = "abc123def4567890"
    d1, d2 = "11112222", "33334444"
    t1, t2 = refined_id(F, d1), refined_id(F, d2)
    assert t1 != t2                         # same coarse, different chrome -> different refined id
    assert t1.startswith("T")               # the id-marker convention (V/S/L/T)
    assert refined_id(F, d1) == t1          # deterministic
    assert refined_id("other", d1) != t1    # coarse fp participates


def test_volatile_widget_chrome_is_excluded():
    # adversarial finding (media apps): a SeekBar/ProgressBar/Chronometer carries a churning
    # content-desc (a playback timestamp) that advances every second on a SETTLED screen. It must
    # NOT enter the digest, else two visits to the SAME screen falsely collide and split (ghost
    # twins). Grounded in the Android volatile-widget taxonomy, like the adapter-list exclusion.
    def player(ts):
        return _h(_toolbar("Now Playing"),
                  f'<node class="android.widget.SeekBar" resource-id="com.app:id/seek" '
                  f'clickable="false" content-desc="{ts}" text="" bounds="[0,2000][1080,2080]"/>')
    a = chrome_digest(player("0:01 of 0:40"), focus_pkg="com.app")
    b = chrome_digest(player("0:23 of 0:40"), focus_pkg="com.app")
    assert a is not None and a == b  # the churning seekbar desc was excluded; only the title remains
    # a Chronometer (running clock) is excluded too
    chrono = _h(_toolbar("Timer"),
                '<node class="android.widget.Chronometer" resource-id="com.app:id/c" '
                'clickable="false" content-desc="00:42" text="" bounds="[0,200][200,260]"/>')
    chrono2 = chrono.replace("00:42", "01:13")
    assert chrome_digest(chrono, focus_pkg="com.app") == chrome_digest(chrono2, focus_pkg="com.app")
