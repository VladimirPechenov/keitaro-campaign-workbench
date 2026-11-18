import hashlib
import json
from uuid import uuid4

from sqlalchemy.orm import Session

from app.campaigns import require_active_offer
from app.db import StreamDraft
from app.schemas import OfferChange, OfferRow
from app.tracker import WorkbenchError


def active(item):
    return item.state == "active" and not item.removed


def rebalance(items):
    enabled = [item for item in items if active(item)]
    if not enabled:
        return
    locked = sum(item.share for item in enabled if item.pinned)
    free = [item for item in enabled if not item.pinned]
    remaining = 100 - locked
    if remaining < 0 or (remaining and not free):
        raise WorkbenchError(
            "Снимите закрепление с одной или нескольких долей, чтобы получить 100%."
        )
    if free:
        share, extra = divmod(remaining, len(free))
        for index, item in enumerate(free):
            item.share = share + (index < extra)


def remote_offers(stream, pins=()):
    return [
        OfferRow(
            offer_id=item["offer_id"],
            share=int(item["share"]),
            state=item.get("state", "active"),
            pinned=int(item["offer_id"]) in pins,
        )
        for item in stream.get("offers") or []
    ]


def offer_signature(items):
    return sorted((item.offer_id, item.state, item.share) for item in items if not item.removed)


def stream_version(stream, offers):
    content = [stream["id"], stream.get("schema"), stream.get("state"), offer_signature(offers)]
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


def view_stream(stream, draft):
    published = remote_offers(stream, draft.published_pins if draft else ())
    stream_token = stream_version(stream, published)
    staged = draft is not None and draft.items is not None
    offers = [OfferRow.model_validate(item) for item in draft.items] if staged else published
    changed = staged and draft.base_version != stream_token
    return {
        "id": stream["id"],
        "name": stream["name"],
        "type": stream.get("type"),
        "schema": stream.get("schema"),
        "position": stream.get("position"),
        "state": stream.get("state"),
        "country_filters": [
            {"mode": item.get("mode"), "payload": item.get("payload")}
            for item in stream.get("filters") or []
            if item.get("name") == "country"
        ],
        "redirect_url": stream.get("action_payload")
        if stream.get("schema") == "redirect"
        else None,
        "editable": editable(stream),
        "staged": staged,
        "conflict": changed and offer_signature(offers) != offer_signature(published),
        "version": draft.revision
        if staged
        else f"{stream_token}:{draft.revision if draft else ''}",
        "offers": [item.model_dump() for item in offers],
    }


def editable(stream):
    return stream.get("schema") == "landings" and stream.get("state") != "deleted"


def require_stream(tracker, campaign_id, stream_id, *, allow_readonly=False):
    stream = tracker.stream(stream_id)
    if int(stream["campaign_id"]) != campaign_id:
        raise WorkbenchError("Поток не принадлежит выбранной кампании.", 404)
    if not allow_readonly and not editable(stream):
        raise WorkbenchError("Этот поток использует другую схему и не редактируется здесь.", 422)
    return stream


def require_version(view, version):
    if version != view["version"]:
        raise WorkbenchError("Версия черновика изменилась. Обновите кампанию.", 409)


def change_offer(db: Session, tracker, stream, change: OfferChange):
    draft = db.get(StreamDraft, stream["id"])
    view = view_stream(stream, draft)
    require_version(view, change.version)
    if view["conflict"]:
        raise WorkbenchError(
            "Офферы изменились в Keitaro. Отмените черновик и обновите данные.", 409
        )
    items = [OfferRow.model_validate(item) for item in view["offers"]]
    item = next((item for item in items if item.offer_id == change.offer_id), None)

    if change.action == "add":
        if item is not None:
            raise WorkbenchError(
                "Оффер уже есть в этом потоке. Удалённый оффер можно вернуть.", 409
            )
        require_active_offer(tracker, change.offer_id)
        items.append(OfferRow(offer_id=change.offer_id, share=0))
    elif item is None:
        raise WorkbenchError("Оффер отсутствует в этом потоке.", 404)
    elif change.action == "remove":
        item.removed = True
    elif change.action == "restore":
        item.removed = False
    else:
        if not active(item):
            raise WorkbenchError("Менять долю можно только у активного оффера.")
        if change.action == "share":
            if change.share is None:
                raise WorkbenchError("Укажите долю от 0 до 100%.")
            item.share = change.share
        item.pinned = change.action != "unpin"

    rebalance(items)
    if draft is None:
        draft = StreamDraft(stream_id=stream["id"], published_pins=[])
        db.add(draft)
    if not view["staged"]:
        draft.base_version = stream_version(stream, remote_offers(stream))
    draft.items = [item.model_dump() for item in items]
    draft.revision = str(uuid4())
    db.commit()
    return view_stream(stream, draft)


def finish_draft(db: Session, tracker, stream, version, publish):
    draft = db.get(StreamDraft, stream["id"])
    view = view_stream(stream, draft)
    require_version(view, version)
    if not view["staged"]:
        return view
    if publish:
        if view["conflict"]:
            raise WorkbenchError(
                "Офферы изменились в Keitaro. Отмените черновик и обновите данные.", 409
            )
        desired = [OfferRow.model_validate(item) for item in draft.items if not item["removed"]]
        if offer_signature(desired) != offer_signature(remote_offers(stream)):
            links = {int(link["offer_id"]): link for link in stream.get("offers") or []}
            payload = []
            for item in desired:
                link = links.get(item.offer_id, {})
                fields = item.model_dump(include={"offer_id", "state", "share"})
                if link.get("id"):
                    fields["id"] = link["id"]
                payload.append(fields)
            tracker.set_offers(stream["id"], payload)
            stream = tracker.stream(stream["id"])
            if offer_signature(desired) != offer_signature(remote_offers(stream)):
                raise WorkbenchError("Keitaro вернул другой состав потока. Черновик сохранён.", 502)
        draft.published_pins = [item.offer_id for item in desired if item.pinned]
    draft.items = None
    draft.base_version = None
    draft.revision = str(uuid4())
    db.commit()
    return view_stream(stream, draft)
