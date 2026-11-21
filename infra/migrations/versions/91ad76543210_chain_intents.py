"""Wallet-bound, session-scoped challenges and immutable typed signing intents."""
import sqlalchemy as sa
from alembic import op

revision = "91ad76543210"
down_revision = "8d19b246810a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("wallet_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("typed_data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_wallet_challenges_user_id", "wallet_challenges", ["user_id"])
    op.create_table("chain_cases",
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("escrow_address", sa.String(42), nullable=False, unique=True),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("chain_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("typed_data", sa.JSON(), nullable=False),
        sa.Column("contract_payload", sa.JSON(), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_chain_intents_shipment_id", "chain_intents", ["shipment_id"])
    op.create_table("chain_signatures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intent_id", sa.String(36), sa.ForeignKey("chain_intents.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("intent_id", "user_id"))
    op.create_index("ix_chain_signatures_intent_id", "chain_signatures", ["intent_id"])


def downgrade():
    for name in ("chain_signatures", "chain_intents", "chain_cases", "wallet_challenges"):
        op.drop_table(name)
