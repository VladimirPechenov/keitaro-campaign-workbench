import json
from datetime import date, timedelta
from threading import Lock

import httpx
from sqlalchemy.orm import Session

from app.db import DemoState

OFFERS = [
    {"id": 101, "name": "Aurora · Основной оффер", "state": "active"},
    {"id": 102, "name": "Studio · Пробный период", "state": "active"},
    {"id": 103, "name": "Northline · Промо", "state": "active"},
    {"id": 104, "name": "Atlas · Спецпредложение", "state": "active"},
    {"id": 105, "name": "Tempo · Регистрация", "state": "active"},
]


def initial_state():
    return {
        "campaigns": {
            "2401": {
                "id": 2401,
                "name": "test_pechenov_aurora",
                "alias": "aurora",
                "state": "active",
                "domain_id": 1,
                "group_id": 1,
                "traffic_source_id": 1,
            },
        },
        "streams": {
            "4810": {
                "id": 4810,
                "name": "AU → Google",
                "type": "regular",
                "schema": "redirect",
                "campaign_id": 2401,
                "position": 1,
                "state": "active",
                "action_payload": "https://www.google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
            },
            "4811": {
                "id": 4811,
                "name": "Офферы",
                "type": "default",
                "schema": "landings",
                "campaign_id": 2401,
                "position": 2,
                "state": "active",
                "offers": [
                    {"offer_id": offer, "state": "active", "share": share}
                    for offer, share in ((101, 34), (102, 33), (103, 33))
                ],
            },
        },
    }


class DemoTracker:
    """Local fixture implementing the small part of Admin API used by the app."""

    def __init__(self, engine):
        self.engine = engine
        self.lock = Lock()

    def handle(self, request):
        path = request.url.path.removeprefix("/admin_api/v1/")
        parts = path.split("/")
        method = request.method
        body = json.loads(request.content) if request.content else None
        if method == "GET":
            catalogs = {
                "domains": [{"id": 1, "name": "tracker.example", "state": "active"}],
                "groups": [{"id": 1, "name": "FORTESTS"}],
                "traffic_sources": [{"id": 1, "name": "FORTESTS", "state": "active"}],
                "offers": OFFERS,
            }
            if path in catalogs:
                return httpx.Response(200, json=catalogs[path])
            if len(parts) == 2 and parts[0] == "offers":
                return result(
                    next((offer for offer in OFFERS if str(offer["id"]) == parts[1]), None)
                )
        if method == "POST" and path == "report/build":
            return httpx.Response(200, json=demo_report(body))

        with self.lock, Session(self.engine) as db:
            snapshot = db.get(DemoState, 1)
            data = json.loads(snapshot.data) if snapshot else initial_state()
            response = self.campaigns(method, parts, request.url.params, body, data)
            if method in {"POST", "PUT"} and response.is_success:
                db.merge(DemoState(id=1, data=json.dumps(data)))
                db.commit()
            return response

    def campaigns(self, method, parts, params, body, data):
        campaigns, streams = data["campaigns"], data["streams"]
        collection = parts[0]
        if collection == "campaigns":
            if len(parts) == 1:
                if method == "GET":
                    start, limit = int(params.get("offset", 0)), int(params.get("limit", 30))
                    page = list(reversed(campaigns.values()))[start : start + limit]
                    return result(page)
                if method == "POST":
                    if any(row["alias"] == body["alias"] for row in campaigns.values()):
                        return httpx.Response(409)
                    return insert(campaigns, body)
            campaign = campaigns.get(parts[1]) if len(parts) > 1 else None
            if campaign and len(parts) == 3:
                if method == "GET" and parts[2] == "streams":
                    return result(
                        [row for row in streams.values() if row["campaign_id"] == campaign["id"]]
                    )
                if method == "POST" and parts[2] == "enable":
                    campaign["state"] = "active"
                    return result(campaign)
            if method == "GET" and len(parts) == 2:
                return result(campaign)
        if collection == "streams":
            if method == "POST" and len(parts) == 1:
                if str(body["campaign_id"]) not in campaigns:
                    return httpx.Response(404)
                return insert(streams, {"state": "active", **body})
            if len(parts) == 2:
                stream = streams.get(parts[1])
                if method == "PUT" and stream:
                    stream.update(body)
                return result(stream)
        return httpx.Response(404)


def result(item):
    return httpx.Response(200, json=item) if item is not None else httpx.Response(404)


def insert(items, values):
    item_id = max(map(int, items)) + 1
    item = {"id": item_id, **values}
    items[str(item_id)] = item
    return result(item)


def demo_report(body):
    if body["filters"][0]["expression"] != 2401:
        return {"rows": [], "total": 0}
    start = date.fromisoformat(body["range"]["from"])
    end = date.fromisoformat(body["range"]["to"])
    rows = []
    for index in range((end - start).days + 1):
        day = start + timedelta(days=index)
        for stream_id, offer_id, base in (
            (4810, 0, 12),
            (4811, 101, 61),
            (4811, 102, 47),
            (4811, 103, 42),
        ):
            clicks = base + (day.toordinal() * offer_id + day.day * 13) % (base + 1)
            rows.append(
                {
                    "day": str(day),
                    "stream_id": stream_id,
                    "offer_id": offer_id,
                    "clicks": clicks,
                    "conversions": clicks // 13 if offer_id else 0,
                    "sales": clicks // 26 if offer_id else 0,
                }
            )
    return {"rows": rows, "total": len(rows)}
