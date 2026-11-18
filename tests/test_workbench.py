import json
from datetime import date
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import make_engine
from app.demo import DemoTracker
from app.main import create_app
from app.tracker import report_totals

DEMO_CAMPAIGN = "/api/campaigns/2401"
DEMO_EDITOR = f"{DEMO_CAMPAIGN}/streams/4811"


@pytest.fixture
def demo_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    monkeypatch.setenv("CAMPAIGN_PREFIX", "test_pechenov_")
    command.upgrade(Config("alembic.ini"), "head")
    return Settings(_env_file=None)


@pytest.fixture
def client(demo_settings):
    with TestClient(create_app(demo_settings)) as test_client:
        yield test_client


def offer_flow(client):
    return client.get(DEMO_CAMPAIGN).json()["streams"][1]


def change(client, current, action, offer_id, **fields):
    return client.patch(
        f"{DEMO_EDITOR}/draft",
        json={"version": current["version"], "action": action, "offer_id": offer_id, **fields},
    )


def shares(stream):
    return {row["offer_id"]: row["share"] for row in stream["offers"] if not row["removed"]}


def test_creation_retries_after_tracker_writes_but_times_out(demo_settings):
    engine = make_engine(demo_settings.db_url)
    backend = DemoTracker(engine)
    failed = False

    def lose_one_response(request):
        nonlocal failed
        response = backend.handle(request)
        if (
            not failed
            and request.method == "POST"
            and request.url.path.endswith("/streams")
            and json.loads(request.content)["type"] == "default"
        ):
            failed = True
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path.endswith("/enable"):
            return httpx.Response(response.status_code, json=[])
        return response

    try:
        app = create_app(demo_settings, httpx.MockTransport(lose_one_response))
        with TestClient(app) as client:
            request = {"name": "qa", "country": "au", "offer_id": 105, "request_id": str(uuid4())}
            failed_response = client.post("/api/campaigns", json=request)
            assert failed_response.status_code == 502
            assert "не подтверждена" in failed_response.json()["detail"]
            retry = client.post("/api/campaigns", json=request)
            assert retry.status_code == 201, retry.json()
            assert retry.json() == client.post("/api/campaigns", json=request).json()
            assert (
                client.post("/api/campaigns", json={**request, "country": "GB"}).status_code == 409
            )
            campaign = client.get(f"/api/campaigns/{retry.json()['id']}").json()
            assert campaign["name"] == "test_pechenov_qa"
            assert campaign["state"] == "active"
            assert [
                campaign[field] for field in ("domain_id", "group_id", "traffic_source_id")
            ] == [1, 1, 1]
            redirect, offers = campaign["streams"]
            assert (redirect["type"], redirect["position"], redirect["redirect_url"]) == (
                "regular",
                1,
                "https://www.google.com/",
            )
            assert redirect["country_filters"] == [{"mode": "accept", "payload": ["AU"]}]
            assert (offers["type"], offers["position"], shares(offers)) == (
                "default",
                2,
                {105: 100},
            )
            assert len(client.get("/api/campaigns").json()["items"]) == 2
    finally:
        engine.dispose()


def test_invalid_country_and_offer_create_nothing(client):
    assert (
        client.post(
            "/api/campaigns", json={"name": "x", "country": "ZZ", "offer_id": 101}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/campaigns", json={"name": "x", "country": "AU", "offer_id": 999}
        ).status_code
        == 422
    )
    assert len(client.get("/api/campaigns").json()["items"]) == 1


def test_draft_rebalance_pin_remove_restore_and_publish(client):
    before = offer_flow(client)
    added = change(client, before, "add", 104).json()
    assert shares(added) == {101: 25, 102: 25, 103: 25, 104: 25}
    assert added["published_offers"] == before["offers"]
    assert change(client, before, "remove", 102).status_code == 409

    locked = change(client, added, "share", 101, share=25).json()
    removed = change(client, locked, "remove", 103).json()
    assert shares(removed) == {101: 25, 102: 38, 104: 37}
    assert offer_flow(client) == removed
    restored = change(client, removed, "restore", 103).json()
    assert shares(restored) == shares(locked)
    assert [item["offer_id"] for item in client.app.state.tracker.stream(4811)["offers"]] == [
        101,
        102,
        103,
    ]
    removed = change(client, restored, "remove", 103).json()
    published = client.post(f"{DEMO_EDITOR}/publish", json={"version": removed["version"]}).json()
    assert published["staged"] is False
    assert published["published_offers"] == []
    assert published["offers"][0]["pinned"] is True
    assert shares(published) == shares(removed)
    assert {
        item["offer_id"]: item["share"] for item in client.app.state.tracker.stream(4811)["offers"]
    } == shares(published)
    archived = next(item for item in offer_flow(client)["offers"] if item["offer_id"] == 103)
    assert archived["removed"] and archived["archived"] and archived["share"] == 0
    recovered = change(client, offer_flow(client), "restore", 103).json()
    assert shares(recovered) == {101: 25, 102: 25, 103: 25, 104: 25}
    assert client.post(f"{DEMO_EDITOR}/publish", json={"version": recovered["version"]}).is_success
    assert shares(offer_flow(client)) == shares(recovered)


def test_refresh_archives_offer_removed_directly_in_keitaro(client):
    assert shares(offer_flow(client)) == {101: 34, 102: 33, 103: 33}
    tracker = client.app.state.tracker
    tracker.set_offers(
        4811,
        [
            {"offer_id": 101, "share": 50, "state": "active"},
            {"offer_id": 102, "share": 50, "state": "active"},
        ],
    )

    refreshed = offer_flow(client)
    assert not refreshed["staged"]
    assert refreshed["offers"][-1]["offer_id"] == 103
    assert refreshed["offers"][-1]["archived"]
    draft = change(client, refreshed, "restore", 103).json()
    assert draft["staged"] and shares(draft) == {101: 34, 102: 33, 103: 33}
    published = client.post(f"{DEMO_EDITOR}/publish", json={"version": draft["version"]}).json()
    assert shares(published) == shares(draft)


def test_conflict_preserves_remote_changes_and_can_be_discarded(client):
    draft = change(client, offer_flow(client), "add", 104).json()
    client.app.state.tracker.set_offers(4811, [{"offer_id": 105, "share": 100, "state": "active"}])
    assert offer_flow(client)["conflict"] is True
    body = {"version": draft["version"]}
    assert client.post(f"{DEMO_EDITOR}/publish", json=body).status_code == 409
    discarded = client.post(f"{DEMO_EDITOR}/discard", json=body).json()
    assert discarded["staged"] is False
    assert shares(discarded) == {105: 100}


def test_report_groups_each_offer_by_stream_and_counts_redirect_clicks():
    start = date(2026, 11, 17)
    report = report_totals(
        {
            "rows": [
                {
                    "day": str(start),
                    "stream_id": 1,
                    "offer_id": 0,
                    "clicks": 10,
                    "conversions": 0,
                    "sales": 0,
                },
                {
                    "day": str(start),
                    "stream_id": 2,
                    "offer_id": 101,
                    "clicks": 50,
                    "conversions": 5,
                    "sales": 2,
                },
                {
                    "day": "2026-11-18",
                    "stream_id": 3,
                    "offer_id": 101,
                    "clicks": 4,
                    "conversions": 1,
                    "sales": 1,
                },
            ]
        },
        start,
        2,
        "Europe/Moscow",
    )
    assert report["totals"] == {"clicks": 64, "conversions": 6, "sales": 3, "trend": [60, 4]}
    assert report["streams"][1]["clicks"] == 10
    assert report["offers"]["2:101"]["trend"] == [50, 0]
    assert report["offers"]["3:101"]["trend"] == [0, 4]


def test_report_period_is_parsed_from_query(client):
    report = client.get(f"{DEMO_CAMPAIGN}/report", params={"days": 30})
    assert report.status_code == 200
    assert len(report.json()["days"]) == 30
    assert report.json()["totals"]["clicks"] > 0
    assert client.get(f"{DEMO_CAMPAIGN}/report", params={"days": 3}).status_code == 422
