import os
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from db import leadCollection, msgCollection, compCollection
from services.whatsapp import send_whatsapp_text, send_whatsapp_template, format_phone_wa

# Platform-level WhatsApp auth key — same MSG91 key used for SMS
_PLATFORM_WA_KEY = os.getenv("MSG91_WHATSAPP_AUTH_KEY") or os.getenv("MSG91_AUTH_KEY", "")

whatsapp_bp = Blueprint("whatsapp", __name__)


@whatsapp_bp.route("/whatsapp/config/<company_id>", methods=["GET"])
def get_wa_config(company_id):
    try:
        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404
        return jsonify({
            "wa_auth_key": company.get("wa_auth_key", ""),
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
        if "wa_auth_key" in data:
            update["wa_auth_key"] = data["wa_auth_key"].strip()
        if "wa_number" in data:
            update["wa_number"] = data["wa_number"].strip()
        if "wa_template_name" in data:
            update["wa_template_name"] = data["wa_template_name"].strip()
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
        company_id = data.get("company_id", "").strip()
        phone = data.get("phone", "").strip()
        message = data.get("message", "").strip()
        lead_id = data.get("lead_id", "")
        lead_name = data.get("lead_name", "")

        if not company_id or not phone or not message:
            return jsonify({"error": "company_id, phone, and message are required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        auth_key = company.get("wa_auth_key", "") or _PLATFORM_WA_KEY or None
        integrated_number = company.get("wa_number", "")
        template_name = company.get("wa_template_name", "")

        if template_name:
            result = send_whatsapp_template(
                phone=phone,
                template_name=template_name,
                template_params=[lead_name, message] if lead_name else [message],
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

        wa_type = result.get("type", "")
        if wa_type == "skipped":
            return jsonify({"error": result.get("message", "WhatsApp skipped — check settings")}), 400
        if wa_type == "error":
            return jsonify({"error": result.get("message", "Send failed")}), 500

        status = "sent"
        msgCollection.insert_one({
            "lead_id": lead_id,
            "lead_name": lead_name,
            "company_id": company_id,
            "channel": "whatsapp",
            "phone": format_phone_wa(phone),
            "message": message,
            "sent_at": datetime.utcnow(),
            "status": status,
            "message_type": "manual",
        })

        return jsonify({"message": "WhatsApp sent successfully"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/whatsapp/send_bulk/<company_id>", methods=["POST"])
def send_bulk_whatsapp(company_id):
    try:
        data = request.json or {}
        message = data.get("message", "").strip()

        if not message:
            return jsonify({"error": "Message is required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        integrated_number = company.get("wa_number", "")
        if not integrated_number:
            return jsonify({
                "error": "WhatsApp integrated number not configured. Go to Settings to set it up."
            }), 400

        auth_key = company.get("wa_auth_key", "") or _PLATFORM_WA_KEY or None
        template_name = company.get("wa_template_name", "")

        leads = list(leadCollection.find({
            "company_id": company_id,
            "is_individual_followup": {"$ne": True},
        }))

        leads_with_phone = [l for l in leads if l.get("phone")]
        if not leads_with_phone:
            return jsonify({"error": "No leads with phone numbers found"}), 400

        now = datetime.utcnow()
        count = 0
        failed = []

        for lead in leads_with_phone:
            lead_id_str = str(lead["_id"])
            lead_name = lead.get("name", "")
            phone = lead.get("phone", "")

            personal_msg = (
                message
                .replace("{{name}}", lead_name)
                .replace("{{their_company}}", lead.get("company", ""))
            )

            try:
                if template_name:
                    result = send_whatsapp_template(
                        phone=phone,
                        template_name=template_name,
                        template_params=[lead_name, personal_msg] if lead_name else [personal_msg],
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

                wa_type = result.get("type", "")
                status = "error" if wa_type in ("error", "skipped") else "sent"

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
                })

                if status == "sent":
                    count += 1
                else:
                    failed.append({
                        "lead_id": lead_id_str,
                        "name": lead_name,
                        "reason": result.get("message", "Failed"),
                    })

            except Exception as e:
                failed.append({"lead_id": lead_id_str, "name": lead_name, "reason": str(e)})

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
