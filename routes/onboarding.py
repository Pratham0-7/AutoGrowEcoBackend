from flask import Blueprint, request, jsonify
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from db import usersCollection, compCollection

onboarding_bp = Blueprint("onboarding", __name__)


def safe_objectid(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def get_company_by_user(user):
    company_id = user.get("company_id")
    if not company_id:
        return None

    obj_id = safe_objectid(company_id)
    if not obj_id:
        return None

    return compCollection.find_one({"_id": obj_id})


@onboarding_bp.route("/sync_clerk_user", methods=["POST"])
def sync_clerk_user():
    try:
        data = request.json or {}
        print("[ONBOARDING] sync_clerk_user payload:", data, flush=True)

        clerk_user_id = data.get("clerk_user_id")
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()

        if not clerk_user_id or not email:
            return jsonify({"error": "clerk_user_id and email are required"}), 400

        # 1. First preference: exact Clerk match
        existing_user = usersCollection.find_one({"clerk_user_id": clerk_user_id})

        # 2. Fallback: same email, different/new Clerk ID
        if not existing_user:
            existing_user = usersCollection.find_one({"email": email})

            if existing_user:
                usersCollection.update_one(
                    {"_id": existing_user["_id"]},
                    {
                        "$set": {
                            "clerk_user_id": clerk_user_id,
                            "name": name or existing_user.get("name", ""),
                            "email": email,
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
                existing_user = usersCollection.find_one({"_id": existing_user["_id"]})

        # 3. If found, return it
        if existing_user:
            company = get_company_by_user(existing_user)

            return jsonify({
                "message": "User already synced",
                "user_id": str(existing_user["_id"]),
                "clerk_user_id": existing_user.get("clerk_user_id"),
                "name": existing_user.get("name", ""),
                "email": existing_user.get("email", ""),
                "company_id": existing_user.get("company_id"),
                "company_name": company.get("name", "") if company else existing_user.get("company_name", ""),
                "role": existing_user.get("role", "admin"),
                "onboarding_completed": bool(existing_user.get("company_id")) or existing_user.get("onboarding_completed", False)
            }), 200

        # 4. Otherwise create brand new user
        user_doc = {
            "clerk_user_id": clerk_user_id,
            "name": name,
            "email": email,
            "company_id": None,
            "company_name": None,
            "role": "admin",
            "onboarding_completed": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        result = usersCollection.insert_one(user_doc)

        return jsonify({
            "message": "User synced successfully",
            "user_id": str(result.inserted_id),
            "clerk_user_id": clerk_user_id,
            "name": name,
            "email": email,
            "company_id": None,
            "company_name": "",
            "role": "admin",
            "onboarding_completed": False
        }), 201

    except DuplicateKeyError as e:
        print("[ONBOARDING][SYNC DUPLICATE ERROR]", str(e), flush=True)
        return jsonify({"error": "Duplicate user detected. Please retry."}), 409
    except Exception as e:
        print("[ONBOARDING][SYNC ERROR]", str(e), flush=True)
        return jsonify({"error": str(e)}), 500


@onboarding_bp.route("/complete_onboarding", methods=["POST"])
def complete_onboarding():
    try:
        data = request.json or {}
        print("[ONBOARDING] complete_onboarding payload:", data, flush=True)

        clerk_user_id = data.get("clerk_user_id")
        company_name = (data.get("company_name") or "").strip()
        sender_email = (data.get("sender_email") or "").strip().lower()
        sender_phone = (data.get("sender_phone") or "").strip()

        if not clerk_user_id:
            return jsonify({"error": "clerk_user_id is required"}), 400

        if not company_name or not sender_email or not sender_phone:
            return jsonify({"error": "company_name, sender_email and sender_phone are required"}), 400

        user = usersCollection.find_one({"clerk_user_id": clerk_user_id})
        print("[ONBOARDING] fetched user:", user, flush=True)

        if not user:
            return jsonify({"error": "User not found"}), 404

        # If onboarding already completed, return existing company
        if user.get("company_id"):
            company = get_company_by_user(user)
            return jsonify({
                "message": "Onboarding already completed",
                "user_id": str(user["_id"]),
                "company_id": user.get("company_id"),
                "company_name": company.get("name", "") if company else user.get("company_name", ""),
                "onboarding_completed": True
            }), 200

        # Reuse a company if this same user/email already created one earlier
        existing_company = compCollection.find_one({
            "name": company_name,
            "sender_email": sender_email
        })

        if existing_company:
            company_id = str(existing_company["_id"])
            print("[ONBOARDING] reusing company_id:", company_id, flush=True)
        else:
            company_doc = {
                "name": company_name,
                "sender_email": sender_email,
                "sender_phone": sender_phone,
                "created_by": str(user["_id"]),
                "created_at": datetime.utcnow()
            }

            company_result = compCollection.insert_one(company_doc)
            company_id = str(company_result.inserted_id)
            print("[ONBOARDING] created company_id:", company_id, flush=True)

        usersCollection.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "company_id": company_id,
                    "company_name": company_name,
                    "onboarding_completed": True,
                    "updated_at": datetime.utcnow(),
                }
            }
        )

        return jsonify({
            "message": "Onboarding completed successfully",
            "user_id": str(user["_id"]),
            "company_id": company_id,
            "company_name": company_name,
            "onboarding_completed": True
        }), 200

    except DuplicateKeyError as e:
        print("[ONBOARDING][COMPLETE DUPLICATE ERROR]", str(e), flush=True)
        return jsonify({"error": "Duplicate onboarding detected. Please retry."}), 409
    except Exception as e:
        print("[ONBOARDING][COMPLETE ERROR]", str(e), flush=True)
        return jsonify({"error": str(e)}), 500


@onboarding_bp.route("/me/<clerk_user_id>", methods=["GET"])
def get_me(clerk_user_id):
    try:
        user = usersCollection.find_one({"clerk_user_id": clerk_user_id})
        if not user:
            return jsonify({"error": "User not found"}), 404

        company = get_company_by_user(user)

        return jsonify({
            "user_id": str(user["_id"]),
            "clerk_user_id": user.get("clerk_user_id"),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "company_id": user.get("company_id"),
            "company_name": company.get("name", "") if company else user.get("company_name", ""),
            "sender_email": company.get("sender_email", "") if company else "",
            "sender_phone": company.get("sender_phone", "") if company else "",
            "role": user.get("role", "admin"),
            "onboarding_completed": user.get("onboarding_completed", False)
        }), 200

    except Exception as e:
        print("[ONBOARDING][ME ERROR]", str(e), flush=True)
        return jsonify({"error": str(e)}), 500