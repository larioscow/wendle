from wendle.driver.fake import FakeDriver


def test_fake_driver_returns_canned_shell_output():
    drv = FakeDriver(
        shell_outputs={"getevent -lp": "add device 1: /dev/input/event3\n"},
        display=(1080, 2340),
    )
    assert drv.shell("getevent -lp").startswith("add device 1")
    assert drv.display_size() == (1080, 2340)


def test_fake_driver_records_sendevent_calls():
    drv = FakeDriver(shell_outputs={}, display=(1080, 2340))
    drv.sendevent("/dev/input/event3", 3, 53, 100)
    assert drv.sent == [("/dev/input/event3", 3, 53, 100)]
