import os
import re
from datetime import datetime, timedelta
from html import escape
from urllib.parse import quote

import boto3
import pytz
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify

from db import bookingsCollection
from services.google_calendar import (
    get_available_slots,
    create_event,
    is_slot_available,
)

bookings_bp = Blueprint("bookings", __name__, url_prefix="/bookings")

ADMIN_PIN = os.getenv("ADMIN_PIN", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

IST = pytz.timezone("Asia/Kolkata")
DEMO_DURATION_MINUTES = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_pin():
    if not ADMIN_PIN:
        return False
    return request.headers.get("X-Admin-Pin", "") == ADMIN_PIN


def _clean_text(value: str, max_len: int) -> str:
    value = (value or "").strip()
    value = " ".join(value.split())
    return value[:max_len]


def _parse_slot_start(date_str: str, time_str: str):
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return IST.localize(dt)


def _gcal_link(name, company, date_str, time_str, title):
    """Manual fallback link only after booking is successfully stored."""
    try:
        dt = _parse_slot_start(date_str, time_str)
        end_dt = dt + timedelta(minutes=DEMO_DURATION_MINUTES)
        start = dt.strftime("%Y%m%dT%H%M%S")
        end = end_dt.strftime("%Y%m%dT%H%M%S")
        event_title = title or f"AGE Demo with {name}" + (f" ({company})" if company else "")
        return (
            f"https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(event_title)}"
            f"&dates={start}/{end}"
            f"&ctz=Asia%2FKolkata"
        )
    except Exception:
        return "https://calendar.google.com"


def _send_email(to_addr, subject, body_html, body_text=""):
    if not ADMIN_EMAIL:
        return
    try:
        client = boto3.client("ses", region_name=AWS_REGION)
        client.send_email(
            Source=ADMIN_EMAIL,
            Destination={"ToAddresses": [to_addr]},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Html": {"Data": body_html},
                    "Text": {"Data": body_text or subject},
                },
            },
        )
    except ClientError as exc:
        print(f"[BOOKINGS] SES error: {exc}", flush=True)


@bookings_bp.route("/availability", methods=["GET"])
def availability():
    date_str = request.args.get("date", "").strip()
    if not date_str:
        return jsonify({"error": "date param required (YYYY-MM-DD)"}), 400

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    result = get_available_slots(date_str)

    if not result["ready"]:
        return jsonify({
            "date": date_str,
            "calendar_ready": False,
            "slots": [],
            "error": result.get("error", "Scheduling unavailable")
        }), 503

    return jsonify({
        "date": date_str,
        "calendar_ready": True,
        "slots": result["slots"]
    }), 200


@bookings_bp.route("/book", methods=["POST"])
def create_booking():
    data = request.get_json(silent=True) or {}

    name = _clean_text(data.get("name"), 100)
    email = _clean_text(data.get("email"), 180)
    company = _clean_text(data.get("company"), 120)
    message = _clean_text(data.get("message"), 1000)
    date_str = _clean_text(data.get("date"), 10)
    time_str = _clean_text(data.get("time"), 5)
    title = _clean_text(data.get("title") or "Demo call for AGE", 120)

    if not all([name, email, date_str, time_str]):
        return jsonify({"error": "name, email, date, and time are required"}), 400

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400

    try:
        slot_start = _parse_slot_start(date_str, time_str)
    except ValueError:
        return jsonify({"error": "Invalid date/time"}), 400

    now_ist = datetime.now(IST)
    if slot_start < now_ist:
        return jsonify({"error": "Cannot book a past slot"}), 400

    # Check duplicate in DB first
    existing = bookingsCollection.find_one({
        "date": date_str,
        "time": time_str,
        "status": {"$in": ["confirmed", "pending"]}
    })
    if existing:
        return jsonify({"error": "This slot has already been booked"}), 409

    # Re-check live calendar availability
    if not is_slot_available(date_str, time_str):
        return jsonify({"error": "This slot is no longer available"}), 409

    # Create Google Calendar event
    event = create_event(name, email, company, date_str, time_str, title)
    if not event:
        return jsonify({
            "error": "Could not create calendar event. Please try again."
        }), 503

    gcal_link = event.get("htmlLink") or _gcal_link(name, company, date_str, time_str, title)

    booking = {
        "name": name,
        "email": email,
        "company": company,
        "message": message,
        "date": date_str,
        "time": time_str,
        "title": title,
        "status": "confirmed",
        "calendar_synced": True,
        "calendar_event_id": event.get("id"),
        "event_url": event.get("htmlLink"),
        "start_at": slot_start.astimezone(pytz.UTC).isoformat(),
        "timezone": "Asia/Kolkata",
        "source": "website_demo_form",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    result = bookingsCollection.insert_one(booking)

    safe_title = escape(title)
    safe_name = escape(name)
    safe_email = escape(email)
    safe_company = escape(company) if company else "—"
    safe_message = escape(message) if message else "—"

    if ADMIN_EMAIL:
        admin_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
          <h2 style="color:#1A2E35;">New Demo Booking</h2>
          <p style="color:#0F5E6E;font-size:13px;">✅ Event created in your Google Calendar.</p>
          <table style="border-collapse:collapse;width:100%;margin-top:8px;">
            <tr><td style="padding:6px 0;color:#555;width:100px;">Title</td><td style="padding:6px 0;font-weight:600;color:#111;">{safe_title}</td></tr>
            <tr><td style="padding:6px 0;color:#555;">Name</td><td style="padding:6px 0;font-weight:600;color:#111;">{safe_name}</td></tr>
            <tr><td style="padding:6px 0;color:#555;">Email</td><td style="padding:6px 0;color:#111;">{safe_email}</td></tr>
            <tr><td style="padding:6px 0;color:#555;">Company</td><td style="padding:6px 0;color:#111;">{safe_company}</td></tr>
            <tr><td style="padding:6px 0;color:#555;">Date</td><td style="padding:6px 0;font-weight:600;color:#0F5E6E;">{date_str} at {time_str} IST</td></tr>
            <tr><td style="padding:6px 0;color:#555;">Message</td><td style="padding:6px 0;color:#111;">{safe_message}</td></tr>
          </table>
          <a href="{gcal_link}" style="display:inline-block;margin-top:16px;background:#0F5E6E;color:white;padding:12px 24px;border-radius:10px;text-decoration:none;font-weight:600;">View in Google Calendar</a>
        </div>
        """
        _send_email(
            ADMIN_EMAIL,
            f"AGE Demo Booked — {name} on {date_str} at {time_str}",
            admin_html,
        )

    confirm_html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;background:#1A2E35;border-radius:16px;overflow:hidden;">
      <div style="background:#0F5E6E;padding:28px 32px;">
        <div style="font-size:11px;font-weight:700;letter-spacing:2px;color:rgba(255,255,255,0.6);margin-bottom:8px;">AUTOMATED GROWTH ECOSYSTEM</div>
        <h1 style="margin:0;font-size:22px;color:white;font-weight:700;">Your demo is confirmed!</h1>
      </div>
      <div style="padding:32px;">
        <p style="margin:0 0 12px;color:rgba(255,255,255,0.75);">Hi {safe_name},</p>
        <p style="margin:0 0 8px;color:rgba(255,255,255,0.75);">
          <strong style="color:white;">{safe_title}</strong><br/>
          {date_str} at {time_str} IST · {DEMO_DURATION_MINUTES} min
        </p>
        <p style='margin:12px 0;color:rgba(255,255,255,0.6);font-size:13px;'>A calendar invite has been sent to your email.</p>
        <a href="{gcal_link}" style="display:inline-block;margin-top:16px;background:#E8563A;color:white;padding:13px 26px;border-radius:10px;text-decoration:none;font-weight:600;font-size:14px;">
          View Calendar Event
        </a>
        <p style="margin:24px 0 0;font-size:12px;color:rgba(255,255,255,0.35);">Confirmation sent to {safe_email}</p>
      </div>
    </div>
    """
    _send_email(email, f"Confirmed: {title} on {date_str}", confirm_html)

    return jsonify({
        "success": True,
        "booking_id": str(result.inserted_id),
        "gcal_link": gcal_link,
        "event_created": True,
    }), 201


@bookings_bp.route("", methods=["GET"])
def list_bookings():
    if not _check_pin():
        return jsonify({"error": "Unauthorized"}), 401

    bookings = list(
        bookingsCollection.find(
            {},
            {
                "_id": 1,
                "name": 1,
                "email": 1,
                "company": 1,
                "date": 1,
                "time": 1,
                "title": 1,
                "message": 1,
                "status": 1,
                "calendar_event_id": 1,
                "event_url": 1,
                "start_at": 1,
                "created_at": 1,
            }
        ).sort("start_at", -1).limit(200)
    )

    for booking in bookings:
        booking["_id"] = str(booking["_id"])

    return jsonify({"bookings": bookings}), 200