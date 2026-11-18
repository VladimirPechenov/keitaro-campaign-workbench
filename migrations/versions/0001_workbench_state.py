import sqlalchemy as sa
from alembic import op

revision = "0001_workbench_state"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaign_creations",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("remote_id", sa.Integer, nullable=True),
        sa.Column("complete", sa.Boolean, nullable=False),
    )
    op.create_table(
        "stream_drafts",
        sa.Column("stream_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("base_version", sa.String(64), nullable=True),
        sa.Column("items", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("published_pins", sa.JSON, nullable=False),
    )
    op.create_table(
        "demo_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data", sa.Text, nullable=False),
    )


def downgrade():
    op.drop_table("demo_state")
    op.drop_table("stream_drafts")
    op.drop_table("campaign_creations")
