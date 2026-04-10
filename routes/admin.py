import os
import boto3
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta
from db import usersCollection, compCollection, leadCollection, campCollection, msgCollection, ensure_indexes

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ADMIN_PIN = os.getenv("ADMIN_PIN", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_pin() -> bool:
    if not ADMIN_PIN:
        return False
    return request.headers.get("X-Admin-Pin", "") == ADMIN_PIN


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def _send_admin_email(subject: str, body_html: str, body_text: str = ""):
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
        distinct_users = usersCollection.distinct("clerk_user_id")
        total_users = len(distinct_users)
        total_companies = compCollection.count_documents({})
        completed = len(usersCollection.distinct("clerk_user_id", {"onboarding_completed": True}))

        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        new_today = len(usersCollection.distinct("clerk_user_id", {"created_at": {"$gte": today_start}}))
        new_this_week = len(usersCollection.distinct("clerk_user_id", {"created_at": {"$gte": week_ago}}))

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
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        all_users = list(usersCollection.find({}).sort("created_at", -1))

        # Deduplicate by clerk_user_id — keep the most recent record (has created_at + all fields).
        seen = set()
        users = []
        for u in all_users:
            cid = u.get("clerk_user_id", str(u["_id"]))
            if cid not in seen:
                seen.add(cid)
                users.append(u)

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
                "phone": u.get("phone", ""),
                "company_name": company_name or u.get("company_name", ""),
                "company_id": u.get("company_id"),
                "sender_phone": sender_phone,
                "role": u.get("role", "admin"),
                "details_submitted": u.get("details_submitted", False),
                "approved": u.get("approved", False),
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


@admin_bp.route("/delete_user/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    """
    Cascade-delete a user and everything that belongs to them:
      user → company → leads → messages → campaigns
    """
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        user = usersCollection.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404

        company_id = user.get("company_id")
        leads_deleted = messages_deleted = campaigns_deleted = company_deleted = 0

        if company_id:
            # Collect all lead IDs for this company so we can delete their messages
            lead_ids = [str(l["_id"]) for l in leadCollection.find(
                {"company_id": company_id}, {"_id": 1}
            )]

            if lead_ids:
                msg_result = msgCollection.delete_many({"lead_id": {"$in": lead_ids}})
                messages_deleted = msg_result.deleted_count

            leads_result = leadCollection.delete_many({"company_id": company_id})
            leads_deleted = leads_result.deleted_count

            camp_result = campCollection.delete_many({"company_id": company_id})
            campaigns_deleted = camp_result.deleted_count

            compCollection.delete_one({"_id": ObjectId(company_id)})
            company_deleted = 1

        usersCollection.delete_one({"_id": ObjectId(user_id)})

        print(
            f"[ADMIN] Deleted user {user_id} ({user.get('email')}) — "
            f"company={company_deleted}, leads={leads_deleted}, "
            f"messages={messages_deleted}, campaigns={campaigns_deleted}",
            flush=True,
        )

        return jsonify({
            "message": "Account deleted",
            "user_deleted": 1,
            "company_deleted": company_deleted,
            "leads_deleted": leads_deleted,
            "messages_deleted": messages_deleted,
            "campaigns_deleted": campaigns_deleted,
        }), 200

    except Exception as exc:
        print(f"[ADMIN][DELETE ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/approve_user/<user_id>", methods=["POST"])
def approve_user(user_id):
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        user = usersCollection.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404

        usersCollection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"approved": True}}
        )
        print(f"[ADMIN] Approved user {user_id} ({user.get('email')})", flush=True)
        return jsonify({"message": "User approved", "user_id": user_id, "approved": True}), 200

    except Exception as exc:
        print(f"[ADMIN][APPROVE ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/cleanup_duplicates", methods=["POST"])
def cleanup_duplicates():
    """
    One-time cleanup: removes duplicate user and company records created by
    the old auth.py routes (which lacked created_at and proper fields).
    After cleanup, recreates unique indexes so duplicates can't happen again.

    Safe to run multiple times — idempotent.
    """
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    users_deleted = 0
    companies_deleted = 0
    report = []

    try:
        # ── 1. Deduplicate users by clerk_user_id ────────────────────────────
        # For each clerk_user_id, keep the "best" record:
        #   Priority: has created_at > has onboarding_completed=True > oldest _id
        all_users = list(usersCollection.find({}).sort("_id", 1))
        groups: dict[str, list] = {}
        for u in all_users:
            cid = u.get("clerk_user_id")
            if not cid:
                continue
            groups.setdefault(cid, []).append(u)

        for cid, dupes in groups.items():
            if len(dupes) <= 1:
                continue

            # Score each record — higher is better
            def score(u):
                return (
                    1 if u.get("created_at") else 0,
                    1 if u.get("onboarding_completed") else 0,
                    1 if u.get("plan") else 0,
                )

            dupes.sort(key=score, reverse=True)
            keep = dupes[0]
            to_delete = [d["_id"] for d in dupes[1:]]

            usersCollection.delete_many({"_id": {"$in": to_delete}})
            users_deleted += len(to_delete)
            report.append({
                "clerk_user_id": cid,
                "kept": str(keep["_id"]),
                "deleted": [str(i) for i in to_delete],
            })

        # ── 2. Deduplicate companies by name ─────────────────────────────────
        # Keep the record that has the most fields set (onboarding.py-created ones
        # have msg91 fields, created_by, sms_enabled, etc.)
        all_comps = list(compCollection.find({}).sort("_id", 1))
        comp_groups: dict[str, list] = {}
        for c in all_comps:
            name = (c.get("name") or "").strip().lower()
            if not name:
                continue
            comp_groups.setdefault(name, []).append(c)

        for name, dupes in comp_groups.items():
            if len(dupes) <= 1:
                continue

            def comp_score(c):
                return (
                    1 if c.get("created_by") else 0,
                    1 if c.get("sms_enabled") is not None else 0,
                    1 if c.get("created_at") else 0,
                )

            dupes.sort(key=comp_score, reverse=True)
            keep = dupes[0]
            to_delete_ids = [d["_id"] for d in dupes[1:]]

            # Re-point any users that reference a deleted company to the kept one
            for deleted_comp in dupes[1:]:
                usersCollection.update_many(
                    {"company_id": str(deleted_comp["_id"])},
                    {"$set": {"company_id": str(keep["_id"])}},
                )

            compCollection.delete_many({"_id": {"$in": to_delete_ids}})
            companies_deleted += len(to_delete_ids)

        # ── 3. Re-run index creation now that duplicates are gone ─────────────
        index_errors = ensure_indexes()

        return jsonify({
            "message": "Cleanup complete",
            "users_deleted": users_deleted,
            "companies_deleted": companies_deleted,
            "index_errors": index_errors,
            "detail": report,
        }), 200

    except Exception as exc:
        print(f"[ADMIN][CLEANUP ERROR] {exc}", flush=True)
        return jsonify({"error": str(exc)}), 500
