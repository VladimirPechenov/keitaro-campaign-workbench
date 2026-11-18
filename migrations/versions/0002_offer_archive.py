import sqlalchemy as sa
from alembic import op

revision = "0002_offer_archive"
down_revision = "0001_workbench_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stream_drafts",
        sa.Column("known_offers", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade():
    with op.batch_alter_table("stream_drafts") as batch:
        batch.drop_column("known_offers")
