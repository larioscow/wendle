import pytest

from wendle.calibration.scaling import scale_to_pixels


def test_scales_panel_to_display():
    assert scale_to_pixels(2048, abs_min=0, abs_max=4095, screen=1080) == 540


def test_scales_when_panel_equals_display():
    assert scale_to_pixels(270, abs_min=0, abs_max=1080, screen=1080) == 270


def test_clamps_out_of_range():
    assert scale_to_pixels(5000, abs_min=0, abs_max=4095, screen=1080) == 1079
    assert scale_to_pixels(-10, abs_min=0, abs_max=4095, screen=1080) == 0


def test_rejects_degenerate_axis():
    with pytest.raises(ValueError):
        scale_to_pixels(1, abs_min=5, abs_max=5, screen=1080)
