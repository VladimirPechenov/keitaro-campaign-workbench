import re

from sqlalchemy.orm import Session

from app.db import CampaignCreation
from app.schemas import CreateCampaign
from app.tracker import GOOGLE_URL, Tracker, TrackerError, WorkbenchError


def campaign_summary(campaign):
    return {
        "id": campaign["id"],
        "name": campaign["name"],
        "alias": campaign.get("alias"),
        "state": campaign.get("state"),
    }


def flow_payloads(country, offer_id):
    return [
        {
            "name": f"{country} → Google",
            "type": "regular",
            "position": 1,
            "schema": "redirect",
            "action_type": "http",
            "action_payload": GOOGLE_URL,
            "collect_clicks": True,
            "filters": [{"name": "country", "mode": "accept", "payload": [country]}],
        },
        {
            "name": "Офферы",
            "type": "default",
            "position": 2,
            "schema": "landings",
            "action_type": "do_nothing",
            "collect_clicks": True,
            "offers": [{"offer_id": offer_id, "state": "active", "share": 100}],
        },
    ]


def resolve_catalog(data, tracker):
    catalog = tracker.catalog()
    selections = {}
    for field, key, label in (
        ("domain_id", "domains", "домен"),
        ("group_id", "groups", "группу"),
        ("traffic_source_id", "sources", "источник"),
    ):
        value = getattr(data, field) or catalog["defaults"][field]
        if not any(option["id"] == value for option in catalog[key]):
            raise WorkbenchError(f"Выберите доступный {label} из Keitaro.")
        selections[field] = value
    return selections


def require_active_offer(tracker, offer_id):
    try:
        offer = tracker.offer(offer_id)
    except TrackerError as error:
        if error.upstream_status != 404:
            raise
        raise WorkbenchError(f"Оффер #{offer_id} недоступен в Keitaro.") from error
    if offer.get("state", "active") != "active":
        raise WorkbenchError(f"Оффер #{offer_id} неактивен в Keitaro.")


def create_campaign(db: Session, tracker: Tracker, data: CreateCampaign):
    request_id = str(data.request_id)
    submitted = data.model_dump(mode="json", exclude={"request_id"})
    operation = db.get(CampaignCreation, request_id)
    if operation is not None and operation.payload["input"] != submitted:
        raise WorkbenchError("Этот запрос уже использован с другими параметрами.", 409)
    if operation is not None and operation.complete:
        return campaign_summary(tracker.campaign(operation.remote_id))

    previous_attempt = operation is not None
    if not previous_attempt:
        require_active_offer(tracker, data.offer_id)
        selections = resolve_catalog(data, tracker)
        prefix = tracker.settings.campaign_prefix
        suffix = re.sub(r"[^a-z0-9]+", "-", data.name.lower().removeprefix(prefix)).strip("-")
        campaign = {
            "name": data.name if data.name.startswith(prefix) else prefix + data.name,
            "alias": f"{prefix}{suffix[:24] or 'campaign'}-{data.request_id.hex[:16]}",
            "type": "position",
            "state": "disabled",
            **selections,
        }
        operation = CampaignCreation(
            request_id=request_id,
            payload={"input": submitted, "campaign": campaign},
            complete=False,
        )
        db.add(operation)
        db.commit()

    campaign = operation.payload["campaign"]
    try:
        if operation.remote_id is None:
            remote = tracker.find_alias(campaign["alias"]) if previous_attempt else None
            if remote is None:
                remote = tracker.create_campaign(campaign)
            operation.remote_id = remote["id"]
            db.commit()
        else:
            remote = tracker.campaign(operation.remote_id)

        remote_id = operation.remote_id
        flows = flow_payloads(data.country, data.offer_id)
        existing = existing_positions(tracker.streams(remote_id), flows)
        if remote.get("state") == "active" and len(existing) != len(flows):
            raise WorkbenchError("Структура тестовой кампании изменилась в Keitaro.", 409)
        for payload in flows:
            if payload["position"] not in existing:
                tracker.create_stream(remote_id, payload)
        if len(existing) != len(flows):
            confirmed = existing_positions(tracker.streams(remote_id), flows)
            if len(confirmed) != len(flows):
                raise TrackerError("Keitaro не подтвердил оба потока.")
        if remote.get("state") != "active":
            tracker.enable(remote_id)
            remote = tracker.campaign(remote_id)
        if remote.get("state") != "active":
            raise TrackerError("Keitaro не подтвердил включение кампании.")
        result = campaign_summary(remote)
        operation.complete = True
        db.commit()
        return result
    except TrackerError as error:
        if operation.remote_id is None:
            raise
        raise TrackerError(
            f"Настройка кампании #{operation.remote_id} не подтверждена. "
            "Повторите создание с теми же параметрами, чтобы продолжить.",
            error.status,
            error.upstream_status,
        ) from error


def existing_positions(streams, payloads):
    expected = {payload["position"]: payload for payload in payloads}
    positions = set()
    for stream in streams:
        position = stream.get("position")
        payload = expected.get(position)
        if not payload or position in positions or not matches_flow(stream, payload):
            raise WorkbenchError("Конфигурация потоков не совпадает с запросом на создание.", 409)
        positions.add(position)
    return positions


def matches_flow(stream, payload):
    if any(stream.get(key) != payload[key] for key in ("name", "type", "schema")):
        return False
    if not stream.get("collect_clicks", True) or stream.get("state", "active") != "active":
        return False
    filters = [
        {key: item.get(key) for key in ("name", "mode", "payload")}
        for item in stream.get("filters") or []
    ]
    if filters != payload.get("filters", []):
        return False
    if payload["schema"] == "redirect":
        return stream.get("action_payload") == payload["action_payload"]
    offers = [
        {key: item.get(key) for key in ("offer_id", "state", "share")}
        for item in stream.get("offers") or []
    ]
    return offers == payload["offers"]
