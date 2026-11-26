"""Walk-forward backtest harness: every model scored on the same eval rows.

Rolling origin: for eval row ``i`` (``i >= warmup``), each model is fitted on
rows ``[:i]`` and predicts row ``i``. Rows are the metric's matrix from
``features.to_matrix`` — one pinned eval set per metric, so model comparisons
are never apples-to-oranges (the EDA spike's bug, fixed by construction).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from guai_hub.forecasting.models.base import (
    ClassificationForecaster,
    RegressionForecaster,
)

_LOGLOSS_CLAMP = 1e-3  # keep logloss finite when a prior hits 0 or 1

logger = logging.getLogger(__name__)


def walk_forward_regression(
    X: list[list[float]],
    y: list[float],
    factories: dict[str, Callable[[], RegressionForecaster]],
    warmup: int,
) -> dict[str, dict[str, float | int | None]]:
    """MAE per model over the shared eval rows ``X[warmup:]``."""
    predictions: dict[str, list[float]] = {name: [] for name in factories}
    for i in range(warmup, len(y)):
        for name, factory in factories.items():
            model = factory()
            model.fit(X[:i], y[:i])
            predictions[name].extend(model.predict(X[i : i + 1]))

    scores: dict[str, dict[str, float | int | None]] = {}
    for name, preds in predictions.items():
        errors = [abs(p - actual) for p, actual in zip(preds, y[warmup:], strict=True)]
        scores[name] = {
            "mae": sum(errors) / len(errors) if errors else None,
            "n": len(errors),
        }
    return scores


def walk_forward_classification(
    X: list[list[float]],
    y: list[float],
    factories: dict[str, Callable[[], ClassificationForecaster]],
    warmup: int,
) -> dict[str, dict[str, float | int | None]]:
    """Log loss and accuracy per model over the shared eval rows ``X[warmup:]``."""
    """Log loss and accuracy per model over the shared eval rows ``X[warmup:]``.

    Multi-class: ``y`` holds class indices and a model's probability rows must
    cover them. A candidate that raises at any fold (e.g. an FM whose training
    data lacks a class) is dropped entirely — the remaining candidates keep
    their shared eval rows (fail-soft per candidate).
    """
    probability_rows: dict[str, list[list[float]]] = {name: [] for name in factories}
    for i in range(warmup, len(y)):
        for name, factory in list(factories.items()):
            try:
                model = factory()
                model.fit(X[:i], y[:i])
                proba = model.predict_proba(X[i : i + 1])[0]
                probability_rows[name].append(proba)
            except Exception:  # noqa: BLE001 - a broken candidate must not sink the rest
                probability_rows.pop(name, None)
                factories.pop(name, None)

    scores: dict[str, dict[str, float | int | None]] = {}
    for name, probas in probability_rows.items():
        actuals = y[warmup : warmup + len(probas)]
        losses = [-_log(clamp(row[int(a)])) for row, a in zip(probas, actuals, strict=True)]
        correct = [
            max(range(len(row)), key=lambda k: row[k]) == int(a)
            for row, a in zip(probas, actuals, strict=True)
        ]
        scores[name] = {
            "logloss": sum(losses) / len(losses) if losses else None,
            "accuracy": sum(correct) / len(correct) if correct else None,
            "n": len(losses),
        }
    return scores


def clamp(p: float) -> float:
    return min(max(p, _LOGLOSS_CLAMP), 1 - _LOGLOSS_CLAMP)


def _log(p: float) -> float:
    import math

    return math.log(p)
