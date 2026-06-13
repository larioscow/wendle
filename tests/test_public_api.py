"""The v1 public API surface: `import wendle` must expose the three verbs (record, replay,
navigate) + the honesty result types + the drivers, WITHOUT a device or uiautomator2 at import time.
A library nobody can import-and-use is not a shippable v1."""
import importlib


def test_top_level_exports_the_three_verbs_and_honesty_types():
    m = importlib.import_module("wendle")
    for name in ("record", "replay", "replay_recording", "navigate",
                 "Graph", "NavOutcome", "ReplayResult", "U2Driver", "FakeDriver", "__version__"):
        assert hasattr(m, name), f"wendle.{name} missing from the public API"
    assert callable(m.record) and callable(m.navigate) and callable(m.replay)
    assert m.replay is m.replay_recording  # `replay` is the friendly alias


def test_import_does_not_require_a_device_or_uiautomator2():
    # importing the package must not import uiautomator2 / connect a device (lazy in U2Driver).
    import sys
    sys.modules.pop("uiautomator2", None)
    importlib.reload(importlib.import_module("wendle"))
    assert "uiautomator2" not in sys.modules  # not pulled in by the package import
