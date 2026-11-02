"""演示预热脚本：问题解析、SSE 结果判定与失败不中断。"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts import prewarm_demo as prewarm


def test_load_questions_skips_comments_blank_and_duplicates(tmp_path) -> None:
    path = tmp_path / "questions.txt"
    path.write_text(
        "# 注释\n\n第一问？\n第二问？\n第一问？\n  # 另一条注释\n第三问？\n",
        encoding="utf-8",
    )

    assert prewarm.load_questions(path) == ["第一问？", "第二问？", "第三问？"]


def test_load_questions_rejects_empty_file(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("# 只有注释\n\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        prewarm.load_questions(path)


def _sse(*events: tuple[str, dict]) -> str:
    blocks = []
    for name, data in events:
        blocks.append(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
    return "".join(blocks)


async def _result_for(body: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        return await prewarm._consume_stream(client, 1, "问题", "normal")


async def test_consume_stream_marks_cache_hit() -> None:
    body = _sse(
        ("status", {"stage": "retrieving"}),
        ("meta", {"sources": []}),
        ("delta", {"content": "回答"}),
        ("done", {"cache_hit": True, "content": "回答"}),
    )

    result = await _result_for(body)

    assert result.ok is True
    assert result.cache_hit is True
    assert result.ttft_ms >= 0
    assert result.error == ""


async def test_consume_stream_records_error_event() -> None:
    body = _sse(
        ("meta", {"sources": []}),
        ("error", {"message": "当前咨询较多，请稍后重试"}),
    )

    result = await _result_for(body)

    assert result.ok is False
    assert "当前咨询较多" in result.error


async def test_consume_stream_requires_done_event() -> None:
    body = _sse(
        ("meta", {"sources": []}),
        ("delta", {"content": "半截回答"}),
    )

    result = await _result_for(body)

    assert result.ok is False
    assert "done" in result.error


async def test_consume_stream_reports_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="知识库不存在")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await prewarm._consume_stream(client, 1, "问题", "admin")

    assert result.ok is False
    assert "HTTP 400" in result.error
