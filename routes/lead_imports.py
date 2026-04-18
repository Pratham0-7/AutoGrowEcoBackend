from flask import Blueprint, request, jsonify
import pandas as pd
import os
from werkzeug.utils import secure_filename
from bson import ObjectId

from services.import_service import (
    extract_sheet_id,
    fetch_sheet_rows,
    import_rows,
    save_gsheet_config,
)

lead_imports_bp = Blueprint("lead_imports", __name__)

UPLOAD_FOLDER = "uploads"


@lead_imports_bp.route("/upload_leads", methods=["POST"])
def upload_csv():
    file = request.files.get("file")
    company_id = request.form.get("company_id")
    user_id = request.form.get("user_id")
    campaign_id = request.form.get("campaign_id") or None

    if not file:
        return jsonify({"error": "No file uploaded"}), 400
    if not company_id:
        return jsonify({"error": "company_id is required"}), 400
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)
    if filename == "":
        return jsonify({"error": "No file selected"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(filepath)

        if filename.endswith(".csv"):
            df = pd.read_csv(filepath)
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(filepath)
        else:
            os.remove(filepath)
            return jsonify({"error": "Only CSV, XLSX, and XLS files allowed"}), 400

        df.columns = [col.lower().strip() for col in df.columns]
        rows = df.to_dict(orient="records")

        inserted_count, skipped_count, duplicates = import_rows(rows, company_id, user_id, campaign_id=campaign_id)

        os.remove(filepath)

        return jsonify({
            "message": f"{inserted_count} leads uploaded successfully",
            "skipped_duplicates": skipped_count,
            "duplicates": duplicates,
        }), 200

    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": str(e)}), 500


@lead_imports_bp.route("/connect_gsheet", methods=["POST"])
def connect_gsheet():
    try:
        data = request.json or {}
        company_id = data.get("company_id")
        sheet_url = data.get("sheet_url", "").strip()
        access_token = data.get("access_token")

        if not company_id or not sheet_url:
            return jsonify({"error": "company_id and sheet_url are required"}), 400

        sheet_id = extract_sheet_id(sheet_url)
        if not sheet_id:
            return jsonify({"error": "Invalid Google Sheets URL"}), 400

        save_gsheet_config(company_id, sheet_id, sheet_url, access_token)

        rows = fetch_sheet_rows(sheet_id, access_token)
        inserted, skipped, duplicates = import_rows(rows, company_id, user_id=None)

        return jsonify({
            "message": f"Sheet connected. {inserted} leads imported.",
            "skipped_duplicates": skipped,
            "duplicates": duplicates,
            "sheet_id": sheet_id,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@lead_imports_bp.route("/sync_gsheet/<company_id>", methods=["POST"])
def sync_gsheet(company_id):
    try:
        from db import compCollection

        company = compCollection.find_one({"_id": ObjectId(company_id)})
        if not company:
            return jsonify({"error": "Company not found"}), 404

        sheet_id = company.get("gsheet_id")
        if not sheet_id:
            return jsonify({"error": "No Google Sheet connected"}), 400

        access_token = company.get("gsheet_access_token")
        rows = fetch_sheet_rows(sheet_id, access_token)
        inserted, skipped, duplicates = import_rows(rows, company_id, user_id=None)

        compCollection.update_one(
            {"_id": ObjectId(company_id)},
            {"$set": {"gsheet_last_synced": __import__("datetime").datetime.utcnow()}}
        )

        return jsonify({
            "message": f"Sync complete. {inserted} new leads added.",
            "skipped_duplicates": skipped,
            "duplicates": duplicates,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500