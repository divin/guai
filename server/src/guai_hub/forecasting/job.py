"""Nightly forecast job: panel -> selection -> backtest -> forecast -> store.

Runs on the primary instance's scheduler (06:00 local by default). Per
selected metric:

1. Build the feature frame from the daily panel and the shared eval matrix.
2. Walk-forward backtest all candidates (baselines always; the Inpriora FM
   when the forecast extra is installed).
3. Ship the winner fitted on all rows. ``reliable`` means the winner strictly
   beat the best *baseline* on the pinned eval rows — a baseline winning its
   own comparison demonstrates no skill, so it is tagged unreliable.
4. Upsert ``forecast_results`` rows for the horizons whose query features are
   complete.

Horizon semantics: **h=1 forecasts today** — at 06:00, yesterday is closed so
today's lag features are clean (the EDA backtest measured exactly this). h=2+
target future days whose lag-1 would be today's still-unclosed value, so they
are attempted and stored only when their features happen to be complete; with
a 06:00-only run they typically stay empty rather than being faked (see
PLAN.md — a rerun mechanism is a 1.8 candidate).

Metrics with too little data to backtest are still forecast (baselines only)
and tagged unreliable — the UI shows them honestly instead of hiding them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from guai_hub.config import Settings
from guai_hub.database import Database
from guai_hub.forecasting.aggregate import build_daily_panel
from guai_hub.forecasting.backtest import walk_forward_classification, walk_forward_regression
from guai_hub.forecasting.features import (
    build_frame,
    build_query_features,
    to_matrix,
)
from guai_hub.forecasting.models.base import new_classifier, new_regressor
from guai_hub.forecasting.selection import (
    build_registry,
    prune_orphan_forecasts,
    resolve_selection,
)
from guai_hub.models import (
    Activity,
    CheckinDefinition,
    CheckinInstance,
    CheckinResponse,
    ForecastResult,
    HeartRate,
    Sleep,
    Weather,
)
from guai_hub.timeutil import utc_now_iso

logger = logging.getLogger(__name__)

WARMUP = 12  # folds start here; matches the EDA backtest
MIN_BACKTEST_ROWS = WARMUP + 2  # below this a walk-forward says nothing
MIN_FIT_ROWS = 3  # below this the metric is skipped entirely
HORIZONS = (1, 2, 3)

REGRESSION_BASELINES = ("lag1", "seasonal_naive")
CLASSIFICATION_BASELINES = ("expanding_prior",)
FM_NAMES = ("inpriora_regressor", "inpriora_classifier")


async def run_forecast_job(settings: Settings, db: Database) -> dict[str, int]:
    """Produce and store forecasts for every selected metric. Returns a summary."""
    tz = settings.timezone

    async with db.session_factory() as session:
        activity = list((await session.execute(select(Activity))).scalars().all())
        heart_rate = list((await session.execute(select(HeartRate))).scalars().all())
        sleep = list((await session.execute(select(Sleep))).scalars().all())
        weather = list((await session.execute(select(Weather))).scalars().all())
        definitions = list((await session.execute(select(CheckinDefinition))).scalars().all())
        instances = list((await session.execute(select(CheckinInstance))).scalars().all())
        responses = list((await session.execute(select(CheckinResponse))).scalars().all())

    definition_by_id = {d.id: d for d in definitions}
    instance_by_id = {i.id: i for i in instances}
    checkins = []
    for response in responses:
        instance = instance_by_id.get(response.instance_id)
        if instance is None:
            continue
        definition = definition_by_id.get(instance.definition_id)
        if definition is not None:
            checkins.append((definition, instance, response))

    panel = build_daily_panel(
        tz=tz,
        activity=activity,
        heart_rate=heart_rate,
        sleep=sleep,
        weather=weather,
        checkins=checkins,
    )
    registry = build_registry(definitions)
    selected = await resolve_selection(db, registry)
    await prune_orphan_forecasts(db, {e.spec.metric for e in selected})

    today = datetime.now(tz).date()
    computed_at = utc_now_iso()
    rows_written = 0
    per_metric: dict[str, int] = {}

    for entry in selected:
        try:
            written = await _forecast_metric(db, entry, panel, definitions, today, computed_at)
        except Exception:
            logger.exception("forecast job failed for metric %s", entry.spec.metric)
            written = 0
        per_metric[entry.spec.metric] = written
        rows_written += written

    return {"metrics": len(selected), "rows": rows_written, "per_metric": per_metric}


async def _forecast_metric(db, entry, panel, definitions, today, computed_at) -> int:
    """Backtest, fit the winner, and store forecast rows for one metric."""
    spec = entry.spec
    frame = build_frame(panel, spec)
    names, X, y, _dates = to_matrix(frame)
    if len(y) < MIN_FIT_ROWS:
        logger.info("metric %s has %d usable rows; skipping", spec.metric, len(y))
        return 0

    if entry.kind == "regression":
        candidates = _regression_candidates(names)
        scores = (
            walk_forward_regression(X, y, candidates, WARMUP) if len(y) >= MIN_BACKTEST_ROWS else {}
        )
        winner, reliable, backtest_score, baseline_score = _pick_winner(
            scores, REGRESSION_BASELINES, "mae"
        )
        fitted = new_regressor(winner, column_index=names.index("target_lag1"))
        fitted.fit(X, y)
    else:
        n_classes = _n_classes(entry.spec.metric, definitions)
        candidates = _classification_candidates(n_classes)
        scores = (
            walk_forward_classification(X, y, candidates, WARMUP)
            if len(y) >= MIN_BACKTEST_ROWS
            else {}
        )
        winner, reliable, backtest_score, baseline_score = _pick_winner(
            scores, CLASSIFICATION_BASELINES, "logloss"
        )
        fitted = new_classifier(winner, n_classes=n_classes)
        fitted.fit(X, [int(v) for v in y])

    rows: list[ForecastResult] = []
    for horizon in HORIZONS:
        target_date = today + timedelta(days=horizon - 1)
        features = build_query_features(spec, target_date, panel)
        if any(v is None for v in features):
            continue  # incomplete features (e.g. tomorrow needs today's unclosed value)
        query = [[float(v) for v in features]]
        if entry.kind == "regression":
            value = fitted.predict(query)[0]
            rows.append(
                _result_row(
                    entry,
                    target_date,
                    horizon,
                    winner,
                    reliable,
                    backtest_score,
                    baseline_score,
                    computed_at,
                    value=value,
                )
            )
        else:
            proba = fitted.predict_proba(query)[0]
            rows.append(
                _result_row(
                    entry,
                    target_date,
                    horizon,
                    winner,
                    reliable,
                    backtest_score,
                    baseline_score,
                    computed_at,
                    probabilities=json.dumps({str(k): round(p, 6) for k, p in enumerate(proba)}),
                )
            )

    async with db.session_factory() as session:
        for row in rows:
            await session.merge(row)
        await session.commit()
    logger.info("metric %s: model=%s reliable=%s rows=%d", spec.metric, winner, reliable, len(rows))
    return len(rows)


def _result_row(
    entry,
    target_date,
    horizon,
    model,
    reliable,
    backtest_score,
    baseline_score,
    computed_at,
    value=None,
    probabilities=None,
) -> ForecastResult:
    return ForecastResult(
        metric=entry.spec.metric,
        target_date=target_date.isoformat(),
        horizon=horizon,
        model=model,
        value=value,
        probabilities=probabilities,
        reliable=reliable,
        backtest_score=backtest_score,
        baseline_score=baseline_score,
        computed_at=computed_at,
    )


def _regression_candidates(names: list[str]):
    """Baseline candidates always; the FM candidate only when importable."""
    candidates: dict = {
        name: (lambda n=name: new_regressor(n, column_index=names.index("target_lag1")))
        for name in REGRESSION_BASELINES
    }
    if _fm_available_quietly():
        candidates["inpriora_regressor"] = lambda: new_regressor(
            "inpriora_regressor", column_index=0
        )
    return candidates


def _classification_candidates(n_classes: int):
    candidates: dict = {
        name: (lambda n=name, k=n_classes: new_classifier(n, n_classes=k))
        for name in CLASSIFICATION_BASELINES
    }
    if _fm_available_quietly():
        candidates["inpriora_classifier"] = lambda: new_classifier("inpriora_classifier")
    return candidates


def _fm_available_quietly() -> bool:
    try:
        from guai_hub.forecasting.models.inpriora_model import fm_available

        return fm_available()
    except Exception:  # noqa: BLE001 - missing extra must not break the job
        return False


def _pick_winner(scores: dict, baselines: tuple[str, ...], score_key: str):
    """Winner = lowest score overall; reliable = strictly beats best baseline.

    A baseline winning its own comparison demonstrates no skill over naive
    rules, hence reliable=False. No backtest (too little data) -> the most
    honest baseline ships, tagged unreliable.
    """
    if not scores:
        winner = baselines[0]
        return winner, False, None, None

    baseline_scores = [
        scores[name][score_key]
        for name in scores
        if name in baselines and scores[name][score_key] is not None
    ]
    best_baseline = min(baseline_scores) if baseline_scores else None
    winner = min(
        (name for name, s in scores.items() if s[score_key] is not None),
        key=lambda name: scores[name][score_key],
    )
    winner_score = scores[winner][score_key]
    reliable = best_baseline is not None and winner_score < best_baseline
    return winner, reliable, winner_score, best_baseline


def _n_classes(metric: str, definitions: list[CheckinDefinition]) -> int:
    """Class count for a check-in metric: boolean -> 2, choice -> option count."""
    if not metric.startswith("checkin:"):
        return 2
    definition_id = metric.removeprefix("checkin:")
    definition = next((d for d in definitions if d.id == definition_id), None)
    if definition is not None and definition.answer_type == "single_choice" and definition.options:
        return len(json.loads(definition.options))
    return 2
