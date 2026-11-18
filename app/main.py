from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.campaigns import campaign_summary, create_campaign
from app.config import Settings
from app.db import make_engine
from app.demo import DemoTracker
from app.editor import change_offer, finish_draft, require_stream, sync_stream_views
from app.schemas import CreateCampaign, Id, OfferChange, Period, Revision
from app.tracker import Tracker, WorkbenchError, choices

STATIC = Path(__file__).parent / "static"


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as db:
        yield db


def get_tracker(request: Request) -> Tracker:
    return request.app.state.tracker


Db = Annotated[Session, Depends(get_db)]
Keitaro = Annotated[Tracker, Depends(get_tracker)]


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        engine = make_engine(settings.db_url)
        app.state.engine = engine
        if settings.demo_mode and transport is None:
            backend = DemoTracker(engine)
            tracker_transport = httpx.MockTransport(backend.handle)
        else:
            tracker_transport = transport
        api_root = str(settings.keitaro_base_url or "https://tracker.example").rstrip("/")
        headers = {"Accept": "application/json"}
        if not settings.demo_mode:
            headers["Api-Key"] = settings.keitaro_api_key.get_secret_value()
        try:
            with httpx.Client(
                base_url=f"{api_root}/admin_api/v1/",
                headers=headers,
                timeout=12,
                transport=tracker_transport,
            ) as client:
                app.state.tracker = Tracker(client, settings)
                yield
        finally:
            engine.dispose()

    app = FastAPI(title="Keitaro Workbench", version="0.1.0", lifespan=lifespan)
    mutate = Lock()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(WorkbenchError)
    async def workbench_error(request: Request, error: WorkbenchError):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return {
            "demo": settings.demo_mode,
            "prefix": settings.campaign_prefix,
            "tracker_url": str(settings.keitaro_base_url).rstrip("/")
            if not settings.demo_mode
            else None,
            "timezone": settings.report_timezone,
        }

    @app.get("/api/catalog")
    def catalog(tracker: Keitaro):
        return tracker.catalog()

    @app.get("/api/offers")
    def list_offers(tracker: Keitaro):
        rows = tracker.offers()
        return {
            "active": choices(rows),
            "names": {row["id"]: row["name"] for row in rows},
        }

    @app.get("/api/campaigns")
    def list_campaigns(
        tracker: Keitaro,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ):
        page = tracker.campaign_page(offset, limit)
        return {
            "items": [campaign_summary(campaign) for campaign in page],
            "next_offset": offset + len(page) if len(page) == limit else None,
        }

    @app.post("/api/campaigns", status_code=201)
    def add_campaign(body: CreateCampaign, db: Db, tracker: Keitaro):
        with mutate:
            return create_campaign(db, tracker, body)

    @app.get("/api/campaigns/{campaign_id}")
    def campaign_detail(campaign_id: Id, db: Db, tracker: Keitaro):
        campaign = tracker.campaign(campaign_id)
        with mutate:
            streams = sorted(
                tracker.streams(campaign_id),
                key=lambda stream: (stream.get("position") or 0, stream["id"]),
            )
            stream_views = sync_stream_views(db, streams)
        return {
            **campaign_summary(campaign),
            "domain_id": campaign.get("domain_id"),
            "group_id": campaign.get("group_id"),
            "traffic_source_id": campaign.get("traffic_source_id"),
            "streams": stream_views,
        }

    @app.get("/api/campaigns/{campaign_id}/report")
    def campaign_report(campaign_id: Id, tracker: Keitaro, days: Period = Period.WEEK):
        return tracker.report(campaign_id, days)

    @app.patch("/api/campaigns/{campaign_id}/streams/{stream_id}/draft")
    def update_draft(campaign_id: Id, stream_id: Id, body: OfferChange, db: Db, tracker: Keitaro):
        with mutate:
            stream = require_stream(tracker, campaign_id, stream_id)
            return change_offer(db, tracker, stream, body)

    @app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/publish")
    def publish_draft(campaign_id: Id, stream_id: Id, body: Revision, db: Db, tracker: Keitaro):
        with mutate:
            stream = require_stream(tracker, campaign_id, stream_id)
            return finish_draft(db, tracker, stream, body.version, publish=True)

    @app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/discard")
    def discard_draft(campaign_id: Id, stream_id: Id, body: Revision, db: Db, tracker: Keitaro):
        with mutate:
            stream = require_stream(tracker, campaign_id, stream_id, allow_readonly=True)
            return finish_draft(db, tracker, stream, body.version, publish=False)

    return app


app = create_app()
