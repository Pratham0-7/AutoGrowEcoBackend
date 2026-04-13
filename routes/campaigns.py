from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta

from db import campCollection, stepCollection, leadCollection, compCollection

campaigns_bp = Blueprint("campaigns", __name__)

# Relative gap_days = days to wait after the PREVIOUS step was sent
# Absolute day offsets: 0, 3, 6, 9, 14, 19, 25, 32, 40, 50, 62, 75
DEFAULT_STEPS = [
    {
        "step_number": 1, "gap_days": 0, "gap_label": "Recommended", "channel": "both",
        "subject": "Following up on your inquiry",
        "message": "Hi {{name}},\n\nI came across {{company}} and wanted to reach out directly.\n\nMost businesses we work with lose potential clients not because of bad leads — but because follow-up stops after the first or second message. The lead goes cold, someone else closes them.\n\n{{company_service}} helps fix that by automating your entire follow-up sequence so your team never have to chase manually again.\n\nWould you be open to a quick 15-minute call this week?\n\n{{sender_name}}\n{{company}}",
        "sms_message": "Hi {{name}}, came across {{company}} and thought {{company_service}} could help. We automate follow-ups so no lead goes cold. Worth a quick call? Reply YES."
    },
    {
        "step_number": 2, "gap_days": 3, "gap_label": "Recommended", "channel": "email",
        "subject": "Just checking in",
        "message": "Hi {{name}},\n\nJust wanted to make sure my previous message reached you.\n\nWe have helped several businesses in your space with {{pain_point}} — happy to share how if that is useful.\n\nStill open to a quick call?\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, just following up on my earlier message. Would love to connect. Reply YES for a quick call."
    },
    {
        "step_number": 3, "gap_days": 3, "gap_label": "Recommended", "channel": "both",
        "subject": "Something you might find useful",
        "message": "Hi {{name}},\n\nOne thing we hear from most businesses we talk to — {{common_pain_point}}.\n\nWe built {{company_service}} specifically to fix that. Happy to show you how in 10 minutes.\n\nInterested?\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, most businesses we work with struggle with {{common_pain_point}}. We fix that. Worth a 10-minute chat? Reply YES."
    },
    {
        "step_number": 4, "gap_days": 3, "gap_label": "Recommended", "channel": "email",
        "subject": "Quick question",
        "message": "Hi {{name}},\n\nAre you still looking for a solution to {{pain_point}}?\n\nIf yes — let us talk. If the timing is not right, just let me know and I will follow up later.\n\nEither way, happy to help whenever you are ready.\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, still looking for help with {{pain_point}}? Reply YES for a quick call or NO if timing is not right."
    },
    {
        "step_number": 5, "gap_days": 5, "gap_label": "Recommended", "channel": "both",
        "subject": "How we helped a business just like yours",
        "message": "Hi {{name}},\n\nOne of our recent clients was dealing with {{pain_point}}. Within {{timeframe}} of working with us they achieved {{result}}.\n\nWe would love to do the same for you.\n\nStill open to a quick chat?\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, we recently helped a business achieve {{result}} in {{timeframe}}. Happy to share how. Interested? Reply YES."
    },
    {
        "step_number": 6, "gap_days": 5, "gap_label": "Recommended", "channel": "both",
        "subject": "Came across something that made me think of you",
        "message": "Hi {{name}},\n\nI know we have been in touch a few times. I keep reaching out because I genuinely think we can help.\n\nMost businesses we talk to in your space are dealing with {{industry_pain_point}} right now.\n\nWould it make sense to have a 10-minute conversation — no pressure, just an overview?\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, businesses in your space are dealing with {{industry_pain_point}} right now. We help fix that. Worth 10 minutes? Reply YES."
    },
    {
        "step_number": 7, "gap_days": 6, "gap_label": "Recommended", "channel": "both",
        "subject": "What we are seeing in your industry right now",
        "message": "Hi {{name}},\n\nWanted to share something relevant — {{industry_insight}}.\n\nBusinesses that address this early tend to {{positive_outcome}}. Those that do not usually {{negative_outcome}}.\n\nHappy to share what we are doing about it for our clients.\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, {{industry_insight_short}}. Happy to share how we are helping clients deal with this. Interested? Reply YES."
    },
    {
        "step_number": 8, "gap_days": 7, "gap_label": "Recommended", "channel": "both",
        "subject": "The most common concern we hear",
        "message": "Hi {{name}},\n\nThe most common thing we hear from businesses before they start working with us is — {{common_objection}}.\n\nCompletely fair. Here is how we address that: {{objection_response}}.\n\nHappy to walk you through it. 15 minutes is all it takes.\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, a lot of businesses worry about {{common_objection_short}}. We have a clear answer for that. Want to hear it? Reply YES."
    },
    {
        "step_number": 9, "gap_days": 8, "gap_label": "Recommended", "channel": "both",
        "subject": "Before I stop reaching out",
        "message": "Hi {{name}},\n\nI do not want to keep filling your inbox if the timing is not right.\n\nBut before I stop — is there anything specific holding you back? Budget, timing, something else?\n\nSometimes a 10-minute conversation clears things up.\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, before I stop reaching out — is anything holding you back? Just reply and let me know. Happy to help."
    },
    {
        "step_number": 10, "gap_days": 10, "gap_label": "Recommended", "channel": "both",
        "subject": "No strings — just a quick offer",
        "message": "Hi {{name}},\n\nHappy to put together a no-obligation overview of how {{company_service}} could work specifically for {{their_company}} — just so you have something concrete to look at.\n\nNo calls, no pressure. Just useful information you can review at your own pace.\n\nWant me to send that across?\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, want a no-obligation overview of how we can help {{their_company}}? No calls, no pressure. Reply YES and I will send it."
    },
    {
        "step_number": 11, "gap_days": 12, "gap_label": "Recommended", "channel": "both",
        "subject": "Closing the loop",
        "message": "Hi {{name}},\n\nI have reached out a few times and I completely understand if the timing is not right or you have already found what you were looking for.\n\nIf you are still open to it — even just a quick conversation — I would love to reconnect. A lot has changed since we first reached out.\n\nJust reply to this email and we will take it from there.\n\n{{sender_name}}",
        "sms_message": "Hi {{name}}, still open to a quick conversation? A lot has changed since we first reached out. Just reply and we will reconnect."
    },
    {
        "step_number": 12, "gap_days": 13, "gap_label": "Recommended", "channel": "both",
        "subject": "Signing off — but the door is always open",
        "message": "Hi {{name}},\n\nThis will be my last message for a while — I do not want to keep reaching out if it is not the right time.\n\nIf you ever want to revisit {{company_service}}, you know where to find us. We would love to help whenever you are ready.\n\nWishing you all the best.\n\n{{sender_name}}\n{{company}}",
        "sms_message": "Hi {{name}}, this is my last message for now. If you ever want to revisit {{company_service}}, we are here. Wishing you all the best."
    }
]


def serialize_step(step):
    return {
        "campaign_id": str(step.get("campaign_id")) if step.get("campaign_id") else None,
        "company_id": step.get("company_id"),
        "step_number": step.get("step_number"),
        "gap_days": step.get("gap_days", 0),
        "gap_label": step.get("gap_label", "Recommended"),
        "channel": step.get("channel", "both"),
        "subject": step.get("subject", ""),
        "message": step.get("message", ""),
        "sms_message": step.get("sms_message", ""),
        "status": step.get("status", "pending"),
        "created_at": step.get("created_at").isoformat() if step.get("created_at") else None,
        "updated_at": step.get("updated_at").isoformat() if step.get("updated_at") else None,
    }


@campaigns_bp.route("/campaigns/sequence/<company_id>", methods=["GET"])
def list_sequence_campaigns(company_id):
    try:
        raw = list(campCollection.find(
            {"company_id": company_id, "is_sequence": True},
            sort=[("created_at", -1)]
        ))
        result = []
        for c in raw:
            cid = c["_id"]
            cid_str = str(cid)
            lead_count = leadCollection.count_documents({
                "campaign_id": {"$in": [cid_str, cid]}
            })
            result.append({
                "_id": cid_str,
                "name": c.get("name", "Unnamed"),
                "channel": c.get("channel", "both"),
                "auto_run": c.get("auto_run", False),
                "test_mode": c.get("test_mode", False),
                "variables": c.get("variables", {}),
                "created_at": c["created_at"].isoformat() if c.get("created_at") else "",
                "lead_count": lead_count,
            })
        return jsonify({"sequences": result}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/sequence", methods=["POST"])
def create_sequence_campaign():
    try:
        data = request.json or {}

        company_id = data.get("company_id", "").strip()
        name = data.get("name", "").strip()
        channel = data.get("channel", "both")
        variables = data.get("variables", {})
        test_mode = bool(data.get("test_mode", False))

        if not company_id:
            return jsonify({"error": "company_id is required"}), 400
        if not name:
            return jsonify({"error": "Campaign name is required"}), 400
        if channel not in ["email", "sms", "both"]:
            return jsonify({"error": "channel must be email, sms, or both"}), 400

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        now = datetime.utcnow()

        campaign = {
            "company_id": company_id,
            "name": name,
            "channel": channel,
            "is_active": True,
            "is_sequence": True,
            "auto_run": False,
            "test_mode": test_mode,
            "variables": variables,
            "notified_steps": [],
            "created_at": now
        }

        result = campCollection.insert_one(campaign)
        campaign_id = result.inserted_id
        campaign_id_str = str(campaign_id)

        steps_to_insert = []
        for step in DEFAULT_STEPS:
            steps_to_insert.append({
                **step,
                "campaign_id": campaign_id,
                "company_id": company_id,
                "status": "pending",
                "created_at": now
            })

        stepCollection.insert_many(steps_to_insert)

        return jsonify({
            "campaign_id": campaign_id_str,
            "name": name,
            "message": "Sequence campaign created with 12 default steps"
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/steps", methods=["GET"])
def get_campaign_steps(campaign_id):
    try:
        raw_steps = list(stepCollection.find(
            {"campaign_id": ObjectId(campaign_id)}
        ).sort("step_number", 1))

        steps = [serialize_step(s) for s in raw_steps]
        return jsonify({"steps": steps}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/steps/<int:step_number>", methods=["PUT"])
def update_campaign_step(campaign_id, step_number):
    try:
        step = stepCollection.find_one({
            "campaign_id": ObjectId(campaign_id),
            "step_number": step_number
        })
        if not step:
            return jsonify({"error": "Step not found"}), 404

        data = request.json or {}
        update_fields = {}

        if "gap_days" in data:
            update_fields["gap_days"] = int(data["gap_days"])
            update_fields["gap_label"] = "Custom"
        if "message" in data:
            update_fields["message"] = data["message"]
        if "sms_message" in data:
            update_fields["sms_message"] = data["sms_message"]
        if "subject" in data:
            update_fields["subject"] = data["subject"]
        if "channel" in data:
            if data["channel"] not in ["email", "sms", "both"]:
                return jsonify({"error": "channel must be email, sms, or both"}), 400
            update_fields["channel"] = data["channel"]

        if not update_fields:
            return jsonify({"error": "No valid fields to update"}), 400

        update_fields["updated_at"] = datetime.utcnow()

        stepCollection.update_one(
            {"campaign_id": ObjectId(campaign_id), "step_number": step_number},
            {"$set": update_fields}
        )

        updated = stepCollection.find_one(
            {"campaign_id": ObjectId(campaign_id), "step_number": step_number}
        )
        return jsonify({"step": serialize_step(updated)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/auto-run", methods=["PATCH"])
def toggle_auto_run(campaign_id):
    try:
        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        data = request.json or {}
        auto_run = bool(data.get("auto_run", False))
        now = datetime.utcnow()

        if auto_run:
            leads = list(leadCollection.find({
                "campaign_id": {"$in": [campaign_id, ObjectId(campaign_id)]},
                "sequence_complete": {"$ne": True},
                "response_status": {"$nin": ["yes", "no"]}
            }))

            for lead in leads:
                if lead.get("pending_approval"):
                    recommended_send_at = lead.get("recommended_send_at")
                    if isinstance(recommended_send_at, datetime):
                        next_followup_at = recommended_send_at if recommended_send_at > now else now
                    else:
                        next_followup_at = now

                    leadCollection.update_one(
                        {"_id": lead["_id"]},
                        {"$set": {
                            "pending_approval": False,
                            "review_deadline_at": None,
                            "recommended_send_at": None,
                            "next_followup_at": next_followup_at,
                        }}
                    )

        campCollection.update_one(
            {"_id": ObjectId(campaign_id)},
            {"$set": {"auto_run": auto_run}}
        )

        return jsonify({
            "auto_run": auto_run,
            "message": "Auto-run enabled for all remaining stages" if auto_run else "Auto-run disabled"
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/enroll", methods=["POST"])
def enroll_leads(campaign_id):
    try:
        data = request.json or {}
        lead_ids = data.get("lead_ids", [])

        if not lead_ids:
            return jsonify({"error": "lead_ids is required"}), 400

        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404
        if not campaign.get("is_sequence"):
            return jsonify({"error": "This campaign is not a sequence"}), 400

        now = datetime.utcnow()
        enrolled = 0
        skipped = 0

        for lead_id in lead_ids:
            try:
                lead = leadCollection.find_one({"_id": ObjectId(lead_id)})
                if not lead:
                    skipped += 1
                    continue

                already_active = (
                    lead.get("sequence_complete") is False
                    and lead.get("campaign_id")
                    and lead.get("response_status") not in ["yes", "no"]
                )
                if already_active:
                    skipped += 1
                    continue

                leadCollection.update_one(
                    {"_id": ObjectId(lead_id)},
                    {"$set": {
                        "campaign_id": ObjectId(campaign_id),
                        "current_step": 1,
                        "sequence_complete": False,
                        "next_followup_at": now,
                        "response_status": "pending",
                        "pending_approval": False,
                        "recommended_send_at": None,
                        "review_deadline_at": None,
                    }}
                )
                enrolled += 1

            except Exception as e:
                print(f"[ENROLL] Error enrolling lead {lead_id}: {e}")
                skipped += 1
                continue

        return jsonify({
            "enrolled": enrolled,
            "skipped": skipped,
            "message": f"{enrolled} leads enrolled. Step 1 will send within 1 minute."
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/approve/<int:step_number>", methods=["POST"])
def approve_step(campaign_id, step_number):
    try:
        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        step = stepCollection.find_one({
            "campaign_id": ObjectId(campaign_id),
            "step_number": step_number
        })
        if not step:
            return jsonify({"error": f"Step {step_number} not found"}), 404

        data = request.json or {}
        mode = data.get("mode", "recommended")
        custom_gap_days = data.get("custom_gap_days", None)

        if mode not in ["send_now", "recommended", "custom"]:
            return jsonify({"error": "mode must be send_now, recommended, or custom"}), 400

        if mode == "custom":
            if custom_gap_days is None:
                return jsonify({"error": "custom_gap_days is required for custom mode"}), 400
            try:
                custom_gap_days = int(custom_gap_days)
            except Exception:
                return jsonify({"error": "custom_gap_days must be an integer"}), 400
            if custom_gap_days < 0:
                return jsonify({"error": "custom_gap_days must be 0 or more"}), 400

        now = datetime.utcnow()

        paused_leads = list(leadCollection.find({
            "campaign_id": {"$in": [campaign_id, ObjectId(campaign_id)]},
            "current_step": step_number,
            "pending_approval": True,
            "sequence_complete": {"$ne": True},
            "response_status": {"$nin": ["yes", "no"]}
        }))

        approved = 0
        scheduled_for = None

        for lead in paused_leads:
            if mode == "send_now":
                next_followup_at = now

            elif mode == "recommended":
                recommended_send_at = lead.get("recommended_send_at")
                if isinstance(recommended_send_at, datetime):
                    next_followup_at = recommended_send_at if recommended_send_at > now else now
                else:
                    next_followup_at = now

            else:  # custom
                next_followup_at = now + timedelta(days=custom_gap_days)

            leadCollection.update_one(
                {"_id": lead["_id"]},
                {"$set": {
                    "pending_approval": False,
                    "review_deadline_at": None,
                    "recommended_send_at": None,
                    "next_followup_at": next_followup_at,
                }}
            )
            approved += 1
            scheduled_for = next_followup_at

        return jsonify({
            "approved": approved,
            "mode": mode,
            "next_send_at": scheduled_for.isoformat() if scheduled_for else None,
            "message": f"Step {step_number} approved for {approved} leads"
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/variables", methods=["PUT"])
def update_variables(campaign_id):
    try:
        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        data = request.json or {}
        variables = data.get("variables", {})

        if not isinstance(variables, dict):
            return jsonify({"error": "variables must be an object"}), 400

        campCollection.update_one(
            {"_id": ObjectId(campaign_id)},
            {"$set": {"variables": variables, "updated_at": datetime.utcnow()}}
        )

        return jsonify({"message": "Variables updated", "variables": variables}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500