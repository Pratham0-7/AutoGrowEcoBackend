from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta

from db import campCollection, stepCollection, leadCollection, compCollection

campaigns_bp = Blueprint("campaigns", __name__)

# Relative gap_days = days to wait after the PREVIOUS step was sent
# Absolute day offsets: 0, 3, 6, 9, 14, 19, 25, 32, 40, 50, 62, 75
DEFAULT_STEPS = [
    {
        "step_number": 1,
        "gap_days": 0,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Quick question about follow-up",
        "message": "Hi {{name}},\n\nI am Pratham, founder of Automated Growth Ecosystem (AGE).\n\nWe help businesses stay consistent with follow-up so potential clients do not go cold after the first or second message.\n\nMost teams do not lose leads because the leads are bad. They lose them because follow-up slows down, gets delayed, or stops completely.\n\nThat is exactly what AGE is built to solve. We automate follow-up across email and SMS so your team can stay on top of every lead without manually chasing each one.\n\nWould you be open to a quick 15-minute call this week?\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, this is Pratham from AGE. We help businesses automate follow-ups so no lead goes cold. Open to a quick call? Reply YES.",
    },
    {
        "step_number": 2,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "email",
        "subject": "The cost of a missed follow-up",
        "message": "Hi {{name}},\n\nA stat worth knowing: 80% of sales require 5 or more follow-ups, but 44% of salespeople stop after just one.\n\nThat gap is where most businesses lose revenue. Not to bad leads or bad pitches, but to inconsistent follow-up.\n\nAGE helps close that gap automatically.\n\nHappy to show you how it works. Would a quick call make sense?\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, 80% of sales need 5+ follow-ups, but most teams stop after one. AGE helps fix that automatically. Interested? Reply YES.",
    },
    {
        "step_number": 3,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "What this looks like in practice",
        "message": "Hi {{name}},\n\nHere is what AGE actually does. When a lead comes in, it automatically sends a structured follow-up sequence across email and SMS for up to 75 days.\n\nEvery message is pre-written, timed, and personalised. The sequence stops as soon as the lead replies.\n\nYour team does not have to keep chasing manually. They just handle the replies that come in.\n\nWorth 10 minutes to see it in action?\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, AGE runs a 75-day follow-up sequence automatically. Your team just handles the replies. Worth 10 minutes? Reply YES.",
    },
    {
        "step_number": 4,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "email",
        "subject": "One question",
        "message": "Hi {{name}},\n\nSimple question. When a lead enquires and does not reply to the first message, what happens next?\n\nFor most businesses, the honest answer is not much. The team follows up once or twice and then moves on.\n\nAGE makes sure that does not happen. Every lead gets followed up with consistently, for as long as it takes.\n\nOpen to a quick conversation?\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, what happens when a lead goes quiet after the first message? AGE makes sure they do not just disappear. Reply YES to learn more.",
    },
    {
        "step_number": 5,
        "gap_days": 5,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Why teams switch to AGE",
        "message": "Hi {{name}},\n\nThe businesses that come to us usually have the same story: good leads coming in, a sales team that is stretched, and follow-up that gets inconsistent the busier things get.\n\nAGE removes follow-up from the daily to-do list. It runs in the background, keeps every lead warm, and only brings a lead back to your team when they respond.\n\nIf that sounds familiar, it is worth a conversation.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, businesses use AGE when follow-up gets inconsistent as the team grows. It runs automatically so nothing slips. Interested? Reply YES.",
    },
    {
        "step_number": 6,
        "gap_days": 5,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Still relevant?",
        "message": "Hi {{name}},\n\nI have reached out a few times, so I will keep this short.\n\nIf inconsistent follow-up is something your team deals with, AGE is worth looking at. If it is not a priority right now, just let me know and I will stop reaching out.\n\nEither way, happy to hear from you.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, last check-in. If follow-up consistency is something your team deals with, AGE can help. If not, just reply NO and I will stop.",
    },
    {
        "step_number": 7,
        "gap_days": 25,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Checking back in",
        "message": "Hi {{name}},\n\nIt has been a while since I last reached out, so I wanted to check back in case the timing is better now.\n\nAGE helps businesses automate follow-up so leads do not go cold across email and SMS, without adding manual work for the team.\n\nIf this is relevant now, happy to jump on a quick call.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, Pratham from AGE checking back in. If follow-up automation is relevant now, happy to connect. Reply YES.",
    },
    {
        "step_number": 8,
        "gap_days": 7,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Something that might be useful",
        "message": "Hi {{name}},\n\nI put together a quick overview of how AGE works for businesses in your space, including how the sequence is structured, what gets automated, and what your team actually sees.\n\nNo call needed. If you want me to send it across, just reply and I will.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, I can send a quick overview of how AGE works. No call needed. Just reply YES and I will send it across.",
    },
    {
        "step_number": 9,
        "gap_days": 8,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Direct question",
        "message": "Hi {{name}},\n\nIs follow-up automation something you are actively looking at, or is the timing just not right?\n\nA one-word reply would genuinely help me understand whether it makes sense to stay in touch.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, quick one. Is follow-up automation something you are actively exploring right now? Just reply YES or NO.",
    },
    {
        "step_number": 10,
        "gap_days": 10,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "One last thought",
        "message": "Hi {{name}},\n\nMost businesses we talk to are not looking for more leads. They just want to make sure the ones they already have get followed up with properly.\n\nIf that is the situation at {{company}}, AGE is a straightforward fix.\n\nHappy to walk you through it whenever the time is right.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, most teams do not need more leads, just better follow-up on the ones they already have. AGE handles that. Worth a chat? Reply YES.",
    },
    {
        "step_number": 11,
        "gap_days": 12,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Closing the loop",
        "message": "Hi {{name}},\n\nI will close the loop here.\n\nIf follow-up automation ever becomes a priority, feel free to reach out anytime at pratham@ageautomation.in or ageautomation.in.\n\nWishing you and the team well.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, closing the loop. If follow-up automation becomes a priority, feel free to reach out anytime. - Pratham, AGE",
    },
    {
        "step_number": 12,
        "gap_days": 13,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Last note",
        "message": "Hi {{name}},\n\nThis is my last message for now.\n\nI reached out because I genuinely believe AGE can help businesses that are losing opportunities due to inconsistent follow-up.\n\nIf that becomes relevant for you later, you know where to find us.\n\nAll the best.\n\nPratham\nFounder, Automated Growth Ecosystem (AGE)\nageautomation.in",
        "sms_message": "Hi {{name}}, last note from me. If you ever want to stop losing leads to missed follow-ups, AGE is built for that. ageautomation.in - Pratham",
    },
]

def serialize_step(step):
    return {
        "campaign_id": (
            str(step.get("campaign_id")) if step.get("campaign_id") else None
        ),
        "company_id": step.get("company_id"),
        "step_number": step.get("step_number"),
        "gap_days": step.get("gap_days", 0),
        "gap_label": step.get("gap_label", "Recommended"),
        "channel": step.get("channel", "both"),
        "subject": step.get("subject", ""),
        "message": step.get("message", ""),
        "sms_message": step.get("sms_message", ""),
        "status": step.get("status", "pending"),
        "created_at": (
            step.get("created_at").isoformat() if step.get("created_at") else None
        ),
        "updated_at": (
            step.get("updated_at").isoformat() if step.get("updated_at") else None
        ),
    }


@campaigns_bp.route("/campaigns/sequence/<company_id>", methods=["GET"])
def list_sequence_campaigns(company_id):
    try:
        raw = list(
            campCollection.find(
                {"company_id": company_id, "is_sequence": True},
                sort=[("created_at", -1)],
            )
        )
        result = []
        for c in raw:
            cid = c["_id"]
            cid_str = str(cid)
            lead_count = leadCollection.count_documents(
                {"campaign_id": {"$in": [cid_str, cid]}}
            )
            result.append(
                {
                    "_id": cid_str,
                    "name": c.get("name", "Unnamed"),
                    "channel": c.get("channel", "both"),
                    "auto_run": c.get("auto_run", False),
                    "test_mode": c.get("test_mode", False),
                    "variables": c.get("variables", {}),
                    "created_at": (
                        c["created_at"].isoformat() if c.get("created_at") else ""
                    ),
                    "lead_count": lead_count,
                }
            )
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
            "created_at": now,
        }

        result = campCollection.insert_one(campaign)
        campaign_id = result.inserted_id
        campaign_id_str = str(campaign_id)

        steps_to_insert = []
        for step in DEFAULT_STEPS:
            steps_to_insert.append(
                {
                    **step,
                    "campaign_id": campaign_id,
                    "company_id": company_id,
                    "status": "pending",
                    "created_at": now,
                }
            )

        stepCollection.insert_many(steps_to_insert)

        return (
            jsonify(
                {
                    "campaign_id": campaign_id_str,
                    "name": name,
                    "message": "Sequence campaign created with 12 default steps",
                }
            ),
            201,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/steps", methods=["GET"])
def get_campaign_steps(campaign_id):
    try:
        raw_steps = list(
            stepCollection.find({"campaign_id": ObjectId(campaign_id)}).sort(
                "step_number", 1
            )
        )

        steps = [serialize_step(s) for s in raw_steps]
        return jsonify({"steps": steps}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>/steps/<int:step_number>", methods=["PUT"])
def update_campaign_step(campaign_id, step_number):
    try:
        step = stepCollection.find_one(
            {"campaign_id": ObjectId(campaign_id), "step_number": step_number}
        )
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
            {"$set": update_fields},
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
            leads = list(
                leadCollection.find(
                    {
                        "campaign_id": {"$in": [campaign_id, ObjectId(campaign_id)]},
                        "sequence_complete": {"$ne": True},
                        "response_status": {"$nin": ["yes", "no"]},
                    }
                )
            )

            for lead in leads:
                if lead.get("pending_approval"):
                    recommended_send_at = lead.get("recommended_send_at")
                    if isinstance(recommended_send_at, datetime):
                        next_followup_at = (
                            recommended_send_at if recommended_send_at > now else now
                        )
                    else:
                        next_followup_at = now

                    leadCollection.update_one(
                        {"_id": lead["_id"]},
                        {
                            "$set": {
                                "pending_approval": False,
                                "review_deadline_at": None,
                                "recommended_send_at": None,
                                "next_followup_at": next_followup_at,
                            }
                        },
                    )

        campCollection.update_one(
            {"_id": ObjectId(campaign_id)}, {"$set": {"auto_run": auto_run}}
        )

        return (
            jsonify(
                {
                    "auto_run": auto_run,
                    "message": (
                        "Auto-run enabled for all remaining stages"
                        if auto_run
                        else "Auto-run disabled"
                    ),
                }
            ),
            200,
        )

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
                    {
                        "$set": {
                            "campaign_id": ObjectId(campaign_id),
                            "current_step": 1,
                            "sequence_complete": False,
                            "next_followup_at": now,
                            "response_status": "pending",
                            "pending_approval": False,
                            "recommended_send_at": None,
                            "review_deadline_at": None,
                        }
                    },
                )
                enrolled += 1

            except Exception as e:
                print(f"[ENROLL] Error enrolling lead {lead_id}: {e}")
                skipped += 1
                continue

        return (
            jsonify(
                {
                    "enrolled": enrolled,
                    "skipped": skipped,
                    "message": f"{enrolled} leads enrolled. Step 1 will send within 1 minute.",
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route(
    "/campaigns/<campaign_id>/approve/<int:step_number>", methods=["POST"]
)
def approve_step(campaign_id, step_number):
    try:
        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        step = stepCollection.find_one(
            {"campaign_id": ObjectId(campaign_id), "step_number": step_number}
        )
        if not step:
            return jsonify({"error": f"Step {step_number} not found"}), 404

        data = request.json or {}
        mode = data.get("mode", "recommended")
        custom_gap_days = data.get("custom_gap_days", None)

        if mode not in ["send_now", "recommended", "custom"]:
            return (
                jsonify({"error": "mode must be send_now, recommended, or custom"}),
                400,
            )

        if mode == "custom":
            if custom_gap_days is None:
                return (
                    jsonify({"error": "custom_gap_days is required for custom mode"}),
                    400,
                )
            try:
                custom_gap_days = int(custom_gap_days)
            except Exception:
                return jsonify({"error": "custom_gap_days must be an integer"}), 400
            if custom_gap_days < 0:
                return jsonify({"error": "custom_gap_days must be 0 or more"}), 400

        now = datetime.utcnow()

        paused_leads = list(
            leadCollection.find(
                {
                    "campaign_id": {"$in": [campaign_id, ObjectId(campaign_id)]},
                    "current_step": step_number,
                    "pending_approval": True,
                    "sequence_complete": {"$ne": True},
                    "response_status": {"$nin": ["yes", "no"]},
                }
            )
        )

        approved = 0
        scheduled_for = None

        for lead in paused_leads:
            if mode == "send_now":
                next_followup_at = now

            elif mode == "recommended":
                recommended_send_at = lead.get("recommended_send_at")
                if isinstance(recommended_send_at, datetime):
                    next_followup_at = (
                        recommended_send_at if recommended_send_at > now else now
                    )
                else:
                    next_followup_at = now

            else:  # custom
                next_followup_at = now + timedelta(days=custom_gap_days)

            leadCollection.update_one(
                {"_id": lead["_id"]},
                {
                    "$set": {
                        "pending_approval": False,
                        "review_deadline_at": None,
                        "recommended_send_at": None,
                        "next_followup_at": next_followup_at,
                    }
                },
            )
            approved += 1
            scheduled_for = next_followup_at

        return (
            jsonify(
                {
                    "approved": approved,
                    "mode": mode,
                    "next_send_at": (
                        scheduled_for.isoformat() if scheduled_for else None
                    ),
                    "message": f"Step {step_number} approved for {approved} leads",
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaigns_bp.route("/campaigns/<campaign_id>", methods=["DELETE"])
def delete_sequence(campaign_id):
    try:
        campaign = campCollection.find_one({"_id": ObjectId(campaign_id)})
        if not campaign:
            return jsonify({"error": "Campaign not found"}), 404

        leadCollection.update_many(
            {"campaign_id": {"$in": [campaign_id, ObjectId(campaign_id)]}},
            {
                "$unset": {
                    "campaign_id": "",
                    "current_step": "",
                    "sequence_complete": "",
                    "next_followup_at": "",
                    "pending_approval": "",
                    "recommended_send_at": "",
                    "review_deadline_at": "",
                }
            },
        )

        stepCollection.delete_many({"campaign_id": ObjectId(campaign_id)})
        campCollection.delete_one({"_id": ObjectId(campaign_id)})

        return jsonify({"message": "Sequence deleted and all leads unenrolled"}), 200

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
            {"$set": {"variables": variables, "updated_at": datetime.utcnow()}},
        )

        return jsonify({"message": "Variables updated", "variables": variables}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
