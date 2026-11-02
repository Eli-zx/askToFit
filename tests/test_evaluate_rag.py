"""评估脚本的指标可用性与 RAGAS 缓存判定。"""

from scripts.evaluate_rag import (
    _cached_scores_usable,
    _first_usable_value,
    _metric_available,
)


def test_metric_available_rejects_none_nan_and_text() -> None:
    assert _metric_available(0.0) is True
    assert _metric_available(0.75) is True
    assert _metric_available("0.5") is True
    assert _metric_available(None) is False
    assert _metric_available(float("nan")) is False
    assert _metric_available(float("inf")) is False
    assert _metric_available("abc") is False


def test_cached_scores_require_every_metric_to_be_usable() -> None:
    names = ["faithfulness", "context_precision"]

    assert _cached_scores_usable(
        {"faithfulness": 0.8, "context_precision": 0.6}, names
    )
    # 单项失败时不能把 None 永久缓存复用
    assert not _cached_scores_usable(
        {"faithfulness": 0.8, "context_precision": None}, names
    )
    assert not _cached_scores_usable({"faithfulness": 0.8}, names)
    assert not _cached_scores_usable(None, names)


def test_first_usable_value_prefers_long_ragas_column_name() -> None:
    """RAGAS 0.3.0 用 llm_context_precision_with_reference 作列名，不能被缺失别名覆盖。"""
    row = {"llm_context_precision_with_reference": 0.75}
    columns = ["llm_context_precision_with_reference", "context_precision"]

    assert _first_usable_value(row, columns) == 0.75
    # 长列名为 NaN 时回退到短列名
    assert (
        _first_usable_value(
            {"llm_context_precision_with_reference": float("nan"),
             "context_precision": 0.5},
            columns,
        )
        == 0.5
    )
    assert _first_usable_value({}, columns) is None
    # 0.0 是有效分数，不能被当成缺失
    assert _first_usable_value({"context_precision": 0.0}, columns) == 0.0

