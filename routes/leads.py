from flask import Blueprint, request, jsonify
from bson import ObjectId

from db import leadCollection

leads_bp = Blueprint("leads", __name__)


@leads_bp.route("/get_leads/<company_id>", methods=["GET"])
def get_leads(company_id):
    try:
        leads = list(leadCollection.find({"company_id": company_id}))

        formatted_leads = []
        for lead in leads:
            formatted_leads.append(
                {
                    "_id": str(lead["_id"]),
                    "company_id": lead.get("company_id"),
                    "uploaded_by": lead.get("uploaded_by"),
                    "name": lead.get("name", ""),
                    "email": lead.get("email", ""),
                    "phone": lead.get("phone", ""),
                    "send_status": lead.get("send_status", "not sent"),
                    "response_status": lead.get("response_status", "pending"),
                    "followup_count": lead.get("followup_count", 0),
                    "last_followup_sent_at": lead.get("last_followup_sent_at"),
                    "next_followup_at": lead.get("next_followup_at"),
                    "is_individual_followup": lead.get("is_individual_followup", False),
                    "pref_channel": lead.get("pref_channel", "email"),
                    "pref_interval_days": lead.get("pref_interval_days", 2),
                }
            )

        return jsonify(formatted_leads), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/update_lead_response/<lead_id>", methods=["PATCH"])
def update_lead_response(lead_id):
    try:
        data = request.json
        new_status = data.get("response_status")

        if new_status not in ["yes", "no", "no reply", "pending"]:
            return jsonify({"error": "Invalid response status"}), 400

        result = leadCollection.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": {"response_status": new_status, "next_followup_at": None}},
        )

        if result.matched_count == 0:
            return jsonify({"error": "Lead not found"}), 404

        return jsonify({"message": "Lead response updated successfully"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/lead_schedule/<lead_id>", methods=["PATCH"])
def save_lead_schedule(lead_id):
    try:
        data = request.json or {}
        channel = data.get("channel")
        interval_days = data.get("interval_days")

        updates = {}
        if channel in ["email", "sms", "both"]:
            updates["pref_channel"] = channel
        if interval_days in [2, 3, 4, 5, 6, 7]:
            updates["pref_interval_days"] = interval_days

        if updates:
            leadCollection.update_one({"_id": ObjectId(lead_id)}, {"$set": updates})

        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/mark_individual/<lead_id>", methods=["PATCH"])
def mark_individual(lead_id):
    try:
        data = request.json or {}
        value = bool(data.get("individual", True))

        leadCollection.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": {"is_individual_followup": value}},
        )
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/delete_company_leads/<company_id>", methods=["DELETE"])
def delete_company_leads(company_id):
    try:
        result = leadCollection.delete_many({"company_id": company_id})
        return jsonify({"message": f"{result.deleted_count} leads deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500