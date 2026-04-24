from datetime import datetime, timedelta
from bson import ObjectId
from apscheduler.schedulers.background import BackgroundScheduler
from botocore.exceptions import ClientError

from db import leadCollection, campCollection, msgCollection, compCollection, stepCollection, usersCollection
from services.msg91 import send_sms_msg91
from services.email_service import (
    render_message,
    build_response_links,
    build_email_html,
    send_email_ses,
)
from services.notification_service import send_step_completion_notification

scheduler = BackgroundScheduler()


def get_step(campaign_id, step_number):
    if isinstance(campaign_id, str):
        campaign_id = ObjectId(campaign_id)
    return stepCollection.find_one({
        "campaign_id": campaign_id,
        "step_number": step_number
    })


def get_total_steps(campaign_id):
    if isinstance(campaign_id, str):
        campaign_id = ObjectId(campaign_id)
    return stepCollection.count_documents({"campaign_id": campaign_id})


def _to_datetime(value):
    if isinstance(value, datetime):
        return value
    return None


def send_followup(lead, campaign):
    now = datetime.utcnow()
    lead_id_str = str(lead["_id"])
    campaign_id = campaign["_id"]
    is_sequence = campaign.get("is_sequence", False)

    company = compCollection.find_one({"_id": ObjectId(lead["company_id"])})
    sender_email = company.get("sender_email", "") if company else ""

    if is_sequence:
        current_step_number = int(lead.get("current_step", 1))
        step = get_step(campaign_id, current_step_number)
        total_steps = get_total_steps(campaign_id)

        if not step:
            print(f"[SCHEDULER] No step {current_step_number} found — marking complete for lead {lead_id_str}")
            leadCollection.update_one(
                {"_id": lead["_id"]},
                {"$set": {"sequence_complete": True, "next_followup_at": None}}
            )
            return

        message_template = step.get("message", "")
        subject = step.get("subject", "Follow-up")
        channel = step.get("channel", campaign.get("channel", "email"))

        variables = dict(campaign.get("variables", {}))
        if company:
            variables.setdefault("company", company.get("name", ""))
            variables.setdefault("sender_email", company.get("sender_email", ""))
            variables.setdefault("website", company.get("website", "") or "ageautomation.in")

        print(f"[SCHEDULER] Sequence step {current_step_number}/{total_steps} for lead {lead_id_str}")

    else:
        current_step_number = None
        total_steps = 0
        message_template = campaign.get("message", "")
        subject = campaign.get("subject", "Follow-up from AGE")
        channel = campaign.get("channel", "email")

        variables = dict(campaign.get("variables", {}))
        if company:
            variables.setdefault("company", company.get("name", ""))
            variables.setdefault("sender_email", company.get("sender_email", ""))
            variables.setdefault("website", company.get("website", "") or "ageautomation.in")

        if lead.get("is_individual_followup") and lead.get("pref_interval_days"):
            channel = lead.get("pref_channel", channel)

        print(f"[SCHEDULER] Recurring follow-up for lead {lead_id_str}")

    final_message = render_message(message_template, lead, variables)
    yes_link, no_link = build_response_links(lead_id_str)

    text_body = f"{final_message}\n\nYes: {yes_link}\nNo: {no_link}"
    html_body = build_email_html(final_message, yes_link, no_link)

    test_mode = campaign.get("test_mode", False)
    if test_mode and company:
        creator = usersCollection.find_one({"_id": ObjectId(company.get("created_by", ""))}) if company.get("created_by") else None
        test_recipient = creator.get("email") if creator else None
    else:
        test_recipient = None

    if channel in ["email", "both"]:
        if not sender_email:
            raise ValueError(f"Sender email not configured for company {lead['company_id']}")
        if not lead.get("email") and not test_recipient:
            raise ValueError(f"Missing email for lead {lead_id_str}")

        to_email = test_recipient or lead["email"]
        send_email_ses(
            to_email=to_email,
            subject=f"[TEST – {lead.get('name', lead_id_str)}] {subject}" if test_recipient else subject,
            body_text=text_body,
            sender_email=sender_email,
            html_body=html_body
        )
        print(f"[SCHEDULER] Email sent for lead {lead_id_str} → {to_email}")

    if channel in ["sms", "both"]:
        sms_enabled = company.get("sms_enabled", False) if company else False
        if company and sms_enabled and lead.get("phone"):
            template_id = (
                company.get("msg91_template_id_followup")
                or company.get("msg91_template_id_initial", "")
            )
            auth_key = company.get("msg91_api_key", "") or None
            send_sms_msg91(
                mobile=lead["phone"],
                template_id=template_id,
                variables={"name": lead.get("name", "there")},
                auth_key=auth_key,
            )
            print(f"[SCHEDULER] SMS sent for lead {lead_id_str}")
        else:
            print(f"[SCHEDULER] SMS skipped for lead {lead_id_str} — sms_enabled={sms_enabled}")

    msgCollection.insert_one({
        "lead_id": lead_id_str,
        "company_id": lead["company_id"],
        "campaign_id": str(campaign_id),
        "channel": channel,
        "message": final_message,
        "subject": subject,
        "yes_link": yes_link,
        "no_link": no_link,
        "sent_at": now,
        "status": "sent",
        "message_type": "followup",
        "step_number": current_step_number,
    })
    print(f"[SCHEDULER] Message logged for lead {lead_id_str}")

    send_status = {"email": "email sent", "sms": "sms sent", "both": "both sent"}.get(channel, "not sent")

    if is_sequence:
        next_step_number = int(current_step_number) + 1
        auto_run = campaign.get("auto_run", False)
        test_mode = campaign.get("test_mode", False)

        if next_step_number > total_steps:
            leadCollection.update_one(
                {"_id": lead["_id"]},
                {
                    "$set": {
                        "send_status": send_status,
                        "last_followup_sent_at": now,
                        "next_followup_at": None,
                        "current_step": next_step_number,
                        "sequence_complete": True,
                        "pending_approval": False,
                        "recommended_send_at": None,
                        "review_deadline_at": None,
                    },
                    "$inc": {"followup_count": 1}
                }
            )
            print(f"[SCHEDULER] Sequence complete for lead {lead_id_str}")
            return

        next_step_obj = get_step(campaign_id, next_step_number)
        next_gap = next_step_obj.get("gap_days", 3) if next_step_obj else 3

        if test_mode:
            next_followup_at = now + timedelta(minutes=1)
            leadCollection.update_one(
                {"_id": lead["_id"]},
                {
                    "$set": {
                        "send_status": send_status,
                        "last_followup_sent_at": now,
                        "next_followup_at": next_followup_at,
                        "current_step": next_step_number,
                        "sequence_complete": False,
                        "pending_approval": False,
                        "recommended_send_at": None,
                        "review_deadline_at": None,
                    },
                    "$inc": {"followup_count": 1}
                }
            )
            print(f"[SCHEDULER] [TEST] Step {current_step_number} done — next step {next_step_number} in 1 minute")
            return

        recommended_send_at = now + timedelta(days=next_gap)

        if auto_run:
            leadCollection.update_one(
                {"_id": lead["_id"]},
                {
                    "$set": {
                        "send_status": send_status,
                        "last_followup_sent_at": now,
                        "next_followup_at": recommended_send_at,
                        "current_step": next_step_number,
                        "sequence_complete": False,
                        "pending_approval": False,
                        "recommended_send_at": None,
                        "review_deadline_at": None,
                    },
                    "$inc": {"followup_count": 1}
                }
            )
            print(f"[SCHEDULER] Step {current_step_number} done — next step {next_step_number} scheduled on recommended timing")
            return

        review_deadline_at = now + timedelta(hours=24)
        wake_at = min(review_deadline_at, recommended_send_at)

        leadCollection.update_one(
            {"_id": lead["_id"]},
            {
                "$set": {
                    "send_status": send_status,
                    "last_followup_sent_at": now,
                    "next_followup_at": wake_at,
                    "current_step": next_step_number,
                    "sequence_complete": False,
                    "pending_approval": True,
                    "recommended_send_at": recommended_send_at,
                    "review_deadline_at": review_deadline_at,
                },
                "$inc": {"followup_count": 1}
            }
        )
        print(
            f"[SCHEDULER] Step {current_step_number} done — step {next_step_number} pending approval. "
            f"Review window ends at {review_deadline_at.isoformat()}, recommended send at {recommended_send_at.isoformat()}"
        )

    else:
        interval_days = campaign.get("interval_days", 2)
        if lead.get("is_individual_followup") and lead.get("pref_interval_days"):
            interval_days = lead.get("pref_interval_days")

        is_recurring = campaign.get("is_recurring", True)
        next_followup_at = now + timedelta(days=interval_days) if is_recurring else None

        leadCollection.update_one(
            {"_id": lead["_id"]},
            {
                "$set": {
                    "send_status": send_status,
                    "last_followup_sent_at": now,
                    "next_followup_at": next_followup_at,
                },
                "$inc": {"followup_count": 1}
            }
        )


def process_followups():
    now = datetime.utcnow()
    step_completions = {}

    try:
        due_leads = list(leadCollection.find({
            "next_followup_at": {"$ne": None, "$lte": now},
            "sequence_complete": {"$ne": True},
            "email_bounced": {"$ne": True},
        }))

        print(f"[SCHEDULER] Found {len(due_leads)} due leads at {now.isoformat()}")

        for lead in due_leads:
            if lead.get("response_status") in ["yes", "no"]:
                print(f"[SCHEDULER] Skipping lead {lead['_id']} — already responded")
                continue

            campaign_id = lead.get("campaign_id")
            if not campaign_id:
                print(f"[SCHEDULER] Skipping lead {lead['_id']} — no campaign_id")
                continue

            if isinstance(campaign_id, str):
                try:
                    campaign_id = ObjectId(campaign_id)
                except Exception:
                    print(f"[SCHEDULER] Invalid campaign_id for lead {lead['_id']}")
                    continue

            campaign = campCollection.find_one({"_id": campaign_id})
            if not campaign or not campaign.get("is_active", False):
                print(f"[SCHEDULER] Skipping lead {lead['_id']} — campaign inactive or missing")
                continue

            if campaign.get("is_sequence", False) and lead.get("pending_approval", False):
                recommended_send_at = _to_datetime(lead.get("recommended_send_at"))
                review_deadline_at = _to_datetime(lead.get("review_deadline_at"))

                try:
                    if recommended_send_at and now >= recommended_send_at:
                        leadCollection.update_one(
                            {"_id": lead["_id"]},
                            {
                                "$set": {
                                    "pending_approval": False,
                                    "review_deadline_at": None,
                                    "recommended_send_at": None,
                                    "next_followup_at": now,
                                }
                            }
                        )
                        lead["pending_approval"] = False
                        lead["next_followup_at"] = now
                        print(f"[SCHEDULER] Auto-continued on recommended timing for lead {lead['_id']}")

                    elif review_deadline_at and now >= review_deadline_at:
                        next_run = recommended_send_at or now
                        if next_run <= now:
                            leadCollection.update_one(
                                {"_id": lead["_id"]},
                                {
                                    "$set": {
                                        "pending_approval": False,
                                        "review_deadline_at": None,
                                        "recommended_send_at": None,
                                        "next_followup_at": now,
                                    }
                                }
                            )
                            lead["pending_approval"] = False
                            lead["next_followup_at"] = now
                            print(f"[SCHEDULER] Review window expired — sending now for lead {lead['_id']}")
                        else:
                            leadCollection.update_one(
                                {"_id": lead["_id"]},
                                {
                                    "$set": {
                                        "pending_approval": False,
                                        "review_deadline_at": None,
                                        "recommended_send_at": None,
                                        "next_followup_at": next_run,
                                    }
                                }
                            )
                            print(
                                f"[SCHEDULER] Review window expired — step remains on recommended timing "
                                f"for lead {lead['_id']} at {next_run.isoformat()}"
                            )
                            continue
                    else:
                        continue

                except Exception as e:
                    print(f"[SCHEDULER][PENDING APPROVAL ERROR] Lead {lead['_id']}: {str(e)}")
                    continue

            step_before = lead.get("current_step") if campaign.get("is_sequence") else None

            try:
                send_followup(lead, campaign)

                if step_before is not None:
                    cid = str(campaign_id)
                    if cid not in step_completions:
                        step_completions[cid] = set()
                    step_completions[cid].add(step_before)

            except ClientError as e:
                print(f"[SCHEDULER][AWS ERROR] Lead {lead['_id']}: {str(e)}")
            except Exception as e:
                print(f"[SCHEDULER][ERROR] Lead {lead['_id']}: {str(e)}")

        for campaign_id_str, completed_steps in step_completions.items():
            try:
                campaign_id = ObjectId(campaign_id_str)
                campaign = campCollection.find_one({"_id": campaign_id})

                if not campaign:
                    continue
                if campaign.get("auto_run", False):
                    continue

                notified_steps = set(campaign.get("notified_steps", []))

                for step_number in sorted(completed_steps):
                    if step_number in notified_steps:
                        continue

                    active_leads = list(leadCollection.find({
                        "campaign_id": {"$in": [campaign_id_str, campaign_id]},
                        "sequence_complete": {"$ne": True},
                        "response_status": {"$nin": ["yes", "no"]}
                    }))

                    all_past = all(
                        l.get("current_step", 1) > step_number
                        for l in active_leads
                    ) if active_leads else True

                    if all_past:
                        send_step_completion_notification(campaign_id, step_number)

            except Exception as e:
                print(f"[SCHEDULER][NOTIFICATION ERROR] {campaign_id_str}: {str(e)}")

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