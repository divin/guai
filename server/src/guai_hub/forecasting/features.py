"""Metric specs and feature-frame construction for the forecasting module.

A ``MetricSpec`` defines one forecastable metric: where its target lives on a
``DailyRow`` and which features describe it. Feature names are target-relative
(``target_lag1`` = the metric's own value one calendar day earlier) so the same
spec grammar covers sensor metrics and check-in metrics alike.

All lags are **calendar-based** (``date - timedelta``), looked up in the panel
by date. A missing day means ``None`` — never a fake lag computed from a
different, earlier row. ``to_matrix`` drops rows with any missing feature or
target, producing ONE eval set shared by every model compared on the metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from guai_hub.forecasting.aggregate import DailyRow


@dataclass(frozen=True)
class MetricSpec:
    """Definition of one forecastable metric."""

    metric: str  # table key in forecast_results, e.g. "steps", "checkin:<id>"
    target_field: str  # DailyRow attribute holding the target ("steps", "checkins", ...)
    features: tuple[str, ...]  # feature names, see _feature_extractor


STEPS = MetricSpec("steps", "steps", ("target_lag1", "target_lag7", "dow"))
RESTING_BPM = MetricSpec("resting_bpm", "resting_bpm", ("target_lag1", "target_lag7", "dow"))
SLEEP = MetricSpec("sleep_seconds", "sleep_seconds", ("target_lag1", "dow"))


def checkin_spec(definition_id: str) -> MetricSpec:
    """Spec for a check-in metric: target from row.checkins[definition_id]."""
    return MetricSpec(
        metric=f"checkin:{definition_id}",
        target_field="checkins",
        features=("target_lag1", "dow"),
    )


def _target_value(row: DailyRow, spec: MetricSpec) -> float | None:
    if spec.target_field == "checkins":
        key = spec.metric.removeprefix("checkin:")
        value = row.checkins.get(key)
    else:
        value = getattr(row, spec.target_field)
    return None if value is None else float(value)


def _feature_extractor(name: str, spec: MetricSpec):
    """Return ``extract(target_date, by_date) -> float | None`` for a feature name."""
    if name == "dow":
        return lambda target_date, by_date: float(target_date.weekday())
    if name == "target_lag1":
        return lambda target_date, by_date: _target_lag(spec, by_date, target_date, 1)
    if name == "target_lag7":
        return lambda target_date, by_date: _target_lag(spec, by_date, target_date, 7)
    raise ValueError(f"unknown feature {name!r} for metric {spec.metric!r}")


def _target_lag(
    spec: MetricSpec, by_date: dict[date, DailyRow], target_date: date, days: int
) -> float | None:
    """The metric's value ``days`` calendar days before ``target_date``.

    ``None`` when that day has no panel row or no usable value — a gap day or
    unclosed day never masquerades as a lag (EDA finding: partial-day values
    poison lag features).
    """
    row = by_date.get(target_date - timedelta(days=days))
    return None if row is None else _target_value(row, spec)


@dataclass
class FeatureFrame:
    """Per-metric feature rows aligned with targets; ``None`` = not usable."""

    metric: str
    feature_names: list[str]
    dates: list[date]
    features: list[list[float | None]]
    targets: list[float | None]


def build_frame(panel: list[DailyRow], spec: MetricSpec) -> FeatureFrame:
    """Build the feature frame for one metric over the panel's days."""
    by_date = {row.date: row for row in panel}
    extractors = [_feature_extractor(name, spec) for name in spec.features]
    frame = FeatureFrame(
        metric=spec.metric,
        feature_names=list(spec.features),
        dates=[],
        features=[],
        targets=[],
    )
    for row in panel:
        frame.dates.append(row.date)
        frame.features.append([ex(row.date, by_date) for ex in extractors])
        frame.targets.append(_target_value(row, spec))
    return frame


def build_query_features(
    spec: MetricSpec, target_date: date, panel: list[DailyRow]
) -> list[float | None]:
    """Features for a *future* date (no panel row yet): lags from history, dow."""
    by_date = {row.date: row for row in panel}
    extractors = [_feature_extractor(name, spec) for name in spec.features]
    return [ex(target_date, by_date) for ex in extractors]


def to_matrix(
    frame: FeatureFrame,
) -> tuple[list[str], list[list[float]], list[float], list[date]]:
    """Drop incomplete rows; every model compared on a metric sees this one set.

    Returns ``(feature_names, X, y, dates)``.
    """
    names = frame.feature_names
    X: list[list[float]] = []
    y: list[float] = []
    dates: list[date] = []
    for row_features, target, when in zip(frame.features, frame.targets, frame.dates):
        if target is None or any(v is None for v in row_features):
            continue
        X.append([float(v) for v in row_features])
        y.append(float(target))
        dates.append(when)
    return names, X, y, dates
