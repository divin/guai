"""Forecasting aggregation tests: daily panel builder with closed-day rule.

The panel builder turns raw sync tables into one row per local date with
forecast-ready targets. Table-driven tests encode the edge cases found in EDA
against the real DB (cumulative counters, partial days, sparse HR, TZ bounds).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from guai_hub.forecasting.aggregate import (
    CLOSURE_HOUR,
    MIN_HR_SAMPLES,
    DailyRow,
    build_daily_panel,
)
from guai_hub.models import (
    Activity,
    CheckinDefinition,
    CheckinInstance,
    CheckinResponse,
    HeartRate,
    Sleep,
    Weather,
)

BERLIN = ZoneInfo("Europe/Berlin")


def act(event_local: str, steps: int) -> Activity:
    """Activity row from a local Berlin timestamp (naive-local is deliberate)."""
    local = datetime.fromisoformat(event_local).replace(tzinfo=BERLIN)
    return Activity(
        event_time=local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        steps=steps,
    )


def hr(event_local: str, bpm: int) -> HeartRate:
    local = datetime.fromisoformat(event_local).replace(tzinfo=BERLIN)
    return HeartRate(
        event_time=local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        bpm=bpm,
    )


def wx(event_local: str, temp: float | None = None, rain: float | None = None) -> Weather:
    local = datetime.fromisoformat(event_local).replace(tzinfo=BERLIN)
    return Weather(
        event_time=local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        temperature_c=temp,
        precipitation_mm=rain,
    )


def bool_checkin(day: str, hour: int, value: int | None, defn: CheckinDefinition):
    inst = CheckinInstance(
        id=f"inst-{day}-{hour}",
        definition_id=defn.id,
        scheduled_local=f"{day}T21:00:00",
        scheduled_utc=f"{day}T20:00:00Z",
        status="answered" if value is not None else "missed",
    )
    resp = (
        CheckinResponse(id=f"resp-{day}-{hour}", instance_id=inst.id, value_boolean=value)
        if value is not None
        else None
    )
    return inst, resp


def choice_checkin(day: str, choice: str, defn: CheckinDefinition):
    inst = CheckinInstance(
        id=f"inst-{day}-choice",
        definition_id=defn.id,
        scheduled_local=f"{day}T21:00:00",
        scheduled_utc=f"{day}T20:00:00Z",
        status="answered",
    )
    resp = CheckinResponse(id=f"resp-{day}-choice", instance_id=inst.id, value_choice=choice)
    return inst, resp


def test_cumulative_steps_use_max_not_sum():
    """Sync bursts repeat the cumulative counter; daily value is the max."""
    # Nov-22-style burst: repeated samples at 1-second spacing, monotonically rising
    activity = [
        act("2026-11-22T10:00:00", 5000),
        act("2026-11-22T10:00:01", 5000),
        act("2026-11-22T10:00:02", 5000),
        act("2026-11-22T12:30:00", 8650),
        act("2026-11-22T12:30:01", 8650),
        act("2026-11-22T23:55:00", 8650),  # end-of-day sync closes the day
    ]
    rows = build_daily_panel(activity=activity, tz=BERLIN)
    day = {r.date: r for r in rows}[date(2026, 11, 22)]
    assert day.steps == 8650, "must be the max cumulative value, not the sum (~1M)"
    assert day.day_closed is True


def test_unclosed_day_has_no_steps_target():
    """A day whose last sync is before CLOSURE_HOUR stays open: no target."""
    assert CLOSURE_HOUR == 23
    activity = [
        act("2026-11-26T00:30:00", 100),
        act("2026-11-26T05:40:00", 3676),  # last sync 05:40 -> day still open
    ]
    rows = build_daily_panel(activity=activity, tz=BERLIN)
    day = {r.date: r for r in rows}[date(2026, 11, 26)]
    assert day.day_closed is False
    assert day.steps is None


def test_timezone_boundary_moves_events_to_local_day():
    """23:30 UTC is already the next day in Berlin; the panel must bucket by local date."""
    utc_activity = Activity(event_time="2026-11-22T23:30:00Z", steps=4000)
    rows = build_daily_panel(activity=[utc_activity], tz=BERLIN)
    by_date = {r.date: r for r in rows}
    assert date(2026, 11, 22) not in by_date
    # the event lands on Nov 23 local, but 00:30 local is not a day-end sync:
    # the day is unclosed and carries no steps target (no fake orphan values)
    day = by_date[date(2026, 11, 23)]
    assert day.day_closed is False
    assert day.steps is None
    # 2026-11-23 is a Monday
    assert day.dow == 0
    # a local 23:55 sync stays on its own local day (Sunday 2026-11-22 -> dow 6)
    rows = build_daily_panel(activity=[act("2026-11-22T23:55:00", 5000)], tz=BERLIN)
    assert rows[0].date == date(2026, 11, 22)
    assert rows[0].dow == 6


def test_resting_hr_is_daily_min_with_sparsity_filter():
    heart = [hr("2026-11-22T09:00:00", 70), hr("2026-11-22T09:01:00", 55)]
    heart += [hr("2026-11-22T09:02:00", 90)] * (MIN_HR_SAMPLES - 2)  # dense day
    heart += [hr("2026-11-04T09:00:00", 49)] + [hr("2026-11-04T09:01:00", 60)] * 50  # sparse day
    rows = build_daily_panel(heart_rate=heart, tz=BERLIN)
    by_date = {r.date: r for r in rows}
    dense = by_date[date(2026, 11, 22)]
    sparse = by_date[date(2026, 11, 4)]
    assert dense.resting_bpm == 55
    assert dense.hr_samples == MIN_HR_SAMPLES
    assert sparse.resting_bpm is None, "below MIN_HR_SAMPLES -> no usable resting HR"
    assert sparse.hr_samples == 51


def test_sleep_rows_keyed_by_night_date():
    sleep = [
        Sleep(night_date="2026-11-25", sleep_seconds=29460, restful_seconds=20280),
        Sleep(night_date="2026-11-26", sleep_seconds=None),
    ]
    rows = build_daily_panel(sleep=sleep, tz=BERLIN)
    by_date = {r.date: r for r in rows}
    assert by_date[date(2026, 11, 25)].sleep_seconds == 29460
    # a night with no usable seconds produces no panel row at all
    assert date(2026, 11, 26) not in by_date


def test_checkins_keyed_by_scheduled_local_day():
    bool_def = CheckinDefinition(
        id="def-bool",
        title="Headache?",
        answer_type="boolean",
        scale_min=0,
        scale_max=1,
        schedule="{}",
        due_window_minutes=120,
        reminder_minutes=30,
        active=True,
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )
    choice_def = CheckinDefinition(
        id="def-choice",
        title="How was your day?",
        answer_type="single_choice",
        scale_min=0,
        scale_max=10,
        options='["Good", "Neutral", "Bad"]',
        schedule="{}",
        due_window_minutes=120,
        reminder_minutes=30,
        active=True,
        created_at="2026-10-20T00:00:00Z",
        updated_at="2026-10-20T00:00:00Z",
    )
    # answered at 01:30 local the next morning, but belongs to its scheduled day
    late_inst, late_resp = bool_checkin("2026-11-20", 1, 1, bool_def)
    late_inst.answered_at = "2026-11-21T00:30:00Z"
    missed_inst, missed_resp = bool_checkin("2026-11-21", 21, None, bool_def)
    good_inst, good_resp = choice_checkin("2026-11-20", "Good", choice_def)
    bad_inst, bad_resp = choice_checkin("2026-11-22", "Bad", choice_def)

    rows = build_daily_panel(
        checkins=[
            (bool_def, late_inst, late_resp),
            (bool_def, missed_inst, missed_resp),
            (choice_def, good_inst, good_resp),
            (choice_def, bad_inst, bad_resp),
        ],
        tz=BERLIN,
    )
    by_date = {r.date: r for r in rows}
    # value lands on the scheduled day, not the answer day
    assert by_date[date(2026, 11, 20)].checkins["def-bool"] == 1
    assert by_date[date(2026, 11, 20)].checkins["def-choice"] == 0  # "Good" -> index 0
    assert by_date[date(2026, 11, 21)].checkins["def-bool"] is None  # missed
    assert by_date[date(2026, 11, 22)].checkins["def-choice"] == 2  # "Bad" -> index 2


def test_weather_daily_mean_and_sum():
    weather = [
        wx("2026-11-22T06:00:00", temp=5.0, rain=0.0),
        wx("2026-11-22T12:00:00", temp=10.0, rain=2.0),
        wx("2026-11-22T18:00:00", temp=None, rain=1.0),  # NULL temp ignored by mean
    ]
    rows = build_daily_panel(weather=weather, tz=BERLIN)
    day = {r.date: r for r in rows}[date(2026, 11, 22)]
    assert day.temp_c == 7.5
    assert day.rain_mm == 3.0


def test_panel_is_sorted_and_covers_gap_days():
    """Days with no data do not appear; rows come back date-sorted."""
    activity = [
        act("2026-11-20T23:30:00", 4000),
        act("2026-11-22T23:30:00", 5000),  # 21st has no data at all
    ]
    rows = build_daily_panel(activity=activity, tz=BERLIN)
    assert [r.date for r in rows] == [date(2026, 11, 20), date(2026, 11, 22)]


def test_daily_row_type_stable():
    rows = build_daily_panel(activity=[act("2026-11-22T23:30:00", 4000)], tz=BERLIN)
    assert all(isinstance(r, DailyRow) for r in rows)
