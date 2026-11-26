"""forecast_results: stored nightly forecasts (schema v2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_results",
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("target_date", sa.String(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("probabilities", sa.String(), nullable=True),
        sa.Column("reliable", sa.Boolean(), nullable=False),
        sa.Column("backtest_score", sa.Float(), nullable=True),
        sa.Column("baseline_score", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.String(), nullable=False),
        sa.CheckConstraint("horizon BETWEEN 1 AND 3", name="ck_fc_horizon"),
        sa.PrimaryKeyConstraint("metric", "target_date", "horizon"),
    )


def downgrade() -> None:
    op.drop_table("forecast_results")
