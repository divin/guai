"""Inpriora tabular FM wrapper — lazy import, fail-soft.

The forecasting extra (``guai-hub[forecast]``) pulls Inpriora and CPU torch.
This module adapts Inpriora's sklearn-style API to the forecaster protocols in
``models/base``. torch/inpriora are imported only inside ``fm_available`` and
the model constructors, so a server without the extra boots normally and the
nightly job falls back to baselines (fail-soft, per PLAN.md 1.7.0).

The Hugging Face checkpoint revision is pinned: a floating ``main`` could
silently change model behavior between releases.
"""

from __future__ import annotations

# ai-for-data/inpriora on Hugging Face — pinned checkpoint revision.
HF_REVISION = "8c41fb1a17dc775cf5c2d76bbe91097d55a040c9"


class ForecastModelUnavailable(RuntimeError):
    """The forecasting extra (inpriora/torch) is not installed."""


def fm_available() -> bool:
    """True when the forecasting extra is importable."""
    try:
        import inpriora  # noqa: F401
    except ImportError:
        return False
    return True


class InprioraRegressorModel:
    """Adapter: InprioraRegressor as a RegressionForecaster."""

    name = "inpriora_regressor"

    def __init__(self, random_seed: int = 0) -> None:
        if not fm_available():
            raise ForecastModelUnavailable(
                "inpriora is not installed — install the 'forecast' extra"
            )
        from inpriora import InprioraRegressor

        self._model = InprioraRegressor(random_seed=random_seed, revision=HF_REVISION)

    def fit(self, features: list[list[float]], targets: list[float]) -> None:
        self._model.fit(features, targets)

    def predict(self, features: list[list[float]]) -> list[float]:
        return [float(v) for v in self._model.predict(features)]


class InprioraClassifierModel:
    """Adapter: InprioraClassifier as a ClassificationForecaster (binary 0/1)."""

    name = "inpriora_classifier"

    def __init__(self, random_seed: int = 0) -> None:
        if not fm_available():
            raise ForecastModelUnavailable(
                "inpriora is not installed — install the 'forecast' extra"
            )
        from inpriora import InprioraClassifier

        self._model = InprioraClassifier(random_seed=random_seed, revision=HF_REVISION)

    def fit(self, features: list[list[float]], targets: list[int]) -> None:
        self._model.fit(features, targets)

    def predict_proba(self, features: list[list[float]]) -> list[list[float]]:
        raw = self._model.predict_proba(features)
        return [[float(p) for p in row] for row in raw]
