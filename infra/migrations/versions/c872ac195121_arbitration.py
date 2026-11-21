"""Human arbitration proposals and signatures by unique Safe owner."""
import sqlalchemy as sa
from alembic import op

revision = "c872ac195121"
down_revision = "b591bb987654"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("arbitration_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.JSON(), nullable=False),
        sa.Column("typed_data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_arbitration_proposals_shipment_id", "arbitration_proposals", ["shipment_id"])
    op.create_table("arbitration_signatures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("arbitration_proposals.id"), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", "wallet_address"))
    op.create_index("ix_arbitration_signatures_proposal_id", "arbitration_signatures", ["proposal_id"])


def downgrade():
    op.drop_table("arbitration_signatures")
    op.drop_table("arbitration_proposals")
