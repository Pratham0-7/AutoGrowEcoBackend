from flask import Blueprint, jsonify

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/auth_health", methods=["GET"])
def auth_health():
    return jsonify({"status": "ok"}), 200