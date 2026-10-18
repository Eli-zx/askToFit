"""SSE 增量解析（Locust 压测共用）单元测试。"""

from __future__ import annotations

import json

from scripts.loadtest_sse import parse_sse_events


def test_parses_multiple_events_and_keeps_remainder() -> None:
    buffer = (
        'event: meta\ndata: {"intent": "logistics"}\n\n'
        'event: delta\ndata: {"content": "满99"}\n\n'
        'event: done\ndata: {"content": "满99包邮"}'
    )
    events, remainder = parse_sse_events(buffer)

    assert [event["event"] for event in events] == ["meta", "delta"]
    assert events[0]["data"] == {"intent": "logistics"}
    assert events[1]["data"] == {"content": "满99"}
    assert remainder.startswith("event: done")


def test_handles_cross_chunk_boundaries() -> None:
    first = 'event: delta\ndata: {"content": "包'
    events, remainder = parse_sse_events(first)
    assert events == []

    second = '邮"}\n\nevent: done\ndata: {"content": "包邮"}\n\n'
    events, remainder = parse_sse_events(remainder + second)
    assert [event["event"] for event in events] == ["delta", "done"]
    assert events[0]["data"]["content"] == "包邮"
    assert remainder == ""


def test_normalizes_crlf_and_ignores_comments() -> None:
    buffer = ': keep-alive\r\nevent: meta\r\ndata: {"a": 1}\r\n\r\n'
    events, remainder = parse_sse_events(buffer)
    assert events == [{"event": "meta", "data": {"a": 1}, "raw": '{"a": 1}', "invalid": False}]
    assert remainder == ""


def test_marks_invalid_json_without_crashing() -> None:
    events, _ = parse_sse_events("event: delta\ndata: not-json\n\n")
    assert len(events) == 1
    assert events[0]["event"] == "delta"
    assert events[0]["data"] is None
    assert events[0]["invalid"] is True
    assert events[0]["raw"] == "not-json"


def test_handles_error_event_and_multiline_data() -> None:
    buffer = (
        'event: error\ndata: {"message": "检索失败"}\n\n'
        "event: note\ndata: line-1\ndata: line-2\n\n"
    )
    events, _ = parse_sse_events(buffer)
    assert events[0]["event"] == "error"
    assert events[0]["data"] == {"message": "检索失败"}
    assert events[1]["raw"] == "line-1\nline-2"
    assert events[1]["invalid"] is True


def test_empty_buffer_returns_nothing() -> None:
    assert parse_sse_events("") == ([], "")
    assert parse_sse_events("\n\n") == ([], "")


def test_parsed_done_payload_keeps_content() -> None:
    payload = {"content": "满99元包邮", "sources": [{"uid": "a"}]}
    buffer = f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    events, _ = parse_sse_events(buffer)
    assert events[0]["data"]["content"] == "满99元包邮"
