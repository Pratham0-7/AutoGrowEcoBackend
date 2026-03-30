from datetime import datetime, timedelta
from bson import ObjectId
from apscheduler.schedulers.background import BackgroundScheduler
from db import leadCollection, campCollection, msgCollection, compCollection
import boto3
import os
from botocore.exceptions import ClientError

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

    try:
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
                print(f"[SCHEDULER] Missing sender_email for company {lead['company_id']}")
                return

            if not lead.get("email"):
                print(f"[SCHEDULER] Missing recipient email for lead {lead_id_str}")
                return

            send_email_ses(
                to_email=lead["email"],
                subject=subject,
                body_text=text_body,
                sender_email=sender_email,
                html_body=html_body
            )
            print(f"[SCHEDULER] Email sent for lead {lead_id_str}")

        # SMS placeholder
        if campaign["channel"] in ["sms", "both"]:
            print(f"[SCHEDULER] SMS placeholder for lead {lead_id_str}")

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

        result = leadCollection.update_one(
            {"_id": lead["_id"]},
            {
                "$set": {
                    "send_status": send_status_value,
                    "last_followup_sent_at": now,
                    "next_followup_at": now + timedelta(days=campaign["interval_days"])
                    # "next_followup_at": now + timedelta(minutes=1)   # TEST MODE
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

    except ClientError as e:
        print(f"[SCHEDULER][AWS ERROR] Lead {lead_id_str}: {str(e)}")
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Lead {lead_id_str}: {str(e)}")


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

            campaign = campCollection.find_one({"_id": campaign_id})
            if not campaign or not campaign.get("is_active", False):
                print(f"[SCHEDULER] Skipping lead {lead['_id']} because campaign inactive/missing")
                continue

            send_followup(lead, campaign)

    except Exception as e:
        print(f"[SCHEDULER][PROCESS ERROR] {str(e)}")


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            process_followups,
            "interval",
            minutes=1,
            id="followup_scheduler",
            replace_existing=True
        )
        scheduler.start()
        print("[SCHEDULER] Started successfully")