"""Forecaster interface and baseline models.

Every forecastable model — including the naive baselines the backtest competes
against — implements one of two sklearn-shaped protocols:

- ``RegressionForecaster``: ``fit(features, targets)`` then ``predict(rows)``
  returning one float per row.
- ``ClassificationForecaster``: ``fit(features, targets)`` then
  ``predict_proba(rows)`` returning ``[p_class0, p_class1, ...]`` per row.

Baselines are honest competitors, not decoration: they read a designated
column of the query row (the lag feature the backtest matrix already carries),
so they never see anything a real model would not see.
"""

from __future__ import annotations

from typing import Protocol


class RegressionForecaster(Protocol):
    """Minimal regression interface shared by baselines and tabular FMs."""

    name: str

    def fit(self, features: list[list[float]], targets: list[float]) -> None: ...

    def predict(self, features: list[list[float]]) -> list[float]: ...


class ClassificationForecaster(Protocol):
    """Minimal binary/multi-class classification interface."""

    name: str

    def fit(self, features: list[list[float]], targets: list[int]) -> None: ...

    def predict_proba(self, features: list[list[float]]) -> list[list[float]]: ...


class LagColumnRegressor:
    """Baseline: predict the query row's own value in one designated column.

    With ``column_index`` pointing at the ``target_lag1`` feature this is the
    classic lag-1 baseline; at ``target_lag7`` it is the seasonal-naive
    ("same weekday last week") baseline.
    """

    def __init__(self, name: str, column_index: int) -> None:
        self.name = name
        self.column_index = column_index

    def fit(self, features: list[list[float]], targets: list[float]) -> None:
        """No-op: the baseline has nothing to learn."""

    def predict(self, features: list[list[float]]) -> list[float]:
        return [float(row[self.column_index]) for row in features]


class ExpandingPriorClassifier:
    """Baseline: predict the class prior seen in the training targets.

    In a walk-forward backtest the training set grows one day at a time, so
    this is exactly the "expanding base rate" — the honest trivial competitor
    for check-in forecasts (EDA: it beat an FM that peeked at features).
    Multi-class capable: single-choice check-ins get one column per option.
    """

    name = "expanding_prior"

    def __init__(self, n_classes: int = 2) -> None:
        if n_classes < 2:
            raise ValueError("n_classes must be >= 2")
        self.n_classes = n_classes
        self._proba: list[float] | None = None

    def fit(self, features: list[list[float]], targets: list[int]) -> None:
        if not targets:
            raise ValueError("no training targets")
        counts = [0] * self.n_classes
        for target in targets:
            index = int(target)
            if not 0 <= index < self.n_classes:
                raise ValueError(f"target {index} outside 0..{self.n_classes - 1}")
            counts[index] += 1
        self._proba = [count / len(targets) for count in counts]

    def predict_proba(self, features: list[list[float]]) -> list[list[float]]:
        if self._proba is None:
            raise RuntimeError("fit before predict_proba")
        return [list(self._proba) for _ in features]

    def classes(self) -> list[int]:
        """Class indices of the probability columns (always 0..n_classes-1)."""
        return list(range(self.n_classes))


def new_regressor(name: str, column_index: int) -> RegressionForecaster:
    """Factory for regression models: baselines plus the Inpriora tabular FM."""
    if name == "lag1":
        return LagColumnRegressor(name="lag1", column_index=column_index)
    if name == "seasonal_naive":
        return LagColumnRegressor(name="seasonal_naive", column_index=column_index)
    if name == "inpriora_regressor":
        from guai_hub.forecasting.models.inpriora_model import InprioraRegressorModel

        return InprioraRegressorModel()
    raise ValueError(f"unknown regressor {name!r}")


def new_classifier(name: str, n_classes: int = 2) -> ClassificationForecaster:
    """Factory for classification models: baseline prior plus the Inpriora FM."""
    if name == "expanding_prior":
        return ExpandingPriorClassifier(n_classes=n_classes)
    if name == "inpriora_classifier":
        from guai_hub.forecasting.models.inpriora_model import InprioraClassifierModel

        return InprioraClassifierModel()
    raise ValueError(f"unknown classifier {name!r}")
