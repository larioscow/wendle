from wendle.fingerprint.compose import COMPOSE_PROFILE
from wendle.fingerprint.signature import fingerprint, structure_id

# Same layout, different text + clock value (volatile chrome).
SCREEN_A = (
    '<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
    'content-desc="" text="" bounds="[0,0][1080,2340]">'
    '<node class="android.widget.TextView" resource-id="com.android.systemui:id/clock" '
    'clickable="false" content-desc="" text="10:30" bounds="[0,0][100,50]"/>'
    '<node class="android.widget.Button" resource-id="com.app:id/ok" clickable="true" '
    'content-desc="" text="Aceptar" bounds="[40,500][200,560]"/></node></hierarchy>'
)
SCREEN_B = (
    '<hierarchy><node class="android.widget.FrameLayout" resource-id="" clickable="false" '
    'content-desc="" text="" bounds="[0,0][1080,2340]">'
    '<node class="android.widget.TextView" resource-id="com.android.systemui:id/clock" '
    'clickable="false" content-desc="" text="11:45" bounds="[0,0][100,50]"/>'
    '<node class="android.widget.Button" resource-id="com.app:id/ok" clickable="true" '
    'content-desc="" text="Accept" bounds="[50,510][210,570]"/></node></hierarchy>'
)


def _list(n):
    rows = "".join(
        f'<node class="android.widget.LinearLayout" resource-id="com.app:id/row" '
        f'clickable="true" content-desc="" text="item {i}" bounds="[0,{i*100}][1080,{i*100+100}]"/>'
        for i in range(n)
    )
    return (
        '<hierarchy><node class="androidx.recyclerview.widget.RecyclerView" '
        'resource-id="com.app:id/list" clickable="false" content-desc="" text="" '
        f'bounds="[0,0][1080,2340]">{rows}</node></hierarchy>'
    )


def test_structure_id_stable_across_text_and_chrome():
    assert structure_id("com.app/.A", SCREEN_A) == structure_id("com.app/.A", SCREEN_B)


def test_structure_id_distinct_for_different_widget_tree():
    extra = SCREEN_A.replace(
        "</node></hierarchy>",
        '<node class="android.widget.Switch" resource-id="com.app:id/toggle" '
        'clickable="true" content-desc="" text="" bounds="[0,600][100,660]"/></node></hierarchy>',
    )
    assert structure_id("com.app/.A", SCREEN_A) != structure_id("com.app/.A", extra)


def test_structure_id_namespace_sensitive():
    assert structure_id("com.app/.A", SCREEN_A) != structure_id("com.app/.B", SCREEN_A)


def test_structure_id_identical_for_adapter_list_siblings():
    # different rows / different content -> SAME structure (the dangerous case the
    # UNVERIFIABLE tier exists for: navigator must NOT claim confident arrival here)
    assert structure_id("com.app/.List", _list(3)) == structure_id("com.app/.List", _list(8))


def test_structure_id_launcher_short_circuits_to_fingerprint():
    ns = "com.sec.android.app.launcher/.activities.LauncherActivity"
    sid = structure_id(ns, SCREEN_A)
    assert sid.startswith("L")
    assert sid == fingerprint(ns, SCREEN_A)  # both tiers coincide for home


def test_structure_id_ignores_text_where_exact_fingerprint_keeps_it():
    # Compose wizard: same widget layout, different label text. EXACT (Compose profile,
    # text-sensitive) differs; structure_id (text-free) is the SAME -> keying the
    # record-time effectiveness filter on structure would wrongly drop the step edge.
    def compose(label):
        return (
            '<hierarchy><node class="androidx.compose.ui.platform.AndroidComposeView" '
            'resource-id="" clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.view.View" resource-id="" clickable="true" content-desc="" '
            f'text="{label}" bounds="[0,0][1080,200]"/></node></hierarchy>'
        )

    s1, s2 = compose("Step 1"), compose("Step 2")
    assert structure_id("com.app/.Wizard", s1) == structure_id("com.app/.Wizard", s2)
    assert fingerprint("com.app/.Wizard", s1, COMPOSE_PROFILE) != fingerprint(
        "com.app/.Wizard", s2, COMPOSE_PROFILE
    )
