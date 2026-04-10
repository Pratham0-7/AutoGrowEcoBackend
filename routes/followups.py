from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta
from botocore.exceptions import ClientError

from db import leadCollection, campCollection, msgCollection, compCollection
from services.msg91 import send_sms_msg91
from services.email_service import (
    render_message,
    build_response_links,
    build_email_html,
    send_email_ses,
)
from scheduler import send_followup

followups_bp = Blueprint("followups", __name__)


@followups_bp.route("/respond/<lead_id>/<response>", methods=["GET"])
def respond_to_lead(lead_id, response):
    def html_page(title, heading, subtext, accent):
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{title}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;">
  <div style="background: white; border: 1px solid #e2e8f0; border-radius: 20px; padding: 48px 40px; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,0.06);">
    <h1>{heading}</h1>
    <p>{subtext}</p>
  </div>
</body>
</html>"""

    try:
        if response not in ["yes", "no"]:
            return html_page("Invalid Link", "Invalid Link", "This response link is not valid.", "#ef4444"), 400

        result = leadCollection.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": {"response_status": response, "next_followup_at": None}}
        )

        if result.matched_count == 0:
            return html_page("Link Expired", "Link Expired", "This link is no longer valid.", "#f59e0b"), 404

        if response == "yes":
            return html_page("Thanks", "We'll be in touch!", "Thanks for your interest.", "#22c55e"), 200

        return html_page("Response Received", "Got it, no problem.", "We won't reach out again.", "#64748b"), 200

    except Exception:
        return html_page("Error", "Something went wrong", "Please try again later.", "#ef4444"), 500


@followups_bp.route("/send_bulk/<company_id>", methods=["POST"])
def send_bulk(company_id):
    try:
        data = request.json
        send_type = data.get("type")
        interval_days = data.get("interval_days")
        subject = data.get("subject", "Follow-up from AGE")
        template = data.get("message", "")

        if send_type not in ["email", "sms", "both"]:
            return jsonify({"error": "Invalid send type"}), 400
        if interval_days not in [2, 3, 4, 5, 6, 7]:
            return jsonify({"error": "Interval must be between 2 and 7 days"}), 400
        if not template.strip():
            return jsonify({"error": "Message template is required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        sender_email = company.get("sender_email", "")
        if send_type in ["email", "both"] and not sender_email:
            return jsonify({"error": "Sender email not configured for this company"}), 400

        now = datetime.utcnow()

        campaign = {
            "company_id": company_id,
            "channel": send_type,
            "interval_days": interval_days,
            "message": template,
            "subject": subject,
            "is_active": True,
            "created_at": now,
        }

        campaign_result = campCollection.insert_one(campaign)
        campaign_id = campaign_result.inserted_id

        leads = list(leadCollection.find({
            "company_id": company_id,
            "is_individual_followup": {"$ne": True},
        }))

        count = 0
        failed = []

        send_status_value = "not sent"
        if send_type == "email":
            send_status_value = "email sent"
        elif send_type == "sms":
            send_status_value = "sms sent"
        elif send_type == "both":
            send_status_value = "both sent"

        for lead in leads:
            lead_id_str = str(lead["_id"])
            final_message = render_message(template, lead)
            yes_link, no_link = build_response_links(lead_id_str)
            text_body = f"{final_message}\n\nYes: {yes_link}\nNo: {no_link}"
            html_body = build_email_html(final_message, yes_link, no_link)

            try:
                if send_type in ["email", "both"]:
                    if not lead.get("email"):
                        failed.append({"lead_id": lead_id_str, "name": lead.get("name", ""), "reason": "Missing email"})
                        continue

                    send_email_ses(
                        to_email=lead["email"],
                        subject=subject,
                        body_text=text_body,
                        sender_email=sender_email,
                        html_body=html_body,
                    )

                if send_type in ["sms", "both"]:
                    sms_enabled = company.get("sms_enabled", False)
                    if sms_enabled and lead.get("phone"):
                        template_id = company.get("msg91_template_id_initial", "")
                        auth_key = company.get("msg91_api_key", "")
                        send_sms_msg91(
                            mobile=lead["phone"],
                            template_id=template_id,
                            variables={"name": lead.get("name", "there")},
                            auth_key=auth_key or None,
                        )

                msgCollection.insert_one({
                    "lead_id": lead_id_str,
                    "company_id": company_id,
                    "channel": send_type,
                    "message": final_message,
                    "subject": subject,
                    "yes_link": yes_link,
                    "no_link": no_link,
                    "sent_at": now,
                    "status": "sent",
                    "message_type": "initial",
                })

                leadCollection.update_one(
                    {"_id": lead["_id"]},
                    {"$set": {
                        "send_status": send_status_value,
                        "campaign_id": campaign_id,
                        "followup_count": 1,
                        "last_followup_sent_at": now,
                        "next_followup_at": now + timedelta(days=campaign["interval_days"]),
                    }}
                )

                count += 1

            except ClientError as e:
                failed.append({"lead_id": lead_id_str, "name": lead.get("name", ""), "reason": str(e)})
            except Exception as e:
                failed.append({"lead_id": lead_id_str, "name": lead.get("name", ""), "reason": str(e)})

        return jsonify({
            "message": f"{count} messages sent via {send_type}",
            "campaign_id": str(campaign_id),
            "failed": failed,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@followups_bp.route("/start_followup/<lead_id>", methods=["POST"])
def start_followup(lead_id):
    try:
        data = request.json or {}

        subject = data.get("subject", "Follow-up from AGE")
        message = data.get("message", "").strip()
        channel = data.get("channel")
        interval_days = int(data.get("interval_days", 2))

        if not message:
            return jsonify({"error": "Message is required"}), 400
        if channel not in ["email", "sms", "both"]:
            return jsonify({"error": "Invalid channel"}), 400
        if interval_days not in [2, 3, 4, 5, 6, 7]:
            return jsonify({"error": "Interval must be between 2 and 7 days"}), 400

        lead = leadCollection.find_one({"_id": ObjectId(lead_id)})
        if not lead:
            return jsonify({"error": "Lead not found"}), 404
        if channel in ["email", "both"] and not lead.get("email"):
            return jsonify({"error": "This lead has no email address"}), 400

        now = datetime.utcnow()

        campaign = {
            "company_id": lead["company_id"],
            "channel": channel,
            "interval_days": interval_days,
            "message": message,
            "subject": subject,
            "is_active": True,
            "is_recurring": True,
            "created_at": now,
        }

        campaign_result = campCollection.insert_one(campaign)
        campaign_id = campaign_result.inserted_id
        campaign["_id"] = campaign_id

        already_contacted = (lead.get("followup_count") or 0) > 0

        if already_contacted:
            next_followup = now + timedelta(days=interval_days)

            leadCollection.update_one(
                {"_id": ObjectId(lead_id)},
                {"$set": {
                    "campaign_id": campaign_id,
                    "response_status": "pending",
                    "is_individual_followup": True,
                    "pref_channel": channel,
                    "pref_interval_days": interval_days,
                    "last_followup_sent_at": lead.get("last_followup_sent_at") or now,
                    "next_followup_at": next_followup,
                }}
            )
        else:
            leadCollection.update_one(
                {"_id": ObjectId(lead_id)},
                {"$set": {
                    "campaign_id": campaign_id,
                    "response_status": "pending",
                    "is_individual_followup": True,
                    "pref_channel": channel,
                    "pref_interval_days": interval_days,
                }}
            )

            updated_lead = leadCollection.find_one({"_id": ObjectId(lead_id)})
            send_followup(updated_lead, campaign)

        return jsonify({"message": "Follow-up started"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500