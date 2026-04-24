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


def normalize_followup_variables(raw_variables, company=None):
    """
    Supports both:
    frontend old keys:
    sender_name, company_service, value_prop, pain_point, cta

    backend sequence keys:
    your_name, product_service, help_with, main_problem, call_to_action
    """
    raw_variables = raw_variables or {}
    company = company or {}

    sender_email = company.get("sender_email", "")
    website = company.get("website", "") or "ageautomation.in"
    company_name = company.get("name", "")

    variables = {
        "your_name": (
            raw_variables.get("your_name")
            or raw_variables.get("sender_name")
            or ""
        ).strip(),
        "product_service": (
            raw_variables.get("product_service")
            or raw_variables.get("company_service")
            or ""
        ).strip(),
        "help_with": (
            raw_variables.get("help_with")
            or raw_variables.get("value_prop")
            or ""
        ).strip(),
        "main_problem": (
            raw_variables.get("main_problem")
            or raw_variables.get("pain_point")
            or ""
        ).strip(),
        "call_to_action": (
            raw_variables.get("call_to_action")
            or raw_variables.get("cta")
            or ""
        ).strip(),
        "industry": (raw_variables.get("industry") or "").strip(),
        "signature_title": (raw_variables.get("signature_title") or "Founder").strip(),
        "website": (raw_variables.get("website") or website).strip(),
        "sender_email": (raw_variables.get("sender_email") or sender_email).strip(),
        "company": (raw_variables.get("company") or company_name).strip(),

        # Keep old keys too for backward compatibility.
        "sender_name": (
            raw_variables.get("sender_name")
            or raw_variables.get("your_name")
            or ""
        ).strip(),
        "company_service": (
            raw_variables.get("company_service")
            or raw_variables.get("product_service")
            or ""
        ).strip(),
        "value_prop": (
            raw_variables.get("value_prop")
            or raw_variables.get("help_with")
            or ""
        ).strip(),
        "pain_point": (
            raw_variables.get("pain_point")
            or raw_variables.get("main_problem")
            or ""
        ).strip(),
        "cta": (
            raw_variables.get("cta")
            or raw_variables.get("call_to_action")
            or ""
        ).strip(),
    }

    # Preserve any extra advanced variables
    for key, value in raw_variables.items():
        if key not in variables:
            variables[key] = "" if value is None else str(value)

    return variables


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
        data = request.json or {}
        send_type = data.get("type")
        interval_days = data.get("interval_days")
        subject = data.get("subject", "Follow-up from AGE")
        template = data.get("message", "")
        raw_variables = data.get("variables", {})

        if send_type not in ["email", "sms", "both"]:
            return jsonify({"error": "Invalid send type"}), 400
        if interval_days not in [2, 3, 4, 5, 6, 7]:
            return jsonify({"error": "Interval must be between 2 and 7 days"}), 400
        if not template.strip():
            return jsonify({"error": "Message template is required"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        variables = normalize_followup_variables(raw_variables, company)

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
            "variables": variables,
            "is_active": True,
            "is_recurring": True,
            "is_sequence": False,
            "created_at": now,
            "updated_at": now,
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
            final_message = render_message(template, lead, variables)
            yes_link, no_link = build_response_links(lead_id_str)
            text_body = f"{final_message}\n\nYes: {yes_link}\nNo: {no_link}"
            html_body = build_email_html(final_message, yes_link, no_link)

            try:
                if send_type in ["email", "both"]:
                    if not lead.get("email"):
                        failed.append({
                            "lead_id": lead_id_str,
                            "name": lead.get("name", ""),
                            "reason": "Missing email"
                        })
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
                    "campaign_id": str(campaign_id),
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
                        "response_status": "pending",
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
        raw_variables = data.get("variables", {})

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

        company = compCollection.find_one({"_id": ObjectId(lead["company_id"])})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        variables = normalize_followup_variables(raw_variables, company)

        now = datetime.utcnow()

        campaign = {
            "company_id": lead["company_id"],
            "channel": channel,
            "interval_days": interval_days,
            "message": message,
            "subject": subject,
            "variables": variables,
            "is_active": True,
            "is_recurring": True,
            "is_sequence": False,
            "created_at": now,
            "updated_at": now,
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