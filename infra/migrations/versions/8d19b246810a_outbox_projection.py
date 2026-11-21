"""Durable internal workflow projection and bounded retry/dead letters."""
import sqlalchemy as sa
from alembic import op

revision = "8d19b246810a"
down_revision = "bf7cb05623ea"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outbox_events", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("outbox_events", sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("outbox_events", sa.Column("error_code", sa.String(60), nullable=True))
    op.add_column("outbox_events", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_outbox_pending", "outbox_events", ["available_at"],
                    postgresql_where=sa.text("delivered_at IS NULL AND dead_at IS NULL"))
    op.create_table("workflow_events",
        sa.Column("event_id", sa.String(36), sa.ForeignKey("outbox_events.id"), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measurement_scope", sa.String(30), nullable=False))
    op.create_index("ix_workflow_events_shipment_id", "workflow_events", ["shipment_id"])


def downgrade():
    op.drop_table("workflow_events")
    op.drop_index("ix_outbox_pending", table_name="outbox_events")
    for column in ("dead_at", "error_code", "available_at", "attempts"):
        op.drop_column("outbox_events", column)
