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
META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
META_STATUS_ORDER = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
}
# "received" is the terminal status for inbound messages — never replace it with outbound status codes
_INBOUND_STATUSES = {"received"}


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

    svix_id        = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    if not svix_id or not svix_timestamp or not svix_signature:
        print("[WEBHOOK] Missing svix headers", flush=True)
        return False

    # Reject payloads older than 5 minutes
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

        leads_deleted    = leadCollection.delete_many({"company_id": company_id}).deleted_count
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
        return "[interactive reply]"
    if message_type == "button":
        return "[button reply]"
    if message_type == "reaction":
        return "[reaction]"
    if message_type == "location":
        return "[location]"
    if message_type == "contacts":
        return "[contact card]"

    return "[unsupported message]"


def _resolve_company_by_meta_phone_number_id(phone_number_id: str):
    if not phone_number_id:
        return None
    return compCollection.find_one({"meta_phone_number_id": str(phone_number_id).strip()})


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
        {"name": 1, "phone": 1, "mobile": 1, "phone_number": 1, "contact_number": 1,
         "contact_phone": 1, "whatsapp": 1, "whatsapp_number": 1},
    )

    for lead in leads:
        for field in ("phone", "mobile", "phone_number", "contact_number",
                      "contact_phone", "whatsapp", "whatsapp_number"):
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

    # Inbound "received" is terminal — outbound status codes must never overwrite it
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
    # message_echo = AGE's own outbound message reflected back; skip it
    message_type_raw = str(message.get("type", "")).strip().lower()
    if message_type_raw == "message_echo":
        print(f"[META WEBHOOK] Skipping message_echo: {message.get('id', '')}", flush=True)
        return

    phone_number_id = str(metadata.get("phone_number_id", "")).strip()
    from_phone = _normalize_phone(message.get("from", ""))
    company = _resolve_company_for_inbound(phone_number_id, from_phone)
    if not company:
        print(
            f"[META WEBHOOK] Ignoring inbound message for unknown phone_number_id={phone_number_id} "
            f"from_phone={from_phone}",
            flush=True,
        )
        return

    company_id = str(company["_id"])
    meta_message_id = str(message.get("id", "")).strip()

    if not meta_message_id or not from_phone:
        print("[META WEBHOOK] Inbound message missing id or sender phone", flush=True)
        return

    received_at = _parse_meta_timestamp(message.get("timestamp"))
    contact_name = _pick_contact_name(contacts, message.get("from", ""))
    matched_lead = _find_lead_by_phone(company_id, from_phone)
    body_preview = _extract_body_preview(message)
    message_type = message_type_raw or "text"
    to_phone = _normalize_phone(metadata.get("display_phone_number", ""))

    print(
        f"[META WEBHOOK] Inbound message from={from_phone} id={meta_message_id} "
        f"type={message_type} preview={body_preview!r}",
        flush=True,
    )

    # If a status webhook arrived first it may have created an outbound placeholder.
    # Correct it to inbound instead of treating it as a duplicate.
    existing = waMessagesCollection.find_one({"provider": "meta_cloud", "meta_message_id": meta_message_id})
    if existing:
        if existing.get("direction") == "outbound":
            print(
                f"[META WEBHOOK] Correcting outbound placeholder → inbound: {meta_message_id}",
                flush=True,
            )
            waMessagesCollection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "direction": "inbound",
                    "status": "received",
                    "from_phone": from_phone,
                    "to_phone": to_phone,
                    "contact_phone": from_phone,
                    "body_preview": body_preview,
                    "message_type": message_type,
                    "lead_id": str(matched_lead["_id"]) if matched_lead else existing.get("lead_id", ""),
                    "lead_name": str(matched_lead.get("name", "")).strip() if matched_lead else contact_name,
                    "from_name": contact_name,
                    "company_id": company_id,
                    "status_timestamps": {"received": received_at},
                    "last_webhook_payload": {"metadata": metadata, "contacts": contacts, "message": message},
                    "created_at": received_at,
                    "updated_at": received_at,
                }},
            )
        else:
            print(f"[META WEBHOOK] Duplicate inbound message ignored: {meta_message_id}", flush=True)
        return

    log_doc = {
        "company_id": company_id,
        "lead_id": str(matched_lead["_id"]) if matched_lead else "",
        "user_id": "",
        "channel": "whatsapp",
        "direction": "inbound",
        "provider": "meta_cloud",
        "contact_phone": from_phone,
        "from_phone": from_phone,
        "to_phone": to_phone,
        "lead_name": str(matched_lead.get("name", "")).strip() if matched_lead else contact_name,
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
        "created_at": received_at,
        "updated_at": received_at,
    }
    waMessagesCollection.insert_one(log_doc)
    print(f"[META WEBHOOK] Inbound message saved: direction=inbound status=received id={meta_message_id}", flush=True)

    if matched_lead:
        leadCollection.update_one(
            {"_id": matched_lead["_id"]},
            {"$set": {
                "last_whatsapp_message_at": received_at,
                "last_whatsapp_direction": "inbound",
                "last_whatsapp_preview": body_preview,
                "whatsapp_window_open_until": received_at + timedelta(hours=24),
            }},
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

    # Status updates track outbound delivery. If the row is inbound, skip it entirely.
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
        # Guard: if an inbound row already exists for this id (any company), don't create an outbound placeholder
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
        f"{current_status} → {final_status} (incoming={incoming_status})",
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
    # Only update from_phone for outbound rows — inbound from_phone is the user's phone, not the business phone
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


@webhooks_bp.route("/webhooks/meta/whatsapp", methods=["GET"])
@webhooks_bp.route("/meta/whatsapp", methods=["GET"])
def meta_whatsapp_webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    expected_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN")

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
        print(f"[META WEBHOOK] Received payload with {entry_count} entr{'y' if entry_count == 1 else 'ies'}", flush=True)

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {}) or {}
                metadata = value.get("metadata", {}) or {}
                contacts = value.get("contacts", []) or []
                messages = value.get("messages", []) or []
                statuses = value.get("statuses", []) or []

                print(
                    "[META WEBHOOK] change"
                    f" phone_number_id={metadata.get('phone_number_id', '')}"
                    f" display_phone_number={metadata.get('display_phone_number', '')}"
                    f" messages={len(messages)}"
                    f" statuses={len(statuses)}",
                    flush=True,
                )

                for message in messages:
                    print(
                        f"[META WEBHOOK] Processing message id={message.get('id', '')} "
                        f"type={message.get('type', '')} from={message.get('from', '')}",
                        flush=True,
                    )
                    _handle_inbound_message(metadata, contacts, message)

                for status in statuses:
                    print(
                        f"[META WEBHOOK] Processing status id={status.get('id', '')} "
                        f"status={status.get('status', '')} recipient={status.get('recipient_id', '')}",
                        flush=True,
                    )
                    _handle_status_update(metadata, status)
    except Exception as exc:
        print(f"[META WEBHOOK] Processing error: {exc}", flush=True)

    return jsonify({"received": True}), 200


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

    # All other events — acknowledge but do nothing
    return jsonify({"received": True, "event": event_type}), 200


@webhooks_bp.route("/webhooks/ses", methods=["POST"])
def ses_bounce_webhook():
    try:
        payload = json.loads(request.get_data(as_text=True))
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    msg_type = payload.get("Type")

    # SNS requires us to hit SubscribeURL to confirm the subscription
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
                    {"$set": {
                        "email_bounced": True,
                        "bounce_type": bounce_type,
                        "bounced_at": datetime.utcnow(),
                    }},
                )
                print(f"[SES] Bounce ({bounce_type}) for {email}", flush=True)

    elif notification_type == "Complaint":
        for recipient in message.get("complaint", {}).get("complainedRecipients", []):
            email = (recipient.get("emailAddress") or "").strip().lower()
            if email:
                leadCollection.update_many(
                    {"email": re.compile(f"^{re.escape(email)}$", re.IGNORECASE)},
                    {"$set": {
                        "email_bounced": True,
                        "bounce_type": "complaint",
                        "bounced_at": datetime.utcnow(),
                    }},
                )
                print(f"[SES] Complaint for {email}", flush=True)

    return jsonify({"received": True}), 200
