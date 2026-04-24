import os
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from db import leadCollection, msgCollection, compCollection, waMessagesCollection, usersCollection
from services.whatsapp import send_whatsapp_text, send_whatsapp_template, format_phone_wa
from services.meta_cloud import (
    send_meta_template,
    build_components,
    format_phone_meta,
    PREBUILT_TEMPLATES,
)

_PLATFORM_META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
_PLATFORM_META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")

_PLATFORM_WA_KEY = os.getenv("MSG91_WHATSAPP_AUTH_KEY") or os.getenv("MSG91_AUTH_KEY", "")

whatsapp_bp = Blueprint("whatsapp", __name__)


def _build_meta_log_doc(
    *,
    company_id: str,
    lead_id: str,
    user_id: str,
    phone: str,
    lead_name: str,
    template_name: str,
    language_code: str,
    variables_used: dict,
    body_preview: str,
    now: datetime,
    direction: str = "outbound",
    message_type: str = "template",
    from_phone: str = "",
    from_name: str = "",
    meta_message_id: str | None = None,
    status: str = "pending",
):
    contact_phone = format_phone_meta(phone)

    return {
        "company_id": company_id,
        "lead_id": lead_id,
        "user_id": user_id,
        "channel": "whatsapp",
        "direction": direction,
        "provider": "meta_cloud",
        "contact_phone": contact_phone,
        "from_phone": from_phone,
        "to_phone": contact_phone if direction == "outbound" else "",
        "lead_name": lead_name,
        "from_name": from_name,
        "template_name": template_name,
        "language_code": language_code,
        "variables_used": variables_used or {},
        "body_preview": body_preview,
        "message_type": message_type,
        "meta_message_id": meta_message_id,
        "provider_response": {},
        "status": status,
        "status_timestamps": {status: now},
        "error_details": {},
        "last_webhook_payload": {},
        "created_at": now,
        "updated_at": now,
    }


@whatsapp_bp.route("/whatsapp/config/<company_id>", methods=["GET"])
def get_wa_config(company_id):
    try:
        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        return jsonify({
            "wa_number": company.get("wa_number", ""),
            "wa_template_name": company.get("wa_template_name", ""),
            "wa_enabled": company.get("wa_enabled", False),
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/config/<company_id>", methods=["POST"])
def save_wa_config(company_id):
    try:
        data = request.json or {}
        update = {}

        if "wa_number" in data:
            update["wa_number"] = str(data["wa_number"]).strip()

        if "wa_template_name" in data:
            update["wa_template_name"] = str(data["wa_template_name"]).strip()

        if "wa_enabled" in data:
            update["wa_enabled"] = bool(data["wa_enabled"])

        if not update:
            return jsonify({"error": "No fields to update"}), 400

        compCollection.update_one({"_id": ObjectId(company_id)}, {"$set": update})
        return jsonify({"message": "WhatsApp config saved"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/send_manual", methods=["POST"])
def send_manual():
    try:
        data = request.json or {}

        company_id = str(data.get("company_id", "")).strip()
        phone = str(data.get("phone", "")).strip()
        message = str(data.get("message", "")).strip()
        lead_id = str(data.get("lead_id", "")).strip()
        lead_name = str(data.get("lead_name", "")).strip()

        if not company_id or not phone or not message:
            return jsonify({"error": "company_id, phone, and message are required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        auth_key = _PLATFORM_WA_KEY or None
        integrated_number = str(company.get("wa_number", "")).strip()
        template_name = str(company.get("wa_template_name", "")).strip()

        print("[WA SEND MANUAL] company_id:", company_id, flush=True)
        print("[WA SEND MANUAL] platform key exists:", bool(_PLATFORM_WA_KEY), flush=True)
        print("[WA SEND MANUAL] final auth key exists:", bool(auth_key), flush=True)
        print(
            "[WA SEND MANUAL] auth key preview:",
            f"{auth_key[:6]}...{auth_key[-4:]}" if auth_key else "None",
            flush=True
        )
        print("[WA SEND MANUAL] integrated_number:", integrated_number, flush=True)
        print("[WA SEND MANUAL] template_name:", template_name, flush=True)

        if template_name:
            result = send_whatsapp_template(
                phone=phone,
                template_name=template_name,
                template_params={"name": lead_name},
                integrated_number=integrated_number,
                auth_key=auth_key,
            )
        else:
            result = send_whatsapp_text(
                phone=phone,
                message=message,
                integrated_number=integrated_number,
                auth_key=auth_key,
            )

        print("[WA SEND MANUAL] result:", result, flush=True)

        if not result.get("ok"):
            return jsonify({
                "error": result.get("message", "WhatsApp send failed"),
                "provider_response": result.get("provider_response", {})
            }), 400

        msgCollection.insert_one({
            "lead_id": lead_id,
            "lead_name": lead_name,
            "company_id": company_id,
            "channel": "whatsapp",
            "phone": format_phone_wa(phone),
            "message": message,
            "sent_at": datetime.utcnow(),
            "status": "sent",
            "message_type": "manual",
            "provider_response": result.get("provider_response", {}),
        })

        return jsonify({
            "message": "WhatsApp sent successfully",
            "provider_response": result.get("provider_response", {})
        }), 200

    except Exception as e:
        print("[WA SEND MANUAL] Exception:", e, flush=True)
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/send_bulk/<company_id>", methods=["POST"])
def send_bulk_whatsapp(company_id):
    try:
        data = request.json or {}
        message = str(data.get("message", "")).strip()

        if not message:
            return jsonify({"error": "Message is required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        integrated_number = str(company.get("wa_number", "")).strip()
        if not integrated_number:
            return jsonify({"error": "WhatsApp integrated number not configured"}), 400

        auth_key = _PLATFORM_WA_KEY or None
        template_name = str(company.get("wa_template_name", "")).strip()

        leads = list(leadCollection.find({
            "company_id": company_id,
            "is_individual_followup": {"$ne": True},
        }))

        def get_phone(lead):
            return str(
                lead.get("phone")
                or lead.get("mobile")
                or lead.get("phone_number")
                or lead.get("contact_number")
                or ""
            ).strip()

        leads_with_phone = [l for l in leads if get_phone(l)]
        if not leads_with_phone:
            return jsonify({"error": "No leads with phone numbers found"}), 400

        now = datetime.utcnow()
        count = 0
        failed = []

        for lead in leads_with_phone:
            lead_id_str = str(lead["_id"])
            lead_name = str(lead.get("name", "")).strip()
            phone = get_phone(lead)

            personal_msg = (
                message
                .replace("{{name}}", lead_name)
                .replace("{{their_company}}", str(lead.get("company", "")).strip())
            )

            try:
                if template_name:
                    result = send_whatsapp_template(
                        phone=phone,
                        template_name=template_name,
                        template_params={"name": lead_name},
                        integrated_number=integrated_number,
                        auth_key=auth_key,
                    )
                else:
                    result = send_whatsapp_text(
                        phone=phone,
                        message=personal_msg,
                        integrated_number=integrated_number,
                        auth_key=auth_key,
                    )

                status = "sent" if result.get("ok") else "error"

                msgCollection.insert_one({
                    "lead_id": lead_id_str,
                    "lead_name": lead_name,
                    "company_id": company_id,
                    "channel": "whatsapp",
                    "phone": format_phone_wa(phone),
                    "message": personal_msg,
                    "sent_at": now,
                    "status": status,
                    "message_type": "bulk",
                    "provider_response": result.get("provider_response", {}),
                })

                if result.get("ok"):
                    count += 1
                else:
                    failed.append({
                        "lead_id": lead_id_str,
                        "name": lead_name,
                        "reason": result.get("message", "Failed"),
                        "provider_response": result.get("provider_response", {}),
                    })

            except Exception as e:
                failed.append({
                    "lead_id": lead_id_str,
                    "name": lead_name,
                    "reason": str(e),
                })

        return jsonify({
            "message": f"{count} WhatsApp message{'s' if count != 1 else ''} sent",
            "failed": failed,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/messages/<company_id>", methods=["GET"])
def get_wa_messages(company_id):
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        skip = (page - 1) * per_page

        query = {"company_id": company_id, "channel": "whatsapp"}
        messages = list(
            msgCollection.find(query)
            .sort("sent_at", -1)
            .skip(skip)
            .limit(per_page)
        )
        total = msgCollection.count_documents(query)

        result = []
        for msg in messages:
            result.append({
                "id": str(msg["_id"]),
                "lead_id": msg.get("lead_id", ""),
                "lead_name": msg.get("lead_name", "Unknown"),
                "phone": msg.get("phone", ""),
                "message": msg.get("message", ""),
                "status": msg.get("status", ""),
                "message_type": msg.get("message_type", ""),
                "sent_at": msg["sent_at"].isoformat() if msg.get("sent_at") else "",
            })

        return jsonify({
            "messages": result,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // per_page)),
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Meta Cloud API routes ─────────────────────────────────────────────────────


@whatsapp_bp.route("/whatsapp/meta/templates", methods=["GET"])
def get_meta_templates():
    return jsonify({"templates": PREBUILT_TEMPLATES}), 200


@whatsapp_bp.route("/whatsapp/meta/config/<company_id>", methods=["GET"])
def get_meta_config(company_id):
    try:
        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        return jsonify({
            "meta_phone_number_id": company.get("meta_phone_number_id", ""),
            "meta_access_token": company.get("meta_access_token", ""),
            "meta_waba_id": company.get("meta_waba_id", ""),
            "meta_enabled": company.get("meta_enabled", False),
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/meta/config/<company_id>", methods=["POST"])
def save_meta_config(company_id):
    try:
        data = request.json or {}
        update = {}

        for field in ("meta_phone_number_id", "meta_access_token", "meta_waba_id"):
            if field in data:
                update[field] = str(data[field]).strip()

        if "meta_enabled" in data:
            update["meta_enabled"] = bool(data["meta_enabled"])

        if not update:
            return jsonify({"error": "No fields to update"}), 400

        compCollection.update_one({"_id": ObjectId(company_id)}, {"$set": update})
        return jsonify({"message": "Meta Cloud config saved"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/meta/send_template", methods=["POST"])
def meta_send_template():
    try:
        data = request.json or {}

        company_id = str(data.get("company_id", "")).strip()
        lead_id = str(data.get("lead_id", "")).strip()
        user_id = str(data.get("user_id", "")).strip()
        phone = str(data.get("phone", "")).strip()
        lead_name = str(data.get("lead_name", "")).strip()
        template_name = str(data.get("template_name", "")).strip()
        language_code = str(data.get("language_code", "en")).strip()
        variables_used = data.get("variables_used", {})
        body_preview = str(data.get("body_preview", "")).strip()

        if not company_id:
            return jsonify({"error": "company_id is required"}), 400
        if not phone:
            return jsonify({"error": "phone is required"}), 400
        if not template_name:
            return jsonify({"error": "template_name is required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        # Per-company credentials take priority; fall back to platform-level env vars
        phone_number_id = str(company.get("meta_phone_number_id", "")).strip() or _PLATFORM_META_PHONE_NUMBER_ID
        access_token = str(company.get("meta_access_token", "")).strip() or _PLATFORM_META_ACCESS_TOKEN

        print(f"[META SEND] company={company_id} phone={phone} template={template_name}", flush=True)
        print(f"[META SEND] phone_number_id exists: {bool(phone_number_id)}", flush=True)

        components = build_components(variables_used) if variables_used else None

        now = datetime.utcnow()

        # Insert log immediately as "pending" so we always have a record
        log_doc = _build_meta_log_doc(
            company_id=company_id,
            lead_id=lead_id,
            user_id=user_id,
            phone=phone,
            lead_name=lead_name,
            template_name=template_name,
            language_code=language_code,
            variables_used=variables_used,
            body_preview=body_preview,
            now=now,
            direction="outbound",
            message_type="template",
            status="pending",
        )
        inserted = waMessagesCollection.insert_one(log_doc)
        log_id = inserted.inserted_id

        result = send_meta_template(
            phone_number_id=phone_number_id,
            access_token=access_token,
            to_phone=phone,
            template_name=template_name,
            language_code=language_code,
            components=components,
        )

        final_status = "sent" if result["ok"] else "failed"
        final_status_at = datetime.utcnow()
        waMessagesCollection.update_one(
            {"_id": log_id},
            {"$set": {
                "status": final_status,
                "meta_message_id": result.get("meta_message_id"),
                "provider_response": result.get("provider_response", {}),
                "error_details": {} if result["ok"] else {"message": result.get("message", "Meta send failed")},
                f"status_timestamps.{final_status}": final_status_at,
                "updated_at": final_status_at,
            }},
        )

        if not result["ok"]:
            return jsonify({
                "error": result.get("message", "Meta send failed"),
                "provider_response": result.get("provider_response", {}),
                "log_id": str(log_id),
            }), 400

        return jsonify({
            "message": "Template sent via Meta Cloud API",
            "meta_message_id": result["meta_message_id"],
            "log_id": str(log_id),
            "status": "sent",
        }), 200

    except Exception as e:
        print(f"[META SEND] Exception: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/meta/messages/<company_id>", methods=["GET"])
def get_meta_messages(company_id):
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        skip = (page - 1) * per_page
        direction = str(request.args.get("direction", "outbound")).strip().lower()
        if direction not in {"all", "inbound", "outbound"}:
            direction = "outbound"

        query = {"company_id": company_id, "provider": "meta_cloud"}
        if direction in {"inbound", "outbound"}:
            query["direction"] = direction
        messages = list(
            waMessagesCollection.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(per_page)
        )
        total = waMessagesCollection.count_documents(query)

        result = []
        for msg in messages:
            result.append({
                "id": str(msg["_id"]),
                "lead_id": msg.get("lead_id", ""),
                "lead_name": msg.get("lead_name", "Unknown"),
                "contact_phone": msg.get("contact_phone", ""),
                "from_phone": msg.get("from_phone", ""),
                "to_phone": msg.get("to_phone", ""),
                "template_name": msg.get("template_name", ""),
                "variables_used": msg.get("variables_used", {}),
                "body_preview": msg.get("body_preview", ""),
                "message_type": msg.get("message_type", ""),
                "meta_message_id": msg.get("meta_message_id", ""),
                "status": msg.get("status", "pending"),
                "direction": msg.get("direction", "outbound"),
                "user_id": msg.get("user_id", ""),
                "created_at": msg["created_at"].isoformat() if msg.get("created_at") else "",
            })

        return jsonify({
            "messages": result,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // per_page)),
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/meta/conversations/<company_id>", methods=["GET"])
def get_meta_conversations(company_id):
    try:
        pipeline = [
            {"$match": {"company_id": company_id, "provider": "meta_cloud"}},
            {"$sort": {"created_at": -1}},
            {"$group": {
                "_id":            "$contact_phone",
                "contact_phone":  {"$first": "$contact_phone"},
                "lead_id":        {"$first": "$lead_id"},
                "lead_name":      {"$first": "$lead_name"},
                "last_body":      {"$first": "$body_preview"},
                "last_direction": {"$first": "$direction"},
                "last_status":    {"$first": "$status"},
                "last_at":        {"$first": "$created_at"},
                "inbound_count":  {"$sum": {"$cond": [{"$eq": ["$direction", "inbound"]}, 1, 0]}},
                "total_count":    {"$sum": 1},
            }},
            {"$sort": {"last_at": -1}},
        ]
        conversations = list(waMessagesCollection.aggregate(pipeline))

        result = []
        for conv in conversations:
            last_dir = conv.get("last_direction", "outbound")
            inbound_count = conv.get("inbound_count", 0)

            if last_dir == "inbound":
                conv_status = "unread"
            elif inbound_count > 0:
                conv_status = "replied"
            else:
                conv_status = "no_reply"

            assigned_to = ""
            lead_id = conv.get("lead_id", "")
            if lead_id:
                try:
                    lead = leadCollection.find_one({"_id": ObjectId(lead_id)}, {"uploaded_by": 1})
                    if lead:
                        ub = lead.get("uploaded_by", "")
                        if ub and ub != "gsheet_sync":
                            user_q = {"_id": ObjectId(ub)} if ObjectId.is_valid(ub) else {"clerk_user_id": ub}
                            user = usersCollection.find_one(user_q, {"name": 1})
                            if user:
                                assigned_to = user.get("name", "")
                except Exception:
                    pass

            result.append({
                "contact_phone":  conv.get("contact_phone", ""),
                "lead_id":        lead_id,
                "lead_name":      conv.get("lead_name", "Unknown"),
                "last_body":      conv.get("last_body", ""),
                "last_direction": last_dir,
                "last_status":    conv.get("last_status", ""),
                "last_at":        conv.get("last_at").isoformat() if conv.get("last_at") else "",
                "inbound_count":  inbound_count,
                "total_count":    conv.get("total_count", 0),
                "status":         conv_status,
                "assigned_to":    assigned_to,
            })

        return jsonify({"conversations": result, "total": len(result)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/meta/thread/<company_id>/<path:contact_phone>", methods=["GET"])
def get_meta_thread(company_id, contact_phone):
    try:
        messages = list(
            waMessagesCollection.find({
                "company_id":    company_id,
                "provider":      "meta_cloud",
                "contact_phone": contact_phone,
            }).sort("created_at", 1)
        )

        result = []
        for msg in messages:
            st = {}
            for k, v in msg.get("status_timestamps", {}).items():
                st[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
            result.append({
                "id":                str(msg["_id"]),
                "direction":         msg.get("direction", "outbound"),
                "body_preview":      msg.get("body_preview", ""),
                "template_name":     msg.get("template_name", ""),
                "message_type":      msg.get("message_type", ""),
                "status":            msg.get("status", "pending"),
                "meta_message_id":   msg.get("meta_message_id", ""),
                "user_id":           msg.get("user_id", ""),
                "from_name":         msg.get("from_name", ""),
                "created_at":        msg["created_at"].isoformat() if msg.get("created_at") else "",
                "status_timestamps": st,
            })

        return jsonify({"messages": result, "total": len(result)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
