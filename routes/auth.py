from flask import Blueprint, request, jsonify
from db import usersCollection, compCollection
from datetime import datetime

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/sync_clerk_user", methods=["POST"])
def sync_clerk_user():
    data = request.json

    clerk_user_id = data.get("clerk_user_id")
    name = data.get("name")
    email = data.get("email")

    if not clerk_user_id or not email:
        return jsonify({"error": "Missing data"}), 400

    user = usersCollection.find_one({"clerk_user_id": clerk_user_id})

    if user:
        return jsonify({
            "user_id": str(user["_id"]),
            "company_id": user.get("company_id"),
            "name": user.get("name"),
            "company_name": user.get("company_name"),
            "onboarding_completed": bool(user.get("company_id"))
        }), 200

    new_user = {
        "clerk_user_id": clerk_user_id,
        "email": email,
        "name": name,
        "company_id": None,
        "company_name": None
    }

    result = usersCollection.insert_one(new_user)

    return jsonify({
        "user_id": str(result.inserted_id),
        "onboarding_completed": False
    }), 201


# 🔥 Complete onboarding
@auth_bp.route("/complete_onboarding", methods=["POST"])
def complete_onboarding():
    data = request.get_json()

    clerk_user_id = data.get("clerk_user_id")
    company_name = data.get("company_name")
    sender_email = data.get("sender_email")
    sender_phone = data.get("sender_phone")

    if not clerk_user_id or not company_name:
        return jsonify({"error": "Missing required fields"}), 400

    # create company
    company = {
        "name": company_name,
        "sender_email": sender_email,
        "sender_phone": sender_phone,
        "created_at": datetime.utcnow()
    }

    comp = compCollection.insert_one(company)
    company_id = str(comp.inserted_id)

    # update user
    usersCollection.update_one(
        {"clerk_user_id": clerk_user_id},
        {"$set": {
            "company_id": company_id,
            "company_name": company_name,
            "onboarding_completed": True
        }}
    )

    return jsonify({
        "company_id": company_id,
        "company_name": company_name
    }), 200