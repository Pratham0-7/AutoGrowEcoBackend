from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta

from db import campCollection, stepCollection, leadCollection, compCollection

campaigns_bp = Blueprint("campaigns", __name__)

# Relative gap_days = days to wait after the PREVIOUS step was sent
# Absolute day offsets: 0, 3, 6, 9, 14, 19, 25, 32, 40, 50, 62, 75
#
# IMPORTANT:
# These steps are now TEMPLATE steps, not hardcoded AGE copy.
# The scheduler already calls render_message(message_template, lead, variables),
# so placeholders like {{your_name}} and {{call_to_action}} will be filled at send time.
DEFAULT_STEPS = [
    {
        "step_number": 1,
        "gap_days": 0,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Quick question about follow-up",
        "message": (
            "Hi {{name}},\n\n"
            "I am {{your_name}}.\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}}.\n\n"
            "The main problem we solve is {{main_problem}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, this is {{your_name}}. "
            "{{product_service}} "
            "We help with {{help_with}}. "
            "{{call_to_action}} Reply YES."
        ),
    },
    {
        "step_number": 2,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "email",
        "subject": "The cost of a missed follow-up",
        "message": (
            "Hi {{name}},\n\n"
            "A lot of teams do not lose leads because the leads are bad. "
            "They lose them because follow-up slows down, gets delayed, or stops.\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}}, and the main problem we solve is {{main_problem}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, many teams lose leads because follow-up gets inconsistent. "
            "{{product_service}} {{call_to_action}} Reply YES."
        ),
    },
    {
        "step_number": 3,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "What this looks like in practice",
        "message": (
            "Hi {{name}},\n\n"
            "Here is what this looks like in practice.\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}} so businesses do not keep losing opportunities because of {{main_problem}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, here is what this looks like in practice: "
            "{{product_service}} We help with {{help_with}}. "
            "{{call_to_action}} Reply YES."
        ),
    },
    {
        "step_number": 4,
        "gap_days": 3,
        "gap_label": "Recommended",
        "channel": "email",
        "subject": "One question",
        "message": (
            "Hi {{name}},\n\n"
            "Simple question: is {{main_problem}} something your team deals with today?\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, quick question: is {{main_problem}} something your team deals with? "
            "{{call_to_action}}"
        ),
    },
    {
        "step_number": 5,
        "gap_days": 5,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Why teams switch to this",
        "message": (
            "Hi {{name}},\n\n"
            "The teams that find this useful usually have the same issue: {{main_problem}}.\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}} without adding more manual work.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, teams usually use this when {{main_problem}} becomes a real issue. "
            "{{product_service}} {{call_to_action}}"
        ),
    },
    {
        "step_number": 6,
        "gap_days": 5,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Still relevant?",
        "message": (
            "Hi {{name}},\n\n"
            "I have reached out a few times, so I will keep this short.\n\n"
            "If {{main_problem}} is still relevant for your team, {{product_service}} may be useful.\n\n"
            "{{call_to_action}}\n\n"
            "If not, no worries at all.\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, still relevant? If {{main_problem}} is something your team is dealing with, "
            "{{product_service}} may help. Reply YES or NO."
        ),
    },
    {
        "step_number": 7,
        "gap_days": 6,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Checking back in",
        "message": (
            "Hi {{name}},\n\n"
            "Just checking back in in case the timing is better now.\n\n"
            "{{product_service}}\n\n"
            "We help with {{help_with}} and solve {{main_problem}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, checking back in. {{product_service}} "
            "We help with {{help_with}}. {{call_to_action}}"
        ),
    },
    {
        "step_number": 8,
        "gap_days": 7,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Something that might be useful",
        "message": (
            "Hi {{name}},\n\n"
            "Thought this might be useful to send over.\n\n"
            "{{product_service}}\n\n"
            "It is built for teams dealing with {{main_problem}} and needing help with {{help_with}}.\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, something that might be useful: {{product_service}} "
            "Built for teams dealing with {{main_problem}}. {{call_to_action}}"
        ),
    },
    {
        "step_number": 9,
        "gap_days": 8,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Direct question",
        "message": (
            "Hi {{name}},\n\n"
            "Direct question: is solving {{main_problem}} something you are actively looking at right now?\n\n"
            "{{product_service}}\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, direct question: are you actively looking for a way to solve {{main_problem}}? "
            "{{call_to_action}}"
        ),
    },
    {
        "step_number": 10,
        "gap_days": 10,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "One last thought",
        "message": (
            "Hi {{name}},\n\n"
            "One last thought.\n\n"
            "A lot of teams do not need more leads. They just need a better way to deal with {{main_problem}}.\n\n"
            "{{product_service}}\n\n"
            "{{call_to_action}}\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, one last thought: many teams do not need more leads, they just need a better way "
            "to solve {{main_problem}}. {{call_to_action}}"
        ),
    },
    {
        "step_number": 11,
        "gap_days": 12,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Closing the loop",
        "message": (
            "Hi {{name}},\n\n"
            "I will close the loop here.\n\n"
            "If {{main_problem}} ever becomes a bigger priority, {{product_service}} may be worth a look.\n\n"
            "You can always reach me here.\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{sender_email}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, closing the loop here. If solving {{main_problem}} becomes a priority, "
            "feel free to reach out. - {{your_name}}"
        ),
    },
    {
        "step_number": 12,
        "gap_days": 13,
        "gap_label": "Recommended",
        "channel": "both",
        "subject": "Last note",
        "message": (
            "Hi {{name}},\n\n"
            "This is my last note for now.\n\n"
            "{{product_service}}\n\n"
            "I reached out because I genuinely believe it can help teams dealing with {{main_problem}}.\n\n"
            "If that becomes relevant later, feel free to reply anytime.\n\n"
            "{{your_name}}\n"
            "{{signature_title}}\n"
            "{{website}}"
        ),
        "sms_message": (
            "Hi {{name}}, last note from me. If {{main_problem}} becomes a priority later, "
            "feel free to reach out. - {{your_name}}"
        ),
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


def _clean_variables(variables, company=None):
    """
    Normalizes sequence variables and provides safe defaults.
    These keys are what the templates above use.
    """
    variables = variables or {}

    company_name = company.get("name", "") if company else ""
    sender_email = company.get("sender_email", "") if company else ""
    website = company.get("website", "") if company else ""

    normalized = {
        "your_name": variables.get("your_name", "").strip(),
        "product_service": variables.get("product_service", "").strip(),
        "help_with": variables.get("help_with", "").strip(),
        "main_problem": variables.get("main_problem", "").strip(),
        "call_to_action": variables.get("call_to_action", "").strip(),
        "industry": variables.get("industry", "").strip(),
        "signature_title": variables.get("signature_title", "").strip(),
        "website": variables.get("website", "").strip() or website,
        "sender_email": variables.get("sender_email", "").strip() or sender_email,
        "company": variables.get("company", "").strip() or company_name,
    }

    # Reasonable defaults so blank forms do not produce ugly signatures.
    if not normalized["signature_title"]:
        normalized["signature_title"] = "Founder"
    if not normalized["website"]:
        normalized["website"] = "ageautomation.in"

    return normalized


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

        variables = _clean_variables(variables, company)
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
            "updated_at": now,
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
                    # Respect campaign-level selected channel if the step is not already more specific.
                    "channel": step.get("channel", channel),
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                }
            )

        stepCollection.insert_many(steps_to_insert)

        return (
            jsonify(
                {
                    "campaign_id": campaign_id_str,
                    "name": name,
                    "variables": variables,
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
            {"_id": ObjectId(campaign_id)},
            {"$set": {"auto_run": auto_run, "updated_at": datetime.utcnow()}},
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
        skipped_reasons = []

        for lead_id in lead_ids:
            try:
                lead = leadCollection.find_one({"_id": ObjectId(lead_id)})
                if not lead:
                    skipped += 1
                    skipped_reasons.append(
                        {"lead_id": lead_id, "reason": "Lead not found"}
                    )
                    continue

                already_active = (
                    lead.get("sequence_complete") is False
                    and lead.get("campaign_id")
                    and lead.get("response_status") not in ["yes", "no"]
                )
                if already_active:
                    skipped += 1
                    skipped_reasons.append(
                        {
                            "lead_id": lead_id,
                            "reason": "Lead already enrolled in another active sequence",
                        }
                    )
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
                skipped_reasons.append(
                    {"lead_id": lead_id, "reason": f"Exception: {str(e)}"}
                )
                continue

        message = (
            f"{enrolled} leads enrolled. Step 1 will send within 1 minute."
            if enrolled > 0
            else "No leads were enrolled."
        )

        return (
            jsonify(
                {
                    "enrolled": enrolled,
                    "skipped": skipped,
                    "skipped_reasons": skipped_reasons,
                    "message": message,
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

        company = compCollection.find_one({"_id": ObjectId(campaign["company_id"])})
        variables = _clean_variables(variables, company)

        campCollection.update_one(
            {"_id": ObjectId(campaign_id)},
            {"$set": {"variables": variables, "updated_at": datetime.utcnow()}},
        )

        return jsonify({"message": "Variables updated", "variables": variables}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500