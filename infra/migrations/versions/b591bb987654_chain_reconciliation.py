"""Idempotent log cursor and canonical/orphaned event history."""
import sqlalchemy as sa
from alembic import op

revision = "b591bb987654"
down_revision = "91ad76543210"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chain_cases", sa.Column("indexed_block", sa.Integer(), nullable=True))
    op.add_column("chain_cases", sa.Column("indexed_block_hash", sa.String(66), nullable=True))
    op.create_table("chain_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("block_number", sa.Integer(), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("transaction_hash", sa.String(66), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("canonical", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("chain_id", "transaction_hash", "log_index"))
    op.create_index("ix_chain_logs_shipment_id", "chain_logs", ["shipment_id"])


def downgrade():
    op.drop_table("chain_logs")
    op.drop_column("chain_cases", "indexed_block_hash")
    op.drop_column("chain_cases", "indexed_block")
