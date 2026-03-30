from flask import Blueprint, request, jsonify
from db import usersCollection, compCollection
from werkzeug.security import generate_password_hash, check_password_hash
from bson import ObjectId

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.json

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    company_name = data.get("company_name")

    if not all([name, email, password, company_name]):
        return jsonify({"error": "Missing fields"}), 400

    existing_user = usersCollection.find_one({"email": email})
    if existing_user:
        return jsonify({"error": "User already exists"}), 400

    company = {"name": company_name}
    company_result = compCollection.insert_one(company)
    company_id = str(company_result.inserted_id)

    hashed_password = generate_password_hash(password)

    user = {
        "name": name,
        "email": email,
        "password": hashed_password,
        "company_id": company_id,
        "role": "admin"
    }

    user_result = usersCollection.insert_one(user)

    return jsonify({
        "message": "User registered successfully",
        "user_id": str(user_result.inserted_id),
        "company_id": company_id,
        "company_name": company_name,
        "name": name
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.json

    email = data.get("email")
    password = data.get("password")

    user = usersCollection.find_one({"email": email})

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    company = compCollection.find_one({"_id": ObjectId(user["company_id"])})

    return jsonify({
        "message": "Login successful",
        "user_id": str(user["_id"]),
        "company_id": user["company_id"],
        "name": user["name"],
        "company_name": company["name"] if company else ""
    }), 200