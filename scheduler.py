from datetime import datetime, timedelta
from bson import ObjectId
from apscheduler.schedulers.background import BackgroundScheduler
from db import leadCollection, campCollection, msgCollection, compCollection
import boto3
import os
from botocore.exceptions import ClientError
from services.msg91 import send_sms_msg91

scheduler = BackgroundScheduler()

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

    return ses_client.send_email(
        FromEmailAddress=sender_email,
        Destination={"ToAddresses": [to_email]},
        Content={
            "Simple": {
                "Subject": {"Data": subject},
                "Body": body
            }
        }
    )


def send_followup(lead, campaign):
    now = datetime.utcnow()
    lead_id_str = str(lead["_id"])

    print(f"[SCHEDULER] Sending follow-up for lead {lead_id_str}")

    final_message = render_message(campaign.get("message", ""), lead)
    subject = campaign.get("subject", "Follow-up from AGE")
    yes_link, no_link = build_response_links(lead_id_str)

    text_body = f"""{final_message}

Yes: {yes_link}
No: {no_link}
"""

    html_body = build_email_html(final_message, yes_link, no_link)

    company = compCollection.find_one({"_id": ObjectId(lead["company_id"])})
    sender_email = company.get("sender_email", "") if company else ""

    if campaign["channel"] in ["email", "both"]:
        if not sender_email:
            raise ValueError(f"Sender email not configured for company {lead['company_id']}")

        if not lead.get("email"):
            raise ValueError(f"Missing recipient email for lead {lead_id_str}")

        send_email_ses(
            to_email=lead["email"],
            subject=subject,
            body_text=text_body,
            sender_email=sender_email,
            html_body=html_body
        )
        print(f"[SCHEDULER] Email sent for lead {lead_id_str}")

    if campaign["channel"] in ["sms", "both"]:
        sms_enabled = company.get("sms_enabled", False) if company else False
        if sms_enabled and lead.get("phone"):
            template_id = company.get("msg91_template_id_followup") or company.get("msg91_template_id_initial", "")
            auth_key = company.get("msg91_api_key", "") or None
            send_sms_msg91(
                mobile=lead["phone"],
                template_id=template_id,
                variables={"name": lead.get("name", "there")},
                auth_key=auth_key,
            )
            print(f"[SCHEDULER] SMS sent via MSG91 for lead {lead_id_str}")
        else:
            print(f"[SCHEDULER] SMS skipped for lead {lead_id_str} — sms_enabled={sms_enabled}")

    msgCollection.insert_one({
        "lead_id": lead_id_str,
        "company_id": lead["company_id"],
        "channel": campaign["channel"],
        "message": final_message,
        "subject": subject,
        "yes_link": yes_link,
        "no_link": no_link,
        "sent_at": now,
        "status": "sent",
        "message_type": "followup"
    })
    print(f"[SCHEDULER] Message log inserted for lead {lead_id_str}")

    send_status_value = "not sent"
    if campaign["channel"] == "email":
        send_status_value = "email sent"
    elif campaign["channel"] == "sms":
        send_status_value = "sms sent"
    elif campaign["channel"] == "both":
        send_status_value = "both sent"

    is_recurring = campaign.get("is_recurring", True)
    next_followup_at = None

    interval_days = campaign.get("interval_days", 2)
    if lead.get("is_individual_followup") and lead.get("pref_interval_days"):
        interval_days = lead.get("pref_interval_days")

    if is_recurring:
        # next_followup_at = now + timedelta(days=interval_days)
        next_followup_at = now + timedelta(seconds=10)

    result = leadCollection.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "send_status": send_status_value,
                "last_followup_sent_at": now,
                "next_followup_at": next_followup_at,
            },
            "$inc": {
                "followup_count": 1
            }
        }
    )

    print(
        f"[SCHEDULER] Lead update result for {lead_id_str}: "
        f"matched={result.matched_count}, modified={result.modified_count}"
    )


def process_followups():
    now = datetime.utcnow()

    try:
        due_leads = list(leadCollection.find({
            "next_followup_at": {"$ne": None, "$lte": now}
        }))

        print(f"[SCHEDULER] Found {len(due_leads)} due leads at {now}")

        for lead in due_leads:
            if lead.get("response_status") in ["yes", "no"]:
                print(f"[SCHEDULER] Skipping lead {lead['_id']} because response is {lead.get('response_status')}")
                continue

            campaign_id = lead.get("campaign_id")
            if not campaign_id:
                print(f"[SCHEDULER] Skipping lead {lead['_id']} because campaign_id is missing")
                continue

            if isinstance(campaign_id, str):
                try:
                    campaign_id = ObjectId(campaign_id)
                except Exception:
                    print(f"[SCHEDULER] Invalid campaign_id for lead {lead['_id']}")
                    continue

            campaign = campCollection.find_one({"_id": campaign_id})
            if not campaign or not campaign.get("is_active", False):
                print(f"[SCHEDULER] Skipping lead {lead['_id']} because campaign inactive/missing")
                continue

            try:
                send_followup(lead, campaign)
            except ClientError as e:
                print(f"[SCHEDULER][AWS ERROR] Lead {lead['_id']}: {str(e)}")
            except Exception as e:
                print(f"[SCHEDULER][ERROR] Lead {lead['_id']}: {str(e)}")

    except Exception as e:
        print(f"[SCHEDULER][PROCESS ERROR] {str(e)}")


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            process_followups,
            "interval",
            # minutes=1,
            seconds=5,
            id="followup_scheduler",
            replace_existing=True
        )
        scheduler.start()
        print("[SCHEDULER] Started successfully")