import os
import re
import requests as http_requests
from datetime import datetime
from bson import ObjectId

from db import leadCollection, usersCollection, compCollection

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")


def extract_sheet_id(url):
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    return match.group(1) if match else None


def fetch_sheet_rows(sheet_id, access_token=None):
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:Z1000"
    else:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:Z1000?key={GOOGLE_API_KEY}"

    resp = http_requests.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"Google Sheets API error: {resp.status_code} — {resp.text}")

    data = resp.json()
    rows = data.get("values", [])
    if not rows:
        return []

    headers_row = [h.lower().strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers_row) - len(row))
        record = dict(zip(headers_row, padded))
        result.append(record)
    return result


def import_rows(rows, company_id, user_id):
    inserted = 0
    skipped = 0
    duplicates = []

    for row in rows:
        name = str(row.get("name", "")).strip()
        email = str(row.get("email", "")).strip()
        phone = str(row.get("phone", "")).strip()

        if not name and not email and not phone:
            continue

        dup_query = {"company_id": company_id, "$or": []}
        if email:
            dup_query["$or"].append({"email": email})
        if phone:
            dup_query["$or"].append({"phone": phone})

        existing = None
        if dup_query["$or"]:
            existing = leadCollection.find_one(dup_query)

        if existing:
            skipped += 1
            uploader_name = "Another salesperson"
            uploader_id = existing.get("uploaded_by")

            if uploader_id:
                try:
                    u = usersCollection.find_one({"_id": ObjectId(uploader_id)})
                    if u:
                        uploader_name = u.get("name", uploader_name)
                except Exception:
                    pass

            duplicates.append({
                "name": name,
                "email": email,
                "phone": phone,
                "already_uploaded_by": uploader_name
            })
            continue

        leadCollection.insert_one({
            "company_id": company_id,
            "uploaded_by": user_id or "gsheet_sync",
            "name": name,
            "email": email,
            "phone": phone,
            "send_status": "not sent",
            "response_status": "pending",
            "followup_count": 0,
            "last_followup_sent_at": None,
            "next_followup_at": None,
            "campaign_id": None,
            "source": "google_sheets" if user_id is None else "csv_upload",
        })
        inserted += 1

    return inserted, skipped, duplicates


def save_gsheet_config(company_id, sheet_id, sheet_url, access_token=None):
    compCollection.update_one(
        {"_id": ObjectId(company_id)},
        {"$set": {
            "gsheet_id": sheet_id,
            "gsheet_url": sheet_url,
            "gsheet_connected_at": datetime.utcnow(),
            "gsheet_access_token": access_token,
        }}
    )