from wendle.fingerprint.settle import settle
from wendle.fingerprint.signature import FingerprintConfig

A = '<hierarchy><node class="A" resource-id="" clickable="true" content-desc="" text="" bounds="[0,0][1,1]"/></hierarchy>'
B = '<hierarchy><node class="B" resource-id="" clickable="true" content-desc="" text="" bounds="[0,0][1,1]"/></hierarchy>'
CFG = lambda _xml: FingerprintConfig()  # noqa: E731


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _feeder(seq):
    it = iter(seq)
    return lambda: next(it)


def test_settles_after_n_consecutive_identical():
    clk = _Clock()

    def sleep(dt):
        clk.t += dt

    xml, ns, settled = settle(
        _feeder([A, A, A]), lambda: "ns", CFG, need=3, sleep=sleep, clock=clk
    )
    assert settled is True


def test_transition_then_settle():
    clk = _Clock()
    # launcher (A), then app appears (B), then app settles (B, B)
    xml, ns, settled = settle(
        _feeder([A, B, B, B]), lambda: "ns", CFG, need=3,
        sleep=lambda dt: setattr(clk, "t", clk.t + dt), clock=clk,
    )
    assert settled is True
    assert "B" in structural_of(xml)


def test_namespace_change_resets_consecutive():
    clk = _Clock()
    namespaces = _feeder(["launcher", "launcher", "app", "app", "app"])
    xml, ns, settled = settle(
        _feeder([A, A, A, A, A]), namespaces, CFG, need=3,
        sleep=lambda dt: setattr(clk, "t", clk.t + dt), clock=clk,
    )
    # sig is constant but ns flips launcher->app at step 3, resetting the run;
    # it then needs 3 consecutive 'app' dumps
    assert settled is True
    assert ns == "app"


def test_never_settles_times_out():
    clk = _Clock()
    xml, ns, settled = settle(
        _feeder([A, B] * 50), lambda: "ns", CFG, need=3, interval=0.4, max_wait=2.0,
        sleep=lambda dt: setattr(clk, "t", clk.t + dt), clock=clk,
    )
    assert settled is False


def structural_of(xml):
    from wendle.fingerprint.signature import structural_signature

    return structural_signature(xml)
