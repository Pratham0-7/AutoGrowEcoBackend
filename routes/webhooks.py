import os
import re
import json
import hmac
import hashlib
import base64
import time
from datetime import datetime
from urllib.request import urlopen

from flask import Blueprint, request, jsonify
from bson import ObjectId

from db import usersCollection, compCollection, leadCollection, campCollection, msgCollection

webhooks_bp = Blueprint("webhooks", __name__)

CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET", "")


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
