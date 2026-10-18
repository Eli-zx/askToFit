"""Prometheus 指标注册表测试。"""

import pytest

from app.services.metrics import inc, observe, render_prometheus, reset


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset()
    yield
    reset()


def test_render_counters_with_labels() -> None:
    inc("asktofit_intent_total", {"intent": "logistics", "source": "bert"})
    inc("asktofit_intent_total", {"intent": "logistics", "source": "bert"}, value=2)
    inc("asktofit_knowledge_gaps_total", {"intent": "other"})

    text = render_prometheus()

    assert (
        'asktofit_intent_total{intent="logistics",source="bert"} 3' in text
    )
    assert 'asktofit_knowledge_gaps_total{intent="other"} 1' in text


def test_histogram_buckets_are_cumulative() -> None:
    observe("asktofit_http_request_duration_seconds", 0.03, {"path": "/a"})
    observe("asktofit_http_request_duration_seconds", 0.2, {"path": "/a"})

    lines = render_prometheus().splitlines()

    assert (
        'asktofit_http_request_duration_seconds_bucket{path="/a",le="0.05"} 1'
        in lines
    )
    assert (
        'asktofit_http_request_duration_seconds_bucket{path="/a",le="0.1"} 1'
        in lines
    )
    assert (
        'asktofit_http_request_duration_seconds_bucket{path="/a",le="0.25"} 2'
        in lines
    )
    assert (
        'asktofit_http_request_duration_seconds_bucket{path="/a",le="+Inf"} 2'
        in lines
    )
    assert 'asktofit_http_request_duration_seconds_count{path="/a"} 2' in lines
    assert 'asktofit_http_request_duration_seconds_sum{path="/a"} 0.23' in lines
