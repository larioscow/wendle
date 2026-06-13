"""The library-level record() entry — orchestrates calibrate + RecordSession + the gesture stream
into a saved navigable Graph. Device-free via an injected `gestures` iterable (the same seam the
recorder unit tests use), so a consumer can record without copying a spike script."""
from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.models import DeviceProfile
from wendle.record import record

PROFILE = DeviceProfile(touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")
NOSLEEP = {"sleep": lambda _dt: None}


def _screen(pkg, act, rid="go"):
    return (f'<hierarchy><node class="android.widget.FrameLayout" package="{pkg}" resource-id="" '
            f'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.Button" package="{pkg}" resource-id="{pkg}:id/{rid}" '
            f'clickable="true" content-desc="" text="Go" bounds="[40,500][1040,620]"/></node></hierarchy>')


def _dumpsys(pkg, act):
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def test_record_returns_a_graph_built_from_injected_gestures():
    L = ("com.sec.android.app.launcher", ".Home")
    A = ("com.app", ".A")
    drv = FakeDriver(hierarchies=[_screen(*L)] * 3 + [_screen(*A)] * 3,
                     dumpsys_pairs=[_dumpsys(*L)] * 3 + [_dumpsys(*A)] * 3, display=(1080, 2340))
    tap = Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=560)
    seen = []
    g = record(drv, profile=PROFILE, gestures=[tap], settle_kwargs=NOSLEEP,
               on_transition=lambda t: seen.append(t))
    # one screen-to-screen transition recorded, the graph is returned (not just saved)
    assert g.g.number_of_nodes() == 2 and g.g.number_of_edges() == 1
    assert len(seen) == 1 and seen[0] is not None       # live progress callback fired
    assert g.anchors()                                   # the launcher->app anchor was set


def test_record_saves_when_out_given(tmp_path):
    from wendle.graph import Graph
    A = ("com.app", ".A")
    drv = FakeDriver(hierarchies=[_screen(*A)] * 3, dumpsys_pairs=[_dumpsys(*A)] * 3, display=(1080, 2340))
    out = str(tmp_path / "rec.json")
    g = record(drv, profile=PROFILE, gestures=[], out=out, settle_kwargs=NOSLEEP)
    reloaded = Graph.from_json(open(out).read())
    assert reloaded.g.number_of_nodes() == g.g.number_of_nodes()  # round-trips through the saved file
