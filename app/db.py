from sqlalchemy import JSON, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CampaignCreation(Base):
    __tablename__ = "campaign_creations"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    remote_id: Mapped[int | None]
    complete: Mapped[bool]


class StreamDraft(Base):
    __tablename__ = "stream_drafts"

    stream_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    revision: Mapped[str] = mapped_column(String(36))
    base_version: Mapped[str | None] = mapped_column(String(64))
    items: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    published_pins: Mapped[list[int]] = mapped_column(JSON)


class DemoState(Base):
    __tablename__ = "demo_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[str] = mapped_column(Text)


def make_engine(url: str):
    return create_engine(url)
