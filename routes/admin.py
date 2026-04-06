import os
import boto3
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta
from db import usersCollection, compCollection, leadCollection, campCollection

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ADMIN_PIN = os.getenv("ADMIN_PIN", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_pin() -> bool:
    """Return True if the request carries the correct admin PIN."""
    if not ADMIN_PIN:
        return False
    return request.headers.get("X-Admin-Pin", "") == ADMIN_PIN


# ---------------------------------------------------------------------------
# Email helper (re-uses the project's SES setup)
# ---------------------------------------------------------------------------

def _send_admin_email(subject: str, body_html: str, body_text: str = ""):
    """Send a notification email to ADMIN_EMAIL via SES."""
    if not ADMIN_EMAIL:
        return
    try:
        client = boto3.client("ses", region_name=AWS_REGION)
        client.send_email(
            Source=ADMIN_EMAIL,
            Destination={"ToAddresses": [ADMIN_EMAIL]},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Html": {"Data": body_html},
                    "Text": {"Data": body_text or subject},
                },
            },
        )
        print(f"[ADMIN] Notification email sent: {subject}", flush=True)
    except ClientError as exc:
        print(f"[ADMIN] SES error: {exc}", flush=True)


def notify_new_registration(name: str, email: str, created_at: datetime):
    """Fire-and-forget admin notification for a fresh signup."""
    subject = f"New AGE Signup — {name or email}"
    ts = created_at.strftime("%d %b %Y, %H:%M UTC")
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#0a0a0a;color:white;padding:24px;">
      <div style="max-width:500px;margin:0 auto;background:#111;padding:24px;border-radius:12px;border:1px solid #222;">
        <h2 style="margin-top:0;">New Registration on AGE</h2>
        <p><strong>Name:</strong> {name or '—'}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Time:</strong> {ts}</p>
        <p style="color:#64748b;font-size:12px;">Go to your admin dashboard to see all registrations.</p>
      </div>
    </body></html>
    """
    _send_admin_email(subject, body_html)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/ping", methods=["GET"])
def ping():
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"status": "ok"}), 200


@admin_bp.route("/stats", methods=["GET"])
def stats():
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        total_users = usersCollection.count_documents({})
        total_companies = compCollection.count_documents({})
        completed = usersCollection.count_documents({"onboarding_completed": True})

        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        new_today = usersCollection.count_documents({"created_at": {"$gte": today_start}})
        new_this_week = usersCollection.count_documents({"created_at": {"$gte": week_ago}})

        return jsonify({
            "total_users": total_users,
            "total_companies": total_companies,
            "onboarding_completed": completed,
            "new_today": new_today,
            "new_this_week": new_this_week,
        }), 200

    except Exception as exc:
        print(f"[ADMIN][STATS ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/users", methods=["GET"])
def list_users():
    """Return all registered users with their company info."""
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        users = list(usersCollection.find({}).sort("created_at", -1))
        result = []

        for u in users:
            company_name = ""
            sender_phone = ""
            msg91_configured = False
            lead_count = 0

            if u.get("company_id"):
                try:
                    company = compCollection.find_one({"_id": ObjectId(u["company_id"])})
                    if company:
                        company_name = company.get("name", "")
                        sender_phone = company.get("sender_phone", "")
                        msg91_configured = bool(company.get("msg91_api_key") or company.get("msg91_entity_id"))
                        lead_count = leadCollection.count_documents({"company_id": u["company_id"]})
                except Exception:
                    pass

            result.append({
                "user_id": str(u["_id"]),
                "clerk_user_id": u.get("clerk_user_id", ""),
                "name": u.get("name", ""),
                "email": u.get("email", ""),
                "company_name": company_name,
                "company_id": u.get("company_id"),
                "sender_phone": sender_phone,
                "role": u.get("role", "admin"),
                "onboarding_completed": u.get("onboarding_completed", False),
                "msg91_configured": msg91_configured,
                "lead_count": lead_count,
                "plan": u.get("plan", "free"),
                "created_at": u["created_at"].isoformat() if u.get("created_at") else "",
            })

        return jsonify({"users": result, "total": len(result)}), 200

    except Exception as exc:
        print(f"[ADMIN][USERS ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/notifications/check", methods=["GET"])
def check_notifications():
    """
    Return users created after `since` (ISO timestamp query param).
    Frontend polls this every 30s to drive the notification bell.
    """
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        since_str = request.args.get("since")
        if since_str:
            since_dt = datetime.fromisoformat(since_str.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            since_dt = datetime.utcnow() - timedelta(minutes=5)

        new_users = list(
            usersCollection.find({"created_at": {"$gt": since_dt}}).sort("created_at", -1)
        )

        result = []
        for u in new_users:
            result.append({
                "user_id": str(u["_id"]),
                "name": u.get("name", ""),
                "email": u.get("email", ""),
                "created_at": u["created_at"].isoformat() if u.get("created_at") else "",
            })

        return jsonify({"new_users": result, "count": len(result)}), 200

    except Exception as exc:
        print(f"[ADMIN][NOTIF ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500
