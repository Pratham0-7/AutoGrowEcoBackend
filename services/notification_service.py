from bson import ObjectId
from db import compCollection, campCollection, leadCollection, stepCollection, usersCollection
from services.email_service import send_email_ses

BASE_URL = "https://ageautomation.in"


def send_step_completion_notification(campaign_id, step_number):
    try:
        if isinstance(campaign_id, str):
            campaign_id = ObjectId(campaign_id)

        campaign = campCollection.find_one({"_id": campaign_id})
        if not campaign:
            print(f"[NOTIFICATION] Campaign {campaign_id} not found")
            return

        company = compCollection.find_one({"_id": ObjectId(campaign.get("company_id"))})
        if not company:
            print(f"[NOTIFICATION] Company not found for campaign {campaign_id}")
            return

        # Look up owner email from users collection — comp doc has no owner_email field
        owner = usersCollection.find_one({"company_id": str(company["_id"])})
        owner_email = owner.get("email", "") if owner else ""

        if not owner_email:
            print(f"[NOTIFICATION] No owner email found for company {company['_id']}")
            return

        campaign_name = campaign.get("name", "Unnamed Campaign")
        campaign_id_str = str(campaign_id)

        all_leads = list(leadCollection.find({
            "campaign_id": {"$in": [campaign_id_str, campaign_id]}
        }))

        sent_count = sum(
            1 for l in all_leads
            if l.get("current_step", 1) > step_number or l.get("sequence_complete", False)
        )
        skipped_count = sum(1 for l in all_leads if l.get("send_status") == "skipped")
        replied_count = sum(
            1 for l in all_leads if l.get("response_status") in ["yes", "no"]
        )

        next_step_number = step_number + 1
        next_step = stepCollection.find_one({
            "campaign_id": campaign_id,
            "step_number": next_step_number
        })
        is_final_step = next_step is None

        if is_final_step:
            no_response_count = sum(
                1 for l in all_leads
                if l.get("sequence_complete", False)
                and l.get("response_status") not in ["yes", "no"]
            )
            subject = f"AGE — Sequence complete: {campaign_name}"
            body = f"""Your sequence "{campaign_name}" has completed all steps.

Final results:
- Total leads: {len(all_leads)}
- Replied: {replied_count}
- No response after full sequence: {no_response_count}

— AGE"""

        else:
            next_gap = next_step.get("gap_days", 3)
            approve_link = f"{BASE_URL}/dashboard/campaigns/{campaign_id_str}/approve/{next_step_number}"

            subject = f"AGE — Step {step_number} done: {campaign_name}"
            body = f"""Step {step_number} of "{campaign_name}" has been sent.

Results so far:
- Sent: {sent_count}
- Skipped (missing contact info): {skipped_count}
- Already replied: {replied_count}

Step {next_step_number} is ready and will send in {next_gap} days.

Review or approve before it sends:
{approve_link}

If you have already set this sequence to auto-run, no action is needed.

— AGE"""

        send_email_ses(
            to_email=owner_email,
            subject=subject,
            body_text=body,
            sender_email="noreply@ageautomation.in",
            html_body=None
        )
        print(f"[NOTIFICATION] Step {step_number} notification sent to {owner_email}")

        campCollection.update_one(
            {"_id": campaign_id},
            {"$addToSet": {"notified_steps": step_number}}
        )

    except Exception as e:
        print(f"[NOTIFICATION ERROR] {str(e)}")