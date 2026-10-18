"""轻量 Prometheus 指标注册表（无额外依赖）。

仅实现项目实际需要的 counter / histogram，输出 Prometheus 文本格式。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

_LATENCY_BUCKETS: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_histograms: dict[
    tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]
] = {}


def _normalize_labels(labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + body + "}"


def inc(name: str, labels: dict[str, Any] | None = None, value: float = 1.0) -> None:
    key = (name, _normalize_labels(labels))
    with _lock:
        _counters[key] += value


def observe(name: str, value: float, labels: dict[str, Any] | None = None) -> None:
    key = (name, _normalize_labels(labels))
    with _lock:
        histogram = _histograms.get(key)
        if histogram is None:
            histogram = {
                "buckets": [0 for _ in _LATENCY_BUCKETS],
                "count": 0,
                "sum": 0.0,
            }
            _histograms[key] = histogram
        # 每个观测只计入首个满足 value <= boundary 的桶，输出时再做累计
        for index, boundary in enumerate(_LATENCY_BUCKETS):
            if value <= boundary:
                histogram["buckets"][index] += 1
                break
        histogram["count"] += 1
        histogram["sum"] += value


def render_prometheus() -> str:
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        histograms = {
            key: {
                "buckets": list(value["buckets"]),
                "count": value["count"],
                "sum": value["sum"],
            }
            for key, value in _histograms.items()
        }

    for (name, labels), value in sorted(counters.items()):
        lines.append(f"{name}{_format_labels(labels)} {value:g}")

    for (name, labels), histogram in sorted(histograms.items()):
        cumulative = 0
        for boundary, count in zip(_LATENCY_BUCKETS, histogram["buckets"], strict=True):
            cumulative += count
            bucket_labels = labels + (("le", f"{boundary:g}"),)
            lines.append(
                f"{name}_bucket{_format_labels(bucket_labels)} {cumulative}"
            )
        inf_labels = labels + (("le", "+Inf"),)
        lines.append(f"{name}_bucket{_format_labels(inf_labels)} {histogram['count']}")
        lines.append(f"{name}_sum{_format_labels(labels)} {histogram['sum']:g}")
        lines.append(f"{name}_count{_format_labels(labels)} {histogram['count']}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset() -> None:
    """测试使用：清空所有指标。"""
    with _lock:
        _counters.clear()
        _histograms.clear()
