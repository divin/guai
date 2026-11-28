"""Inpriora FM wrapper tests (1.7.0 step 5 gate).

The fail-soft contract runs everywhere: without the ``forecast`` extra the FM
factories raise ``ForecastModelUnavailable`` and ``fm_available`` is False.
Adapter behavior tests are skipped when the extra is absent (hermetic CI).
"""

import pytest

from guai_hub.forecasting.models import new_classifier, new_regressor
from guai_hub.forecasting.models.inpriora_model import (
    ForecastModelUnavailable,
    fm_available,
)


def test_fail_soft_when_extra_missing():
    """No inpriora -> FM factories raise the dedicated error; baselines unaffected."""
    if fm_available():
        pytest.skip("forecast extra installed; fail-soft path not exercised")
    with pytest.raises(ForecastModelUnavailable):
        new_regressor("inpriora_regressor", column_index=0)
    with pytest.raises(ForecastModelUnavailable):
        new_classifier("inpriora_classifier")
    # baselines keep working without the extra
    assert new_regressor("lag1", column_index=0).name == "lag1"
    assert new_classifier("expanding_prior").name == "expanding_prior"


def test_unknown_model_still_value_error():
    with pytest.raises(ValueError):
        new_regressor("gpt-9", column_index=0)


@pytest.mark.skipif(not fm_available(), reason="forecast extra not installed")
def test_inpriora_regressor_adapter_synthetic():
    """Adapter learns a linear ramp on synthetic data (CPU, no network)."""
    model = new_regressor("inpriora_regressor", column_index=0)
    X = [[float(i), float(i % 7)] for i in range(30)]
    y = [50.0 + 3.0 * x[0] for x in X]
    model.fit(X, y)
    preds = model.predict([[31.0, 3.0], [32.0, 4.0]])
    assert len(preds) == 2
    assert all(isinstance(p, float) for p in preds)
    assert abs(preds[0] - 143.0) < 30.0, "should track the ramp roughly"


@pytest.mark.skipif(not fm_available(), reason="forecast extra not installed")
def test_inpriora_classifier_exposes_actual_classes():
    """columns/classes_ shrink when a class is absent from training — the job
    keys stored probabilities by these indices, never by column position."""
    model = new_classifier("inpriora_classifier")
    X = [[float(i)] for i in range(12)]
    y = [0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2]  # class 1 never answered
    model.fit(X, y)
    assert model.classes() == [0, 2]
    assert len(model.predict_proba([[1.0]])[0]) == 2


@pytest.mark.skipif(not fm_available(), reason="forecast extra not installed")
def test_inpriora_classifier_adapter_synthetic():
    """Adapter returns rows of per-class probabilities summing to 1."""
    model = new_classifier("inpriora_classifier")
    X = [[float(i % 2)] for i in range(20)]
    y = [i % 2 for i in range(20)]
    model.fit(X, y)
    proba = model.predict_proba([[0.0], [1.0]])
    assert len(proba) == 2
    for row in proba:
        assert abs(sum(row) - 1.0) < 1e-6
