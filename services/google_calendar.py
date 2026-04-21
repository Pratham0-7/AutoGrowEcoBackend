import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import pytz

IST = pytz.timezone("Asia/Kolkata")
SLOT_MINUTES = 30
DAY_START_HOUR = 10   # 10:00 AM IST
DAY_END_HOUR = 17     # 5:00 PM IST
MIN_NOTICE_HOURS = 2
BUFFER_MINUTES = 0

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service():
    """Return Google Calendar service client or None if not properly configured."""
    if not CALENDAR_ID:
        print("[GCAL] GOOGLE_CALENDAR_ID not set", flush=True)
        return None

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"[GCAL] Service account file not found: {SERVICE_ACCOUNT_FILE}", flush=True)
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=SCOPES
        )
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:
        print(f"[GCAL] Init error: {exc}", flush=True)
        return None


def _parse_slot_start(date_str: str, time_str: str):
    date = datetime.strptime(date_str, "%Y-%m-%d").date()
    hour, minute = map(int, time_str.split(":"))
    return IST.localize(datetime(date.year, date.month, date.day, hour, minute))


def _all_slots(date_str: str) -> List[datetime]:
    """Generate all possible slots for the day in IST."""
    date = datetime.strptime(date_str, "%Y-%m-%d").date()
    slots = []

    current = IST.localize(datetime(date.year, date.month, date.day, DAY_START_HOUR, 0))
    end_of_day = IST.localize(datetime(date.year, date.month, date.day, DAY_END_HOUR, 0))

    while current + timedelta(minutes=SLOT_MINUTES) <= end_of_day:
        slots.append(current)
        current += timedelta(minutes=SLOT_MINUTES)

    return slots


def _busy_intervals_for_day(service, date_str: str):
    date = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_start = IST.localize(datetime(date.year, date.month, date.day, 0, 0))
    day_end = IST.localize(datetime(date.year, date.month, date.day, 23, 59, 59))

    result = service.freebusy().query(body={
        "timeMin": day_start.isoformat(),
        "timeMax": day_end.isoformat(),
        "timeZone": "Asia/Kolkata",
        "items": [{"id": CALENDAR_ID}],
    }).execute()

    busy = result.get("calendars", {}).get(CALENDAR_ID, {}).get("busy", [])
    intervals = []

    for item in busy:
        start = datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(IST)
        end = datetime.fromisoformat(item["end"].replace("Z", "+00:00")).astimezone(IST)
        if BUFFER_MINUTES > 0:
            start -= timedelta(minutes=BUFFER_MINUTES)
            end += timedelta(minutes=BUFFER_MINUTES)
        intervals.append((start, end))

    return intervals


def is_calendar_ready() -> bool:
    return _get_service() is not None


def is_slot_available(date_str: str, time_str: str) -> bool:
    service = _get_service()
    if not service:
        return False

    try:
        slot_start = _parse_slot_start(date_str, time_str)
        slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)

        now_ist = datetime.now(IST)
        if slot_start < now_ist + timedelta(hours=MIN_NOTICE_HOURS):
            return False

        busy_intervals = _busy_intervals_for_day(service, date_str)

        for b_start, b_end in busy_intervals:
            if not (slot_end <= b_start or slot_start >= b_end):
                return False

        return True
    except Exception as exc:
        print(f"[GCAL] Slot availability error: {exc}", flush=True)
        return False


def get_available_slots(date_str: str) -> Dict[str, Any]:
    """
    Return:
    {
      "ready": bool,
      "slots": ["10:00", "10:30", ...],
      "error": optional str
    }
    """
    all_slots = _all_slots(date_str)
    service = _get_service()

    if not service:
        return {
            "ready": False,
            "slots": [],
            "error": "Google Calendar is not configured"
        }

    try:
        busy_intervals = _busy_intervals_for_day(service, date_str)
        now_ist = datetime.now(IST)
        available = []

        for slot in all_slots:
            slot_end = slot + timedelta(minutes=SLOT_MINUTES)

            if slot < now_ist + timedelta(hours=MIN_NOTICE_HOURS):
                continue

            is_busy = any(
                not (slot_end <= b_start or slot >= b_end)
                for b_start, b_end in busy_intervals
            )
            if not is_busy:
                available.append(slot.strftime("%H:%M"))

        return {
            "ready": True,
            "slots": available
        }

    except Exception as exc:
        print(f"[GCAL] Freebusy error: {exc}", flush=True)
        return {
            "ready": False,
            "slots": [],
            "error": "Could not fetch availability"
        }


def create_event(
    name: str,
    email: str,
    company: str,
    date_str: str,
    time_str: str,
    title: str = "Demo call for AGE"
) -> Optional[Dict[str, Any]]:
    """
    Create a Google Calendar event and return:
    {
      "id": "...",
      "htmlLink": "...",
      "start": "...",
      "end": "..."
    }
    """
    service = _get_service()
    if not service:
        print("[GCAL] create_event called without configured service", flush=True)
        return None

    try:
        start = _parse_slot_start(date_str, time_str)
        end = start + timedelta(minutes=SLOT_MINUTES)

        description = f"Demo call with {name}"
        if company:
            description += f" from {company}"
        description += f"\n\nContact: {email}\n\nBooked via ageautomation.in"

        event = {
            "summary": title,
            "description": description,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": "Asia/Kolkata"
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": "Asia/Kolkata"
            },
            "attendees": [
                {
                    "email": email,
                    "displayName": name
                }
            ],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 60},
                    {"method": "popup", "minutes": 10},
                ],
            },
        }

        created = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event,
            sendUpdates="all",
        ).execute()

        return {
            "id": created.get("id"),
            "htmlLink": created.get("htmlLink"),
            "start": created.get("start", {}).get("dateTime"),
            "end": created.get("end", {}).get("dateTime"),
        }

    except Exception as exc:
        print(f"[GCAL] Create event error: {exc}", flush=True)
        return None