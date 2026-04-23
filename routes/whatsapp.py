import os
from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from db import leadCollection, msgCollection, compCollection
from services.whatsapp import send_whatsapp_text, send_whatsapp_template, format_phone_wa

_PLATFORM_WA_KEY = os.getenv("MSG91_WHATSAPP_AUTH_KEY") or os.getenv("MSG91_AUTH_KEY", "")

whatsapp_bp = Blueprint("whatsapp", __name__)


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