"""Initial schema — all tables for Squawk Engine.

Revision ID: 001
Revises: None
Create Date: 2026-04-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("phone_number", sa.Text(), nullable=False, unique=True),
        sa.Column("referral_code", sa.Text(), nullable=False, unique=True),
        sa.Column("referred_by", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("trial_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_status", sa.Text(), nullable=False, server_default="trial"),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True, unique=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_time", sa.Text(), nullable=True),
        sa.Column("notification_tz", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "ticker_subscriptions",
        sa.Column("id", sa.dialects.postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "ticker"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "referrals",
        sa.Column("id", sa.dialects.postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("referrer_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("referred_user_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reward_granted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reward_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "squawk_logs",
        sa.Column("id", sa.dialects.postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("news_url_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "squawk_deliveries",
        sa.Column("id", sa.dialects.postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("squawk_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("squawk_logs.id"), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("squawk_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("squawk_deliveries")
    op.drop_table("squawk_logs")
    op.drop_table("referrals")
    op.drop_table("subscriptions")
    op.drop_table("ticker_subscriptions")
    op.drop_table("users")
