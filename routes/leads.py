from flask import Blueprint, request, jsonify
import pandas as pd
import os
from werkzeug.utils import secure_filename
from bson import ObjectId
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError

from db import leadCollection, campCollection, msgCollection, usersCollection, compCollection

leads_bp = Blueprint("leads", __name__)

UPLOAD_FOLDER = "uploads"
SES_REGION = "ap-south-1"
RESPONSE_BASE_URL = os.getenv("RESPONSE_BASE_URL", "http://127.0.0.1:5000")


def render_message(template, lead):
    return template.replace("{{name}}", lead.get("name", "there"))


def build_response_links(lead_id):
    yes_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/yes"
    no_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/no"
    return yes_link, no_link


def build_email_html(final_message, yes_link, no_link):
    safe_message = final_message.replace("\n", "<br>")
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background: #0a0a0a; color: white; padding: 24px;">
        <div style="max-width: 600px; margin: 0 auto; background: #111; padding: 24px; border-radius: 12px; border: 1px solid #222;">
          <p style="font-size: 16px; line-height: 1.6; margin-bottom: 24px;">
            {safe_message}
          </p>

          <div style="margin-top: 24px;">
            <a href="{yes_link}"
               style="display: inline-block; background: #22c55e; color: black; text-decoration: none; padding: 12px 20px; border-radius: 10px; font-weight: bold; margin-right: 12px;">
              Yes
            </a>

            <a href="{no_link}"
               style="display: inline-block; background: #ef4444; color: white; text-decoration: none; padding: 12px 20px; border-radius: 10px; font-weight: bold;">
              No
            </a>
          </div>

          <p style="margin-top: 24px; font-size: 12px; color: #aaa;">
            If the buttons don’t work, use these links:<br>
            Yes: {yes_link}<br>
            No: {no_link}
          </p>
        </div>
      </body>
    </html>
    """


def send_email_ses(to_email, subject, body_text, sender_email, html_body=None):
    ses_client = boto3.client("sesv2", region_name=SES_REGION)

    body = {
        "Text": {
            "Data": body_text
        }
    }

    if html_body:
        body["Html"] = {
            "Data": html_body
        }

    response = ses_client.send_email(
        FromEmailAddress=sender_email,
        Destination={
            "ToAddresses": [to_email]
        },
        Content={
            "Simple": {
                "Subject": {
                    "Data": subject
                },
                "Body": body
            }
        }
    )

    return response


@leads_bp.route("/upload_leads", methods=["POST"])
def upload_csv():
    file = request.files.get("file")
    company_id = request.form.get("company_id")
    user_id = request.form.get("user_id")

    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    if not company_id:
        return jsonify({"error": "company_id is required"}), 400

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)

    if filename == "":
        return jsonify({"error": "No file selected"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(filepath)

        if filename.endswith(".csv"):
            df = pd.read_csv(filepath)
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(filepath)
        else:
            os.remove(filepath)
            return jsonify({"error": "Only CSV, XLSX, and XLS files allowed"}), 400

        df.columns = [col.lower().strip() for col in df.columns]

        inserted_count = 0
        skipped_count = 0
        duplicates = []

        for _, row in df.iterrows():
            name = row.get("name")
            email = row.get("email")
            phone = row.get("phone")

            name = "" if pd.isna(name) else str(name).strip()
            email = "" if pd.isna(email) else str(email).strip()
            phone = "" if pd.isna(phone) else str(phone).strip()

            if not name and not email and not phone:
                continue

            duplicate_query = {
                "company_id": company_id,
                "$or": []
            }

            if email:
                duplicate_query["$or"].append({"email": email})

            if phone:
                duplicate_query["$or"].append({"phone": phone})

            existing = None
            if duplicate_query["$or"]:
                existing = leadCollection.find_one(duplicate_query)

            if existing:
                skipped_count += 1

                uploader_name = "Another salesperson"
                uploader_id = existing.get("uploaded_by")

                if uploader_id:
                    existing_user = None
                    try:
                        existing_user = usersCollection.find_one({"_id": ObjectId(uploader_id)})
                    except Exception:
                        existing_user = usersCollection.find_one({"_id": uploader_id})

                    if existing_user:
                        uploader_name = existing_user.get("name", "Another salesperson")

                duplicates.append({
                    "name": name,
                    "email": email,
                    "phone": phone,
                    "already_uploaded_by": uploader_name
                })

                continue

            lead = {
                "company_id": company_id,
                "uploaded_by": user_id,
                "name": name,
                "email": email,
                "phone": phone,
                "send_status": "not sent",
                "response_status": "pending",
                "followup_count": 0,
                "last_followup_sent_at": None,
                "next_followup_at": None,
                "campaign_id": None
            }

            leadCollection.insert_one(lead)
            inserted_count += 1

        os.remove(filepath)

        return jsonify({
            "message": f"{inserted_count} leads uploaded successfully",
            "skipped_duplicates": skipped_count,
            "duplicates": duplicates
        }), 200

    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/get_leads/<company_id>", methods=["GET"])
def get_leads(company_id):
    try:
        leads = list(leadCollection.find({"company_id": company_id}))

        formatted_leads = []
        for lead in leads:
            formatted_leads.append({
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
                "next_followup_at": lead.get("next_followup_at")
            })

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
            {"$set": {"response_status": new_status, "next_followup_at": None}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Lead not found"}), 404

        return jsonify({"message": "Lead response updated successfully"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/respond/<lead_id>/<response>", methods=["GET"])
def respond_to_lead(lead_id, response):
    try:
        if response not in ["yes", "no"]:
            return jsonify({"error": "Invalid response"}), 400

        result = leadCollection.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": {"response_status": response}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Lead not found"}), 404

        if response == "yes":
            message = "We will contact you soon."
        else:
            message = "Thank you for your time."

        return f"""
        <html>
            <head><title>Response Received</title></head>
            <body style="font-family: Arial; background-color: #0a0a0a; color: white; text-align: center; padding-top: 100px;">
                <h1>Thank you!</h1>
                <p>{message}</p>
            </body>
        </html>
        """

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/send_bulk/<company_id>", methods=["POST"])
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
            "created_at": now
        }

        campaign_result = campCollection.insert_one(campaign)
        campaign_id = campaign_result.inserted_id

        leads = list(leadCollection.find({"company_id": company_id}))
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

            text_body = f"""{final_message}

Yes: {yes_link}
No: {no_link}
"""

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
                        html_body=html_body
                    )

                # SMS placeholder for now
                if send_type in ["sms", "both"]:
                    pass

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
                    "message_type": "initial"
                })

                leadCollection.update_one(
                    {"_id": lead["_id"]},
                    {
                        "$set": {
                            "send_status": send_status_value,
                            "campaign_id": campaign_id,
                            "followup_count": 1,
                            "last_followup_sent_at": now,
                            "next_followup_at": now + timedelta(days=campaign["interval_days"])
                            # "next_followup_at": now + timedelta(minutes=1)
                        }
                    }
                )

                count += 1

            except ClientError as e:
                failed.append({
                    "lead_id": lead_id_str,
                    "name": lead.get("name", ""),
                    "reason": str(e)
                })
            except Exception as e:
                failed.append({
                    "lead_id": lead_id_str,
                    "name": lead.get("name", ""),
                    "reason": str(e)
                })

        return jsonify({
            "message": f"{count} messages sent via {send_type}",
            "campaign_id": str(campaign_id),
            "failed": failed
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    
    
@leads_bp.route("/start_followup/<lead_id>", methods=["POST"])
def start_followup(lead_id):
    try:
        data = request.json

        subject = data.get("subject", "Follow-up from AGE")
        message = data.get("message", "").strip()
        channel = data.get("channel")
        interval_days = data.get("interval_days", 2)

        if not message:
            return jsonify({"error": "Message is required"}), 400

        if channel not in ["email", "sms", "both"]:
            return jsonify({"error": "Invalid channel"}), 400

        lead = leadCollection.find_one({"_id": ObjectId(lead_id)})
        if not lead:
            return jsonify({"error": "Lead not found"}), 404

        now = datetime.utcnow()

        campaign = {
            "company_id": lead["company_id"],
            "channel": channel,
            "interval_days": interval_days,
            "message": message,
            "subject": subject,
            "is_active": True,
            "is_recurring": True,
            "created_at": now
        }

        campaign_result = campCollection.insert_one(campaign)

        # 🔥 TEST MODE (10 seconds)
        next_followup = now + timedelta(seconds=10)

        # ✅ PRODUCTION MODE (uncomment later)
        # next_followup = now + timedelta(days=interval_days)

        leadCollection.update_one(
            {"_id": ObjectId(lead_id)},
            {
                "$set": {
                    "campaign_id": campaign_result.inserted_id,
                    "next_followup_at": next_followup
                }
            }
        )

        return jsonify({"message": "Follow-up started"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/delete_company_leads/<company_id>", methods=["DELETE"])
def delete_company_leads(company_id):
    try:
        result = leadCollection.delete_many({"company_id": company_id})
        return jsonify({
            "message": f"{result.deleted_count} leads deleted"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500