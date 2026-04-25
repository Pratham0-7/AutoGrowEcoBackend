import os
import re
import json
import hmac
import hashlib
import base64
import time
from datetime import datetime, timedelta
from urllib.request import urlopen

from flask import Blueprint, request, jsonify
from bson import ObjectId

from db import (
    usersCollection,
    compCollection,
    leadCollection,
    campCollection,
    msgCollection,
    waMessagesCollection,
)
from services.meta_cloud import format_phone_meta

webhooks_bp = Blueprint("webhooks", __name__)

CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET", "")
META_STATUS_ORDER = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
}

_INBOUND_STATUSES = {"received"}


# ─────────────────────────────────────────────────────────────
# Clerk helpers
# ─────────────────────────────────────────────────────────────

def _verify_svix_signature() -> bool:
    """
    Verify the Svix webhook signature sent by Clerk.
    Docs: https://docs.svix.com/receiving/verifying-payloads/how-manual
    """
    if not CLERK_WEBHOOK_SECRET:
        print("[WEBHOOK] CLERK_WEBHOOK_SECRET not set — rejecting", flush=True)
        return False

    secret = CLERK_WEBHOOK_SECRET
    if secret.startswith("whsec_"):
        secret = secret[6:]

    try:
        secret_bytes = base64.b64decode(secret)
    except Exception:
        print("[WEBHOOK] Invalid CLERK_WEBHOOK_SECRET (bad base64)", flush=True)
        return False

    svix_id = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    if not svix_id or not svix_timestamp or not svix_signature:
        print("[WEBHOOK] Missing svix headers", flush=True)
        return False

    try:
        if abs(time.time() - int(svix_timestamp)) > 300:
            print("[WEBHOOK] Timestamp too old", flush=True)
            return False
    except ValueError:
        return False

    body = request.get_data(as_text=True)
    signed_content = f"{svix_id}.{svix_timestamp}.{body}".encode()
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    for sig in svix_signature.split(" "):
        if sig.startswith("v1,") and hmac.compare_digest(expected, sig[3:]):
            return True

    print("[WEBHOOK] Signature mismatch", flush=True)
    return False


def _cascade_delete_by_clerk_id(clerk_user_id: str) -> dict:
    user = usersCollection.find_one({"clerk_user_id": clerk_user_id})
    if not user:
        return {"skipped": True, "reason": "user not found in MongoDB"}

    company_id = user.get("company_id")
    leads_deleted = messages_deleted = campaigns_deleted = company_deleted = 0

    if company_id:
        lead_ids = [
            str(l["_id"])
            for l in leadCollection.find({"company_id": company_id}, {"_id": 1})
        ]

        if lead_ids:
            r = msgCollection.delete_many({"lead_id": {"$in": lead_ids}})
            messages_deleted = r.deleted_count

        leads_deleted = leadCollection.delete_many({"company_id": company_id}).deleted_count
        campaigns_deleted = campCollection.delete_many({"company_id": company_id}).deleted_count

        try:
            compCollection.delete_one({"_id": ObjectId(company_id)})
            company_deleted = 1
        except Exception:
            pass

    usersCollection.delete_one({"_id": user["_id"]})

    print(
        f"[WEBHOOK] Cascade delete for clerk_user_id={clerk_user_id}: "
        f"company={company_deleted}, leads={leads_deleted}, "
        f"messages={messages_deleted}, campaigns={campaigns_deleted}",
        flush=True,
    )

    return {
        "user_deleted": 1,
        "company_deleted": company_deleted,
        "leads_deleted": leads_deleted,
        "messages_deleted": messages_deleted,
        "campaigns_deleted": campaigns_deleted,
    }


# ─────────────────────────────────────────────────────────────
# Meta WhatsApp helpers
# ─────────────────────────────────────────────────────────────

def _parse_meta_timestamp(value) -> datetime:
    try:
        return datetime.utcfromtimestamp(int(value))
    except Exception:
        return datetime.utcnow()


def _normalize_phone(phone: str) -> str:
    return format_phone_meta(str(phone or "").strip())


def _extract_body_preview(message: dict) -> str:
    message_type = str(message.get("type", "")).strip().lower()

    if message_type == "text":
        return str(message.get("text", {}).get("body", "")).strip()

    if message_type == "image":
        return "[image]"

    if message_type == "document":
        return "[document]"

    if message_type == "audio":
        return "[audio]"

    if message_type == "video":
        return "[video]"

    if message_type == "sticker":
        return "[sticker]"

    if message_type == "interactive":
        interactive = message.get("interactive", {}) or {}
        interactive_type = str(interactive.get("type", "")).lower()

        if interactive_type == "button_reply":
            return str(interactive.get("button_reply", {}).get("title", "[button reply]")).strip()

        if interactive_type == "list_reply":
            return str(interactive.get("list_reply", {}).get("title", "[list reply]")).strip()

        return "[interactive reply]"

    if message_type == "button":
        return str(message.get("button", {}).get("text", "[button reply]")).strip()

    if message_type == "reaction":
        return "[reaction]"

    if message_type == "location":
        return "[location]"

    if message_type == "contacts":
        return "[contact card]"

    return "[unsupported message]"


def _resolve_company_by_meta_phone_number_id(phone_number_id: str):
    """
    Resolve company by Meta phone_number_id.

    Supports current flat fields and future nested WhatsApp config fields.
    """
    if not phone_number_id:
        return None

    pid = str(phone_number_id).strip()

    company = compCollection.find_one({
        "$or": [
            {"meta_phone_number_id": pid},
            {"whatsapp.phone_number_id": pid},
            {"whatsapp.meta_phone_number_id": pid},
        ]
    })

    if company:
        print(
            f"[META WEBHOOK] Company resolved by phone_number_id={pid} company_id={company.get('_id')}",
            flush=True,
        )
    else:
        print(
            f"[META WEBHOOK] No company found for phone_number_id={pid}",
            flush=True,
        )

    return company


def _load_company_by_company_id(company_id: str):
    try:
        return compCollection.find_one({"_id": ObjectId(str(company_id))})
    except Exception:
        return None


def _resolve_company_for_inbound(phone_number_id: str, from_phone: str):
    company = _resolve_company_by_meta_phone_number_id(phone_number_id)
    if company:
        return company

    normalized_from = _normalize_phone(from_phone)
    if not normalized_from:
        return None

    recent_message = waMessagesCollection.find_one(
        {
            "provider": "meta_cloud",
            "contact_phone": normalized_from,
        },
        sort=[("created_at", -1)],
    )

    if not recent_message:
        print(
            f"[META WEBHOOK] No fallback company for inbound contact_phone={normalized_from}",
            flush=True,
        )
        return None

    fallback_company = _load_company_by_company_id(recent_message.get("company_id", ""))

    if fallback_company:
        print(
            f"[META WEBHOOK] Fallback inbound company match via contact_phone={normalized_from} "
            f"company_id={recent_message.get('company_id')}",
            flush=True,
        )

    return fallback_company


def _resolve_company_for_status(phone_number_id: str, meta_message_id: str):
    company = _resolve_company_by_meta_phone_number_id(phone_number_id)
    if company:
        return company

    existing_message = waMessagesCollection.find_one(
        {
            "provider": "meta_cloud",
            "meta_message_id": str(meta_message_id or "").strip(),
        }
    )

    if not existing_message:
        print(
            f"[META WEBHOOK] No fallback company for status meta_message_id={meta_message_id}",
            flush=True,
        )
        return None

    fallback_company = _load_company_by_company_id(existing_message.get("company_id", ""))

    if fallback_company:
        print(
            f"[META WEBHOOK] Fallback status company match via meta_message_id={meta_message_id} "
            f"company_id={existing_message.get('company_id')}",
            flush=True,
        )

    return fallback_company


def _find_lead_by_phone(company_id: str, phone: str):
    normalized = _normalize_phone(phone)
    if not normalized:
        return None

    leads = leadCollection.find(
        {"company_id": company_id},
        {
            "name": 1,
            "phone": 1,
            "mobile": 1,
            "phone_number": 1,
            "contact_number": 1,
            "contact_phone": 1,
            "whatsapp": 1,
            "whatsapp_number": 1,
        },
    )

    for lead in leads:
        for field in (
            "phone",
            "mobile",
            "phone_number",
            "contact_number",
            "contact_phone",
            "whatsapp",
            "whatsapp_number",
        ):
            if _normalize_phone(lead.get(field, "")) == normalized:
                return lead

    return None


def _pick_contact_name(contacts: list, wa_id: str) -> str:
    for contact in contacts or []:
        if str(contact.get("wa_id", "")).strip() == str(wa_id or "").strip():
            return str(contact.get("profile", {}).get("name", "")).strip()

    if contacts:
        return str(contacts[0].get("profile", {}).get("name", "")).strip()

    return ""


def _resolve_status(current_status: str, incoming_status: str) -> str:
    current = str(current_status or "pending").strip().lower()
    incoming = str(incoming_status or "").strip().lower()

    if current in _INBOUND_STATUSES:
        return current

    if incoming == "failed":
        return current if current == "read" else "failed"

    if incoming not in META_STATUS_ORDER:
        return current

    if current == "failed":
        return incoming

    return incoming if META_STATUS_ORDER[incoming] >= META_STATUS_ORDER.get(current, 0) else current


def _handle_inbound_message(metadata: dict, contacts: list, message: dict):
    """
    Handle inbound user messages from Meta.

    Important:
    - Real messages[] payloads from webhook are lead/customer messages.
    - These must be saved as direction=inbound and status=received.
    - AGE outbound sending should only happen in send_text/send_template routes.
    """
    message_type_raw = str(message.get("type", "")).strip().lower()
    meta_message_id = str(message.get("id", "")).strip()
    raw_from_phone = str(message.get("from", "")).strip()
    from_phone = _normalize_phone(raw_from_phone)

    phone_number_id = str(metadata.get("phone_number_id", "")).strip()
    display_phone_number = str(metadata.get("display_phone_number", "")).strip()

    if message_type_raw in {"message_echo", "echo"} or message.get("message_echo"):
        print(
            f"[META WEBHOOK][INBOUND_SKIP_ECHO] id={meta_message_id} type={message_type_raw}",
            flush=True,
        )
        return

    body_preview = _extract_body_preview(message)
    received_at = _parse_meta_timestamp(message.get("timestamp"))
    contact_name = _pick_contact_name(contacts, raw_from_phone)
    to_phone = _normalize_phone(display_phone_number)

    print(
        "[META WEBHOOK][INBOUND_START] "
        f"id={meta_message_id} "
        f"type={message_type_raw} "
        f"from_raw={raw_from_phone} "
        f"from_norm={from_phone} "
        f"phone_number_id={phone_number_id} "
        f"display_phone={display_phone_number} "
        f"preview={body_preview!r}",
        flush=True,
    )

    company = _resolve_company_for_inbound(phone_number_id, from_phone)

    if not company:
        print(
            "[META WEBHOOK][INBOUND_NO_COMPANY] "
            f"id={meta_message_id} phone_number_id={phone_number_id} from_phone={from_phone}",
            flush=True,
        )
        return

    company_id = str(company["_id"])

    if not meta_message_id or not from_phone:
        print(
            "[META WEBHOOK][INBOUND_BAD_PAYLOAD] "
            f"id={meta_message_id} from_phone={from_phone}",
            flush=True,
        )
        return

    matched_lead = _find_lead_by_phone(company_id, from_phone)
    lead_id = str(matched_lead["_id"]) if matched_lead else ""
    lead_name = str(matched_lead.get("name", "")).strip() if matched_lead else contact_name
    message_type = message_type_raw or "text"

    print(
        "[META WEBHOOK][INBOUND_MATCH] "
        f"id={meta_message_id} company_id={company_id} "
        f"lead_id={lead_id or 'NONE'} lead_name={lead_name!r}",
        flush=True,
    )

    existing = waMessagesCollection.find_one({
        "provider": "meta_cloud",
        "meta_message_id": meta_message_id,
    })

    inbound_set_fields = {
        "company_id": company_id,
        "lead_id": lead_id,
        "user_id": "",
        "channel": "whatsapp",
        "direction": "inbound",
        "provider": "meta_cloud",
        "contact_phone": from_phone,
        "from_phone": from_phone,
        "to_phone": to_phone,
        "lead_name": lead_name,
        "from_name": contact_name,
        "template_name": "",
        "language_code": "",
        "variables_used": {},
        "body_preview": body_preview,
        "message_type": message_type,
        "meta_message_id": meta_message_id,
        "provider_response": {},
        "status": "received",
        "status_timestamps": {"received": received_at},
        "error_details": {},
        "last_webhook_payload": {
            "metadata": metadata,
            "contacts": contacts,
            "message": message,
        },
        "updated_at": received_at,
    }

    if existing:
        existing_direction = str(existing.get("direction", "")).strip().lower()

        if existing_direction == "inbound":
            print(
                f"[META WEBHOOK][INBOUND_DUPLICATE] id={meta_message_id} already inbound",
                flush=True,
            )
            return

        print(
            "[META WEBHOOK][INBOUND_CORRECT_EXISTING] "
            f"id={meta_message_id} old_direction={existing_direction}",
            flush=True,
        )

        waMessagesCollection.update_one(
            {"_id": existing["_id"]},
            {"$set": inbound_set_fields},
        )

    else:
        log_doc = {
            **inbound_set_fields,
            "created_at": received_at,
        }

        waMessagesCollection.insert_one(log_doc)

        print(
            "[META WEBHOOK][INBOUND_INSERTED] "
            f"id={meta_message_id} direction=inbound status=received body={body_preview!r}",
            flush=True,
        )

    if matched_lead:
        window_until = received_at + timedelta(hours=24)

        leadCollection.update_one(
            {"_id": matched_lead["_id"]},
            {
                "$set": {
                    "last_whatsapp_message_at": received_at,
                    "last_whatsapp_direction": "inbound",
                    "last_whatsapp_preview": body_preview,
                    "whatsapp_window_open_until": window_until,
                }
            },
        )

        print(
            "[META WEBHOOK][LEAD_WINDOW_OPENED] "
            f"lead_id={lead_id} until={window_until.isoformat()}",
            flush=True,
        )

    else:
        print(
            "[META WEBHOOK][INBOUND_UNMATCHED] "
            f"company_id={company_id} contact_phone={from_phone} body={body_preview!r}",
            flush=True,
        )


def _handle_status_update(metadata: dict, status: dict):
    phone_number_id = str(metadata.get("phone_number_id", "")).strip()
    meta_message_id = str(status.get("id", "")).strip()

    company = _resolve_company_for_status(phone_number_id, meta_message_id)

    if not company:
        print(
            f"[META WEBHOOK] Ignoring status for unknown phone_number_id={phone_number_id} "
            f"meta_message_id={meta_message_id}",
            flush=True,
        )
        return

    company_id = str(company["_id"])
    incoming_status = str(status.get("status", "")).strip().lower()
    event_at = _parse_meta_timestamp(status.get("timestamp"))
    contact_phone = _normalize_phone(status.get("recipient_id", ""))
    business_phone = _normalize_phone(metadata.get("display_phone_number", ""))

    if not meta_message_id:
        print("[META WEBHOOK] Status event missing message id", flush=True)
        return

    existing = waMessagesCollection.find_one({
        "company_id": company_id,
        "provider": "meta_cloud",
        "meta_message_id": meta_message_id,
    })

    if not existing:
        existing = waMessagesCollection.find_one({
            "provider": "meta_cloud",
            "meta_message_id": meta_message_id,
        })

    if existing and existing.get("direction") == "inbound":
        print(
            f"[META WEBHOOK] Skipping status update on inbound row: "
            f"meta_message_id={meta_message_id} incoming_status={incoming_status}",
            flush=True,
        )
        return

    error_details = {}

    if incoming_status == "failed" and status.get("errors"):
        first_error = status["errors"][0] if isinstance(status["errors"], list) and status["errors"] else {}
        error_details = {
            "code": first_error.get("code"),
            "title": first_error.get("title"),
            "message": first_error.get("message"),
            "details": first_error.get("error_data", {}),
        }

    if not existing:
        inbound_check = waMessagesCollection.find_one({
            "provider": "meta_cloud",
            "meta_message_id": meta_message_id,
            "direction": "inbound",
        })

        if inbound_check:
            print(
                f"[META WEBHOOK] Skipping outbound placeholder — inbound row already exists: {meta_message_id}",
                flush=True,
            )
            return

        print(
            f"[META WEBHOOK] Creating outbound placeholder for status={incoming_status} "
            f"meta_message_id={meta_message_id} contact_phone={contact_phone}",
            flush=True,
        )

        matched_lead = _find_lead_by_phone(company_id, contact_phone)

        waMessagesCollection.insert_one({
            "company_id": company_id,
            "lead_id": str(matched_lead["_id"]) if matched_lead else "",
            "user_id": "",
            "channel": "whatsapp",
            "direction": "outbound",
            "provider": "meta_cloud",
            "contact_phone": contact_phone,
            "from_phone": business_phone,
            "to_phone": contact_phone,
            "lead_name": str(matched_lead.get("name", "")).strip() if matched_lead else "",
            "from_name": "",
            "template_name": "",
            "language_code": "",
            "variables_used": {},
            "body_preview": "",
            "message_type": "template",
            "meta_message_id": meta_message_id,
            "provider_response": {},
            "status": _resolve_status("pending", incoming_status),
            "status_timestamps": {incoming_status: event_at} if incoming_status else {},
            "error_details": error_details,
            "last_webhook_payload": {
                "metadata": metadata,
                "status": status,
            },
            "created_at": event_at,
            "updated_at": event_at,
        })

        return

    current_status = str(existing.get("status", "pending")).strip().lower()
    final_status = _resolve_status(current_status, incoming_status)

    print(
        f"[META WEBHOOK] Status update: meta_message_id={meta_message_id} "
        f"{current_status} → {final_status} incoming={incoming_status}",
        flush=True,
    )

    set_fields = {
        "contact_phone": contact_phone or existing.get("contact_phone", ""),
        "to_phone": contact_phone or existing.get("to_phone", ""),
        "last_webhook_payload": {
            "metadata": metadata,
            "status": status,
        },
        "updated_at": event_at,
    }

    if existing.get("direction") != "inbound":
        set_fields["from_phone"] = business_phone or existing.get("from_phone", "")

    if final_status != current_status:
        set_fields["status"] = final_status

    if incoming_status:
        set_fields[f"status_timestamps.{incoming_status}"] = event_at

    if incoming_status == "failed" and final_status == "failed":
        set_fields["error_details"] = error_details
    elif incoming_status in {"sent", "delivered", "read"} and final_status in {"sent", "delivered", "read"}:
        set_fields["error_details"] = {}

    waMessagesCollection.update_one({"_id": existing["_id"]}, {"$set": set_fields})


# ─────────────────────────────────────────────────────────────
# Meta WhatsApp webhooks
# ─────────────────────────────────────────────────────────────

@webhooks_bp.route("/webhooks/meta/whatsapp", methods=["GET"])
@webhooks_bp.route("/meta/whatsapp", methods=["GET"])
def meta_whatsapp_webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    expected_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")

    print("[META VERIFY] args:", dict(request.args), flush=True)
    print("[META VERIFY] mode:", mode, flush=True)
    print("[META VERIFY] token:", token, flush=True)
    print("[META VERIFY] expected:", expected_token, flush=True)
    print("[META VERIFY] challenge:", challenge, flush=True)

    if token == expected_token and challenge:
        return challenge, 200, {"Content-Type": "text/plain"}

    return jsonify({"error": "Verification failed"}), 403


@webhooks_bp.route("/webhooks/meta/whatsapp", methods=["POST"])
@webhooks_bp.route("/meta/whatsapp", methods=["POST"])
def meta_whatsapp_webhook():
    payload = request.get_json(silent=True) or {}

    try:
        entry_count = len(payload.get("entry", []) or [])

        print(
            f"[META WEBHOOK] Received payload entries={entry_count}",
            flush=True,
        )

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                field = change.get("field", "")

                metadata = value.get("metadata", {}) or {}
                contacts = value.get("contacts", []) or []
                messages = value.get("messages", []) or []
                statuses = value.get("statuses", []) or []

                print(
                    "[META WEBHOOK][CHANGE] "
                    f"field={field} "
                    f"phone_number_id={metadata.get('phone_number_id', '')} "
                    f"display_phone_number={metadata.get('display_phone_number', '')} "
                    f"messages={len(messages)} "
                    f"statuses={len(statuses)}",
                    flush=True,
                )

                for message in messages:
                    print(
                        "[META WEBHOOK][DISPATCH_MESSAGE] "
                        f"id={message.get('id', '')} "
                        f"type={message.get('type', '')} "
                        f"from={message.get('from', '')}",
                        flush=True,
                    )

                    _handle_inbound_message(metadata, contacts, message)

                for status in statuses:
                    print(
                        "[META WEBHOOK][DISPATCH_STATUS] "
                        f"id={status.get('id', '')} "
                        f"status={status.get('status', '')} "
                        f"recipient={status.get('recipient_id', '')}",
                        flush=True,
                    )

                    _handle_status_update(metadata, status)

    except Exception as exc:
        print(f"[META WEBHOOK] Processing error: {exc}", flush=True)

    return jsonify({"received": True}), 200


@webhooks_bp.route("/webhooks/meta/debug/latest", methods=["GET"])
def meta_whatsapp_debug_latest():
    """
    Temporary debug endpoint.

    Usage:
    /webhooks/meta/debug/latest?phone=919569955721&company_id=...&limit=10

    Requires X-Admin-Pin header matching ADMIN_PIN.
    Remove this after debugging.
    """
    admin_pin = os.getenv("ADMIN_PIN", "")
    request_pin = request.headers.get("X-Admin-Pin", "")

    if admin_pin and request_pin != admin_pin:
        return jsonify({"error": "Unauthorized"}), 401

    phone = _normalize_phone(request.args.get("phone", ""))
    company_id = str(request.args.get("company_id", "")).strip()

    try:
        limit = int(request.args.get("limit", 10) or 10)
    except Exception:
        limit = 10

    limit = max(1, min(limit, 50))

    query = {"provider": "meta_cloud"}

    if phone:
        query["contact_phone"] = phone

    if company_id:
        query["company_id"] = company_id

    rows = list(
        waMessagesCollection.find(query)
        .sort("created_at", -1)
        .limit(limit)
    )

    result = []

    for row in rows:
        result.append({
            "_id": str(row.get("_id")),
            "company_id": row.get("company_id", ""),
            "lead_id": row.get("lead_id", ""),
            "direction": row.get("direction", ""),
            "status": row.get("status", ""),
            "contact_phone": row.get("contact_phone", ""),
            "from_phone": row.get("from_phone", ""),
            "to_phone": row.get("to_phone", ""),
            "body_preview": row.get("body_preview", ""),
            "message_type": row.get("message_type", ""),
            "meta_message_id": row.get("meta_message_id", ""),
            "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        })

    return jsonify({
        "query": query,
        "count": len(result),
        "messages": result,
    }), 200


# ─────────────────────────────────────────────────────────────
# Clerk webhook
# ─────────────────────────────────────────────────────────────

@webhooks_bp.route("/webhooks/clerk", methods=["POST"])
def clerk_webhook():
    if not _verify_svix_signature():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type")

    print(f"[WEBHOOK] Received event: {event_type}", flush=True)

    if event_type == "user.deleted":
        clerk_user_id = payload.get("data", {}).get("id")

        if not clerk_user_id:
            return jsonify({"error": "Missing user id in payload"}), 400

        result = _cascade_delete_by_clerk_id(clerk_user_id)

        return jsonify({"received": True, **result}), 200

    return jsonify({"received": True, "event": event_type}), 200


# ─────────────────────────────────────────────────────────────
# SES bounce/complaint webhook
# ─────────────────────────────────────────────────────────────

@webhooks_bp.route("/webhooks/ses", methods=["POST"])
def ses_bounce_webhook():
    try:
        payload = json.loads(request.get_data(as_text=True))
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    msg_type = payload.get("Type")

    if msg_type == "SubscriptionConfirmation":
        subscribe_url = payload.get("SubscribeURL", "")

        if subscribe_url.startswith("https://sns."):
            urlopen(subscribe_url)

        return jsonify({"received": True}), 200

    if msg_type != "Notification":
        return jsonify({"received": True}), 200

    try:
        message = json.loads(payload.get("Message", "{}"))
    except Exception:
        return jsonify({"error": "Invalid message"}), 400

    notification_type = message.get("notificationType")

    if notification_type == "Bounce":
        bounce = message.get("bounce", {})
        bounce_type = bounce.get("bounceType", "Permanent").lower()

        for recipient in bounce.get("bouncedRecipients", []):
            email = (recipient.get("emailAddress") or "").strip().lower()

            if email:
                leadCollection.update_many(
                    {"email": re.compile(f"^{re.escape(email)}$", re.IGNORECASE)},
                    {
                        "$set": {
                            "email_bounced": True,
                            "bounce_type": bounce_type,
                            "bounced_at": datetime.utcnow(),
                        }
                    },
                )

                print(f"[SES] Bounce ({bounce_type}) for {email}", flush=True)

    elif notification_type == "Complaint":
        for recipient in message.get("complaint", {}).get("complainedRecipients", []):
            email = (recipient.get("emailAddress") or "").strip().lower()

            if email:
                leadCollection.update_many(
                    {"email": re.compile(f"^{re.escape(email)}$", re.IGNORECASE)},
                    {
                        "$set": {
                            "email_bounced": True,
                            "bounce_type": "complaint",
                            "bounced_at": datetime.utcnow(),
                        }
                    },
                )

                print(f"[SES] Complaint for {email}", flush=True)

    return jsonify({"received": True}), 200