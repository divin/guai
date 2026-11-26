"""Feature frame and baseline forecaster tests (1.7.0 step 4 gate).

Synthetic data throughout; the real-DB reproduction check (steps seasonal-naive
vs EDA) runs separately against the snapshot, not in unit tests.
"""

from datetime import date, timedelta

import pytest

from guai_hub.forecasting.aggregate import DailyRow
from guai_hub.forecasting.backtest import (
    walk_forward_classification,
    walk_forward_regression,
)
from guai_hub.forecasting.features import (
    FeatureFrame,
    MetricSpec,
    build_frame,
    to_matrix,
)
from guai_hub.forecasting.models import (
    ExpandingPriorClassifier,
    LagColumnRegressor,
    new_classifier,
    new_regressor,
)

BERLIN = None  # panel rows are already local; the builder takes no tz


def panel_row(d: date, **kw) -> DailyRow:
    base = {"date": d, "dow": d.weekday()}
    base.update(kw)
    return DailyRow(**base)


def steps_panel(values: dict[date, int | None]) -> list[DailyRow]:
    return [panel_row(d, steps=v, day_closed=v is not None) for d, v in sorted(values.items())]


STEPS_SPEC = MetricSpec("steps", "steps", ("target_lag1", "target_lag7", "dow"))


def test_lag_features_use_calendar_dates_across_gaps():
    """lag-1 of Nov 24 is Nov 23's value; a missing Nov 23 makes it None (no fake lag)."""
    # Nov 20 (closed), Nov 22 (closed) — Nov 21 absent, Nov 23 absent
    panel = steps_panel(
        {
            date(2026, 11, 20): 4000,
            date(2026, 11, 22): 5000,
            date(2026, 11, 24): 6000,
            date(2026, 12, 1): 7000,
        }
    )
    frame = build_frame(panel, STEPS_SPEC)
    by_date = dict(zip(frame.dates, frame.features))
    idx_lag1 = frame.feature_names.index("target_lag1")
    idx_lag7 = frame.feature_names.index("target_lag7")

    # Nov 24: lag1 -> Nov 23 missing -> None; lag7 -> Nov 17 missing -> None
    assert by_date[date(2026, 11, 24)][idx_lag1] is None
    assert by_date[date(2026, 11, 24)][idx_lag7] is None
    # Dec 1: lag7 -> Nov 24 = 6000
    assert by_date[date(2026, 12, 1)][idx_lag7] == 6000.0


def test_to_matrix_drops_incomplete_rows_and_pins_eval_set():
    """One matrix, one eval set: rows with any None feature/target are dropped."""
    panel = steps_panel(
        {
            date(2026, 11, 13): 1000,  # gives lag7/lag1 for later rows
            date(2026, 11, 20): 2000,
            date(2026, 11, 21): None,  # unclosed -> dropped
            date(2026, 11, 27): 3000,  # lag1 -> Nov 26 missing -> dropped
            date(2026, 11, 28): 4000,  # lag1 -> Nov 27 = 3000, lag7 -> Nov 21 None -> dropped
            date(2026, 12, 4): 5000,  # lag7 -> Nov 27 = 3000, lag1 -> Dec 3 missing -> dropped
        }
    )
    frame = build_frame(panel, STEPS_SPEC)
    names, X, y, dates = to_matrix(frame)

    assert names == ["target_lag1", "target_lag7", "dow"]
    # only Nov 20 and Dec 4 have every feature and a target... Nov 20: lag7 -> Nov 13 = 1000,
    # lag1 -> Nov 19 missing -> dropped. Dec 4: lag7 = 3000, lag1 missing -> dropped.
    assert dates == []
    assert X == [] and y == []

    # a denser panel keeps rows: every day closed from Nov 13 to Dec 4
    dense = steps_panel({date(2026, 11, 13) + timedelta(days=i): 1000 + i for i in range(22)})
    frame = build_frame(dense, STEPS_SPEC)
    names, X, y, dates = to_matrix(frame)
    assert len(y) == 22 - 7  # first 7 days lack lag7
    assert dates[0] == date(2026, 11, 20)


def test_lag_column_regressor_reads_designated_column():
    model = LagColumnRegressor(name="lag1", column_index=0)
    model.fit([[1.0, 2.0], [3.0, 4.0]], [10.0, 20.0])  # fit is a no-op for baselines
    assert model.predict([[5.0, 6.0], [7.0, 8.0]]) == [5.0, 7.0]


def test_expanding_prior_uses_training_targets_only():
    model = ExpandingPriorClassifier()
    model.fit([[0.0], [0.0], [0.0], [1.0]], [0, 0, 0, 1])  # 3/4 negatives
    proba = model.predict_proba([[0.0]])
    assert len(proba[0]) == 2
    assert proba[0][1] == pytest.approx(0.25)


def test_expanding_prior_probabilities_sum_to_one():
    model = ExpandingPriorClassifier()
    model.fit([[0.0], [1.0]], [0, 1])
    for row in model.predict_proba([[0.0], [1.0]]):
        assert abs(sum(row) - 1.0) < 1e-9


def test_registry_factories_return_fresh_models():
    """Each walk-forward fold must get an unfitted model instance."""
    reg = new_regressor("lag1", column_index=0)
    assert isinstance(reg, LagColumnRegressor)
    assert new_regressor("seasonal_naive", column_index=1) is not reg

    cls = new_classifier("expanding_prior")
    assert isinstance(cls, ExpandingPriorClassifier)
    with pytest.raises(ValueError):
        new_regressor("no_such_model", column_index=0)


def test_walk_forward_regression_pinned_rows_and_mae():
    """On a +1/day ramp, lag-1 is off by exactly 1 and lag-7 by exactly 7."""
    # 20 consecutive closed days; steps[i] = 100 + i; lag1 of day i is steps of i-1
    days = [date(2026, 11, 1) + timedelta(days=i) for i in range(20)]
    panel = steps_panel({d: 100 + i for i, d in enumerate(days)})
    frame = build_frame(panel, STEPS_SPEC)
    _, X, y, _ = to_matrix(frame)

    scores = walk_forward_regression(
        X,
        y,
        {
            "lag1": lambda: LagColumnRegressor("lag1", column_index=0),
            "sn": lambda: LagColumnRegressor("sn", column_index=1),
        },
        warmup=7,
    )
    assert scores["lag1"]["mae"] == 1.0
    # seasonal naive (column 1) is off by 7 steps per row on this ramp
    assert scores["sn"]["mae"] == 7.0
    # every model scored the same eval rows (6 of them: 13 matrix rows - 7 warmup)
    assert scores["sn"]["n"] == scores["lag1"]["n"] == 6


def test_walk_forward_classification_logloss():
    """Base-rate predictor on all-zero targets: perfect logloss, accuracy 1.0."""
    days = [date(2026, 11, 1) + timedelta(days=i) for i in range(15)]
    panel = [panel_row(d, steps=100, day_closed=True, checkins={"def-h": 0}) for d in days]
    spec = MetricSpec("checkin:def-h", "checkins", ("target_lag1",))
    frame = build_frame(panel, spec)
    _, X, y, _ = to_matrix(frame)

    scores = walk_forward_classification(
        X, y, {"prior": lambda: ExpandingPriorClassifier()}, warmup=5
    )
    assert scores["prior"]["logloss"] < 0.01
    assert scores["prior"]["accuracy"] == 1.0


def test_feature_frame_type():
    frame = build_frame(steps_panel({date(2026, 11, 20): 100}), STEPS_SPEC)
    assert isinstance(frame, FeatureFrame)
    assert frame.feature_names == ["target_lag1", "target_lag7", "dow"]
