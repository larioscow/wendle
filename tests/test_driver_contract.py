"""Driver-contract conformance: every concrete driver must implement the WHOLE DeviceDriver
surface with a matching signature. Without this, a method added to the base (or one whose
signature changes) silently leaves FakeDriver inheriting the NotImplementedError stub — green
suite, but the device-free TDD foundation has quietly stopped covering that method. The growing
~25-method seam makes this drift easy and invisible, so we assert it mechanically.
"""
import inspect

import pytest

from wendle.driver.base import DeviceDriver, _is_not_implemented_stub
from wendle.driver.fake import FakeDriver
from wendle.driver.u2_driver import U2Driver

# Concrete helpers on the base that are MEANT to be inherited, not overridden.
SHARED_CONCRETE = {"supports"}


def _contract_methods():
    return sorted(
        name
        for name, _ in inspect.getmembers(DeviceDriver, predicate=inspect.isfunction)
        if not name.startswith("_") and name not in SHARED_CONCRETE
    )


@pytest.mark.parametrize("driver_cls", [FakeDriver, U2Driver], ids=lambda c: c.__name__)
@pytest.mark.parametrize("name", _contract_methods())
def test_driver_implements_contract_method(driver_cls, name):
    # "implements" = a real body, not a NotImplementedError stub (inherited OR re-declared).
    own = getattr(driver_cls, name, None)
    assert own is not None, f"{driver_cls.__name__} is missing {name}()"
    assert not _is_not_implemented_stub(own), (
        f"{driver_cls.__name__}.{name}() is a NotImplementedError stub -> not a real implementation"
    )


@pytest.mark.parametrize("driver_cls", [FakeDriver, U2Driver], ids=lambda c: c.__name__)
@pytest.mark.parametrize("name", _contract_methods())
def test_driver_signature_matches_contract(driver_cls, name):
    # name + KIND (positional/keyword-only/var-args), not just names, so making an arg
    # keyword-only or adding **kwargs is caught. Defaults/annotations are intentionally ignored
    # (concrete drivers legitimately omit annotations).
    def shape(fn):
        return [(p.name, p.kind) for p in inspect.signature(fn).parameters.values()]

    base_shape, own_shape = shape(getattr(DeviceDriver, name)), shape(getattr(driver_cls, name))
    assert own_shape == base_shape, (
        f"{driver_cls.__name__}.{name} signature drifted from DeviceDriver.{name}: "
        f"{own_shape} != {base_shape}"
    )


def test_supports_true_for_real_false_for_stub():
    # supports() == True only for a method with a real body; a re-declared NotImplementedError
    # stub reports False (the must-fix: 'overridden' is not enough, it must actually be implemented).
    fake = FakeDriver()
    assert fake.supports("launch_monkey") and fake.supports("type_text")  # real impls
    assert not fake.supports("no_such_capability")  # unknown -> False, never AttributeError

    class PartialDriver(FakeDriver):
        def launch_monkey(self, package):  # re-declared as a bare stub
            raise NotImplementedError

    partial = PartialDriver()
    assert not partial.supports("launch_monkey")  # re-declared stub -> NOT supported
    assert partial.supports("type_text")          # still real (inherited from FakeDriver)


def test_stub_detector_distinguishes_stub_from_real_body():
    class Sample(DeviceDriver):
        def shell(self, cmd):  # required abstract -> give it a real body
            return ""

        def display_size(self):
            return (0, 0)

        def sendevent(self, node, type_, code, value):
            return None

        def stub(self):
            raise NotImplementedError

        def stub_called(self):
            raise NotImplementedError("not yet")

        def real(self):
            x = 1
            raise NotImplementedError if x else ValueError  # has real logic before the raise

    assert _is_not_implemented_stub(Sample.stub)
    assert _is_not_implemented_stub(Sample.stub_called)
    assert not _is_not_implemented_stub(Sample.real)
    assert not _is_not_implemented_stub(Sample.shell)
