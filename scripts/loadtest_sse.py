"""SSE 增量解析工具，供 Locust 压测与单元测试共用。"""

from __future__ import annotations

import json
from typing import Any


def _parse_block(block: str) -> dict[str, Any] | None:
    event = "message"
    data_lines: list[str] = []
    has_field = False
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        has_field = True
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value or "message"
        elif field == "data":
            data_lines.append(value)

    if not has_field:
        return None

    raw = "\n".join(data_lines)
    data: Any = None
    invalid = False
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            invalid = True
    return {"event": event, "data": data, "raw": raw, "invalid": invalid}


def parse_sse_events(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """解析缓冲区中的完整 SSE 事件。

    返回 ``(events, remainder)``：``remainder`` 是尚未以空行结束的半截事件，
    调用方应在下一块数据到达后继续拼接，避免跨 chunk 边界丢事件。
    """
    normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
    events: list[dict[str, Any]] = []
    while True:
        index = normalized.find("\n\n")
        if index < 0:
            break
        block, normalized = normalized[:index], normalized[index + 2 :]
        parsed = _parse_block(block)
        if parsed is not None:
            events.append(parsed)
    return events, normalized
