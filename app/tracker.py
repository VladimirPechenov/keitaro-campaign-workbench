import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)
GOOGLE_URL = "https://www.google.com/"
METRICS = ("clicks", "conversions", "sales")


class WorkbenchError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class TrackerError(WorkbenchError):
    def __init__(self, message: str, status: int = 502, upstream_status: int | None = None):
        super().__init__(message, status)
        self.upstream_status = upstream_status


class Tracker:
    def __init__(self, client: httpx.Client, settings: Settings):
        self.client = client
        self.settings = settings

    def request(self, method, path, *, json=None, params=None):
        try:
            response = self.client.request(method, path, json=json, params=params)
        except httpx.RequestError as error:
            logger.warning("Keitaro %s %s: %s", method, path, type(error).__name__)
            raise TrackerError(
                "Не удалось дождаться ответа Keitaro. Проверьте связь и повторите."
            ) from error

        if not response.is_success:
            status = response.status_code
            logger.warning("Keitaro %s %s: HTTP %s", method, path, status)
            if status == 404:
                raise TrackerError("Объект отсутствует или недоступен в Keitaro.", 404, status)
            if status in (401, 402, 403):
                raise TrackerError(
                    "Keitaro отклонил запрос. Проверьте ключ и права Admin API.", 502, status
                )
            raise TrackerError(f"Keitaro отклонил запрос (HTTP {status}).", 502, status)

        try:
            return response.json() if response.content else None
        except ValueError as error:
            raise TrackerError("Keitaro вернул ответ в неожиданном формате.") from error

    def campaign_page(self, offset, limit):
        return self.request("GET", "/campaigns", params={"offset": offset, "limit": limit})

    def find_alias(self, alias):
        limit = 100
        offset = 0
        while True:
            page = self.campaign_page(offset, limit)
            for campaign in page:
                if campaign.get("alias") == alias:
                    return campaign
            if len(page) < limit:
                return None
            offset += limit

    def campaign(self, campaign_id):
        return self.request("GET", f"/campaigns/{campaign_id}")

    def streams(self, campaign_id):
        return self.request("GET", f"/campaigns/{campaign_id}/streams")

    def stream(self, stream_id):
        return self.request("GET", f"/streams/{stream_id}")

    def offers(self):
        return self.request("GET", "/offers")

    def offer(self, offer_id):
        return self.request("GET", f"/offers/{offer_id}")

    def catalog(self):
        domains = choices(self.request("GET", "/domains"))
        groups = choices(self.request("GET", "/groups", params={"type": "campaigns"}))
        sources = choices(self.request("GET", "/traffic_sources"))
        return {
            "domains": domains,
            "groups": groups,
            "sources": sources,
            "defaults": {
                "domain_id": default_id(domains, self.settings.domain_id),
                "group_id": default_id(groups, self.settings.group_id, "FORTESTS"),
                "traffic_source_id": default_id(
                    sources, self.settings.traffic_source_id, "FORTESTS"
                ),
            },
        }

    def create_campaign(self, payload):
        return self.request("POST", "/campaigns", json=payload)

    def create_stream(self, campaign_id, payload):
        return self.request("POST", "/streams", json={"campaign_id": campaign_id, **payload})

    def enable(self, campaign_id):
        return self.request("POST", f"/campaigns/{campaign_id}/enable")

    def set_offers(self, stream_id, offers):
        return self.request("PUT", f"/streams/{stream_id}", json={"offers": offers})

    def report(self, campaign_id, days):
        tz = self.settings.report_timezone
        start, end = report_range(days, tz)
        report = self.request(
            "POST",
            "/report/build",
            json={
                "range": {"from": str(start), "to": str(end), "timezone": tz},
                "dimensions": ["stream_id", "offer_id", "day"],
                "measures": list(METRICS),
                "filters": [
                    {"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}
                ],
            },
        )
        return report_totals(report, start, days, tz)


def choices(rows):
    return sorted(
        (
            {"id": int(row["id"]), "name": row["name"]}
            for row in rows
            if row.get("state", "active") == "active"
        ),
        key=lambda row: (row["name"].casefold(), row["id"]),
    )


def default_id(options, configured, preferred=None):
    if configured is not None:
        return configured if any(item["id"] == configured for item in options) else None
    named = [item["id"] for item in options if item["name"].upper() == preferred]
    if len(named) == 1:
        return named[0]
    return options[0]["id"] if len(options) == 1 else None


def report_range(days, timezone):
    end = datetime.now(ZoneInfo(timezone)).date()
    return end - timedelta(days=days - 1), end


def empty_stats(days):
    return {**dict.fromkeys(METRICS, 0), "trend": [0] * days}


def report_totals(report, start, days, timezone):
    streams = {}
    offers = {}
    totals = empty_stats(days)
    try:
        rows = report["rows"]
        if not isinstance(rows, list):
            raise TypeError("report rows must be a list")
        for row in rows:
            day = date.fromisoformat(row["day"])
            index = (day - start).days
            if not 0 <= index < days:
                raise ValueError("day outside requested period")
            stream_id = int(row.get("stream_id") or 0)
            offer_id = int(row.get("offer_id") or 0)
            values = {metric: int(row[metric]) for metric in METRICS}
            buckets = [totals]
            if stream_id:
                buckets.append(streams.setdefault(stream_id, empty_stats(days)))
            if stream_id and offer_id:
                buckets.append(offers.setdefault(f"{stream_id}:{offer_id}", empty_stats(days)))
            for bucket in buckets:
                for metric, value in values.items():
                    bucket[metric] += value
                bucket["trend"][index] += values["clicks"]
    except (KeyError, TypeError, ValueError) as error:
        raise TrackerError("Не удалось прочитать статистику из ответа Keitaro.") from error
    return {
        "days": [str(start + timedelta(days=offset)) for offset in range(days)],
        "timezone": timezone,
        "totals": totals,
        "streams": streams,
        "offers": offers,
    }
