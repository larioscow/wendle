from wendle.capture.gestures import segment_gestures
from wendle.capture.events import parse_getevent_stream
from wendle.capture.protocols.type_b import TypeBProtocol
from wendle.record.stream import stream_gestures


def _line(ts, code, value, type_="EV_ABS"):
    return f"[{ts:>14.6f}] /dev/input/event3: {type_:<11} {code:<22} {value}\n"


def _tap(t_down, t_up, x, y):
    return [
        _line(t_down, "ABS_MT_SLOT", "00000000"),
        _line(t_down, "ABS_MT_TRACKING_ID", "00000abc"),
        _line(t_down, "ABS_MT_POSITION_X", f"{x:08x}"),
        _line(t_down, "ABS_MT_POSITION_Y", f"{y:08x}"),
        _line(t_down, "SYN_REPORT", "00000000", "EV_SYN"),
        _line(t_up, "ABS_MT_TRACKING_ID", "ffffffff"),
        _line(t_up, "SYN_REPORT", "00000000", "EV_SYN"),
    ]


def test_stream_matches_batch_segmentation():
    lines = _tap(10.0, 10.05, 100, 200) + _tap(11.0, 11.05, 300, 400)
    blob = "".join(lines)
    proto = TypeBProtocol()

    batch = segment_gestures(parse_getevent_stream(blob), protocol=proto)
    streamed = list(stream_gestures(lines, proto))

    assert [(g.kind, g.x, g.y) for g in streamed] == [(g.kind, g.x, g.y) for g in batch]
    assert len(streamed) == 2


def test_stream_holds_back_open_contact_until_closed():
    # a down with no up yet -> nothing final mid-stream; flushed (truncated) at end
    proto = TypeBProtocol()
    open_only = [
        _line(10.0, "ABS_MT_TRACKING_ID", "00000abc"),
        _line(10.0, "ABS_MT_POSITION_X", f"{50:08x}"),
        _line(10.0, "ABS_MT_POSITION_Y", f"{60:08x}"),
    ]
    # consume lazily: before end-of-stream there are no finalized gestures
    gen = stream_gestures(iter(open_only), proto)
    out = list(gen)
    assert len(out) == 1  # flushed at end of stream
    assert out[0].truncated is True


def test_stream_yields_first_tap_before_second_completes():
    proto = TypeBProtocol()
    lines = _tap(10.0, 10.05, 100, 200)
    # add only the DOWN of a second tap (still open)
    lines += [
        _line(11.0, "ABS_MT_TRACKING_ID", "00000fff"),
        _line(11.0, "ABS_MT_POSITION_X", f"{300:08x}"),
    ]
    out = list(stream_gestures(lines, proto))
    # first tap finalized; second still open -> flushed truncated at end
    assert out[0].kind == "tap" and (out[0].x, out[0].y) == (100, 200)
    assert out[-1].truncated is True
