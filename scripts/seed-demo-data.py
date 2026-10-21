#!/usr/bin/env python3
"""Seed 14 days of plausible demo data into a GUAI dev database.

Fills activity (hourly snapshots), sleep (one row per night), heart rate
(daytime samples), weather (hourly) and check-in instances/responses for the
*active* daily definitions, with light correlations between the streams so the
dashboard's scatter plots have something to show:

- longer sleep -> better "Rate the day" scores
- low pressure -> worse headaches (choice + boolean)
- higher step counts -> "Feeling good today?" more likely true

Idempotent: health/weather rows are upserted by their time keys; check-in
instances are keyed by (definition_id, scheduled_utc) and skipped when they
already exist. Run repeatedly is safe.

Usage:
    python3 scripts/seed-demo-data.py [DB_PATH]

Defaults to server/.local-data/guai.sqlite3 (the local dev database).
"""

from __future__ import annotations

import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc  # keep the helpers readable on older Pythons

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "server/.local-data/guai.sqlite3"
TZ = ZoneInfo("Europe/Berlin")
DAYS = 14
SEED = 42

rng = random.Random(SEED)

# The four active daily definitions in the dev DB (checked before writing).
DEFS = {
    "feeling": "711fe9f5-4af5-4949-93e2-0660b94ea4ce",  # boolean
    "headache_bool": "9a3bb7ed-3fb5-4956-9dba-2e37830c1b78",  # boolean
    "rate": "164cf535-1548-4b67-abd1-3425cb6cc4c4",  # scale
    "headache_choice": "05858657-6713-46b6-a717-3697d2a1e034",  # single_choice
}
HEADACHE_OPTIONS = ["No headache", "Mild", "Severe"]


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def daily_total(day_index: int) -> float:
    """Plausible daily step totals: weekday activity, weekend dips."""
    weekday = (datetime.now(TZ) - timedelta(days=DAYS - day_index)).weekday()
    base = rng.gauss(7500, 2200) if weekday < 5 else rng.gauss(4200, 1500)
    return max(1800, base)


def main() -> None:
    db = sqlite3.connect(DB_PATH)
    now = datetime.now(TZ)
    today = now.date()

    # Anchor streams on one latent "feeling" factor per day plus weather dips.
    feeling = [rng.gauss(0, 1) for _ in range(DAYS)]
    daily_steps = [daily_total(i) for i in range(DAYS)]
    sleep_hours = [
        max(4.8, min(9.0, rng.gauss(7.2, 0.9) + 0.35 * feeling[i])) for i in range(DAYS)
    ]

    # --- activity: hourly cumulative snapshots, 0 overnight -------------
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        target = daily_steps[i]
        for hour in range(24):
            if hour < 7 or hour >= 23:
                frac = 0.0 if hour < 7 else 1.0
            else:
                # gentle ramp through the day with a lunch bump
                frac = min(1.0, max(0.0, (hour - 7) / 16 + rng.uniform(-0.04, 0.06)))
            local = datetime(day.year, day.month, day.day, hour, tzinfo=TZ)
            steps = int(target * frac)
            db.execute(
                "INSERT OR REPLACE INTO activity VALUES (?,?,?,?,?,?)",
                (
                    utc_iso(local),
                    steps,
                    round(steps * 0.72, 1),
                    round(1050 + 85 * hour * frac, 1),  # resting kcal cumulative
                    round(0.9 * steps / 20, 1),  # active kcal cumulative
                    int(260 * 16 * frac),  # active seconds cumulative
                ),
            )

    # --- sleep: one row per night --------------------------------------
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        total = int(sleep_hours[i] * 3600)
        restful = int(total * rng.uniform(0.82, 0.94))
        db.execute(
            "INSERT OR REPLACE INTO sleep VALUES (?,?,?)", (day.isoformat(), total, restful)
        )

    # --- heart rate: daytime samples ------------------------------------
    hr_baseline = rng.uniform(58, 64)
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        # less sleep or worse feeling -> slightly elevated resting HR
        offset = -0.6 * (sleep_hours[i] - 7.2) + 1.1 * feeling[i]
        for hour in range(7, 23):
            bpm = int(max(48, hr_baseline + offset + rng.gauss(0, 4) + (6 if hour in (12, 13) else 0)))
            raw = bpm + rng.randint(-3, 3)
            local = datetime(day.year, day.month, day.day, hour, rng.choice([0, 15, 30, 45]), tzinfo=TZ)
            db.execute(
                "INSERT OR REPLACE INTO heart_rate VALUES (?,?,?)", (utc_iso(local), bpm, raw)
            )

    # --- weather: hourly, with pressure dips on some days ---------------
    pressure = rng.uniform(1013, 1022)
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        dip = rng.random() < 0.25
        day_pressure = pressure + rng.gauss(0, 3) - (8 if dip else 0)
        rainy = rng.random() < 0.3
        for hour in range(24):
            local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)
            temp = rng.uniform(4, 9) + 5 * max(0.0, (1 - abs(hour - 14) / 8))  # afternoon peak
            db.execute(
                "INSERT OR REPLACE INTO weather VALUES (?,?,?,?,?,?,?,?)",
                (
                    utc_iso(local),
                    round(temp + rng.uniform(-1, 1), 1),
                    round(rng.uniform(60, 95), 0),
                    round(rng.uniform(1.5, 7), 1),
                    round(rng.uniform(0, 2.4) if rainy else 0.0, 1),
                    round(day_pressure + rng.uniform(-1.5, 1.5), 1),
                    (61 if rainy else 3),  # open-meteo style codes
                    "rain" if rainy else "partly cloudy",
                ),
            )

    # --- check-ins: instances + responses for the four daily defs -------
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        sleep_i = sleep_hours[i]
        rate = int(max(1, min(10, round(5.5 + 0.9 * feeling[i] + 0.4 * (sleep_i - 7.2) + rng.gauss(0, 0.8)))))
        good = rate >= 6 or rng.random() < 0.3
        # headache worse when pressure dipped that day (weather loop above)
        headache_day = rng.random() < (0.45 if dip else 0.12)
        severity = "Severe" if headache_day and rng.random() < 0.25 else "Mild" if headache_day else "No headache"

        answered = i < DAYS - 2  # leave the last two days for pending/missed states
        sched_local = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
        answers = {
            DEFS["rate"]: ("value_scale", rate),
            DEFS["feeling"]: ("value_boolean", 1 if good else 0),
            DEFS["headache_bool"]: ("value_boolean", 1 if headache_day else 0),
            DEFS["headache_choice"]: ("value_choice", severity),
        }
        for def_id, (col, value) in answers.items():
            inst_id = str(uuid.UUID(int=random.Random((i, def_id)).getrandbits(128)))
            sched_utc = utc_iso(sched_local)
            existing = db.execute(
                "SELECT status FROM checkin_instances WHERE definition_id=? AND scheduled_utc=?",
                (def_id, sched_utc),
            ).fetchone()
            if existing is not None:
                continue
            if not answered and i == DAYS - 1:
                status, answered_at, missed_at = "pending", None, None
            elif not answered:
                status, answered_at, missed_at = "missed", None, utc_iso(sched_local + timedelta(minutes=65))
            else:
                status = "answered"
                answered_at = utc_iso(sched_local + timedelta(minutes=rng.randint(5, 40)))
                missed_at = None
            db.execute(
                "INSERT INTO checkin_instances VALUES (?,?,?,?,?,?,?,?,?)",
                (inst_id, def_id, sched_local.strftime("%Y-%m-%dT%H:%M"), sched_utc, status, None, None, answered_at, missed_at),
            )
            if status == "answered":
                db.execute(
                    "INSERT INTO checkin_responses VALUES (?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        inst_id,
                        value if col == "value_boolean" else None,
                        value if col == "value_scale" else None,
                        value if col == "value_choice" else None,
                        answered_at,
                    ),
                )

    db.commit()
    for table in ("activity", "sleep", "heart_rate", "weather", "checkin_instances", "checkin_responses"):
        n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n} rows")
    db.close()


if __name__ == "__main__":
    main()
