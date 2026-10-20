"""initial schema (v1): all GUAI tables

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _shared() -> None:
    pass


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "activity",
        sa.Column("event_time", sa.String(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("resting_kcal", sa.Float(), nullable=True),
        sa.Column("active_kcal", sa.Float(), nullable=True),
        sa.Column("active_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("event_time"),
    )

    op.create_table(
        "heart_rate",
        sa.Column("event_time", sa.String(), nullable=False),
        sa.Column("bpm", sa.Integer(), nullable=False),
        sa.Column("bpm_raw", sa.Integer(), nullable=True),
        sa.CheckConstraint("bpm BETWEEN 20 AND 250", name="ck_hr_bpm"),
        sa.PrimaryKeyConstraint("event_time"),
    )

    op.create_table(
        "sleep",
        sa.Column("night_date", sa.String(), nullable=False),
        sa.Column("sleep_seconds", sa.Integer(), nullable=True),
        sa.Column("restful_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("night_date"),
    )

    op.create_table(
        "weather",
        sa.Column("event_time", sa.String(), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("wind_speed_mps", sa.Float(), nullable=True),
        sa.Column("precipitation_mm", sa.Float(), nullable=True),
        sa.Column("pressure_hpa", sa.Float(), nullable=True),
        sa.Column("weather_code", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("event_time"),
    )

    op.create_table(
        "checkin_definitions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "answer_type",
            sa.String(),
            sa.CheckConstraint(
                "answer_type IN ('boolean','scale','single_choice')",
                name="ck_def_answer_type",
            ),
            nullable=False,
        ),
        sa.Column("scale_min", sa.Integer(), nullable=False),
        sa.Column("scale_max", sa.Integer(), nullable=False),
        sa.Column("options", sa.String(), nullable=True),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column("due_window_minutes", sa.Integer(), nullable=False),
        sa.Column("reminder_minutes", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("due_window_minutes BETWEEN 5 AND 1440", name="ck_def_due_window"),
        sa.CheckConstraint(
            "reminder_minutes = 0 OR reminder_minutes BETWEEN 10 AND 1440",
            name="ck_def_reminder",
        ),
        sa.CheckConstraint("scale_min < scale_max", name="ck_def_scale_range"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkin_definitions_active_type", "checkin_definitions", ["active", "answer_type"]
    )

    op.create_table(
        "checkin_instances",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("definition_id", sa.String(), nullable=False),
        sa.Column("scheduled_local", sa.String(), nullable=False),
        sa.Column("scheduled_utc", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pushover_sent_at", sa.String(), nullable=True),
        sa.Column("reminder_sent_at", sa.String(), nullable=True),
        sa.Column("answered_at", sa.String(), nullable=True),
        sa.Column("missed_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','answered','missed')",
            name="ck_inst_status",
        ),
        sa.ForeignKeyConstraint(["definition_id"], ["checkin_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "scheduled_utc", name="uq_inst_def_sched"),
    )
    op.create_index("ix_inst_status", "checkin_instances", ["status"])
    op.create_index("ix_inst_definition_id", "checkin_instances", ["definition_id"])

    op.create_table(
        "checkin_responses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("value_boolean", sa.Integer(), nullable=True),
        sa.Column("value_scale", sa.Integer(), nullable=True),
        sa.Column("value_choice", sa.String(), nullable=True),
        sa.Column("submitted_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "(value_boolean IS NOT NULL) + "
            "(value_scale IS NOT NULL) + "
            "(value_choice IS NOT NULL) = 1",
            name="ck_resp_exactly_one",
        ),
        sa.ForeignKeyConstraint(["instance_id"], ["checkin_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkin_responses_instance_id", "checkin_responses", ["instance_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_checkin_responses_instance_id", table_name="checkin_responses")
    op.drop_table("checkin_responses")
    op.drop_index("ix_inst_definition_id", table_name="checkin_instances")
    op.drop_index("ix_inst_status", table_name="checkin_instances")
    op.drop_table("checkin_instances")
    op.drop_index("ix_checkin_definitions_active_type", table_name="checkin_definitions")
    op.drop_table("checkin_definitions")
    op.drop_table("weather")
    op.drop_table("sleep")
    op.drop_table("heart_rate")
    op.drop_table("activity")
    op.drop_table("settings")
