import requests

META_GRAPH_BASE = "https://graph.facebook.com/v19.0"

PREBUILT_TEMPLATES = [
    {
        "name": "hello_world",
        "display_name": "Hello World (Meta test default)",
        "language": "en_US",
        "language_code": "en_US",
        "body": "Hello World",
        "variables": [],
        "status": "approved",
        "category": "UTILITY",
        "use_case": "Meta default test template",
        "buttons": [],
    },
    {
        "name": "age_intro_v1",
        "display_name": "AGE Intro",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, I'm reaching out from AutoGrowthEco. We help businesses automate their lead follow-up. Would you be open to a quick chat? Reply YES.",
        "variables": [{"index": 1, "label": "Lead Name", "auto_fill": "lead_name"}],
        "status": "approved",
        "category": "MARKETING",
        "use_case": "Initial outreach to new leads",
        "buttons": [],
    },
    {
        "name": "age_followup_v1",
        "display_name": "AGE Follow-up",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, just following up on my earlier message. Is this a good time to connect? I'd love to show you how we can help {{2}} grow faster.",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
            {"index": 2, "label": "Company Name", "auto_fill": "company_name"},
        ],
        "status": "approved",
        "category": "MARKETING",
        "use_case": "Follow-up after no response to first message",
        "buttons": [],
    },
    {
        "name": "lead_followup_v1",
        "display_name": "Lead Follow-up",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, we noticed you expressed interest in our services. I wanted to follow up and see if you have any questions. Our team at {{2}} is here to help!",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
            {"index": 2, "label": "Company Name", "auto_fill": "company_name"},
        ],
        "status": "pending",
        "category": "MARKETING",
        "use_case": "Follow up with leads who expressed interest",
        "buttons": [],
    },
    {
        "name": "missed_call_followup_v1",
        "display_name": "Missed Call Follow-up",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, I tried calling you but couldn't reach you. I'd love to connect and share how we can help your business. When's a good time to talk?",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
        ],
        "status": "pending",
        "category": "UTILITY",
        "use_case": "Follow up after a missed outbound call",
        "buttons": [],
    },
    {
        "name": "appointment_reminder_v1",
        "display_name": "Appointment Reminder",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, this is a friendly reminder about your appointment on {{2}}. Please let us know if you need to reschedule. — {{3}}",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
            {"index": 2, "label": "Appointment Date", "auto_fill": "appointment_date"},
            {"index": 3, "label": "Your Name", "auto_fill": "salesperson_name"},
        ],
        "status": "pending",
        "category": "UTILITY",
        "use_case": "Remind lead of upcoming appointment",
        "buttons": [],
    },
    {
        "name": "site_visit_reminder_v1",
        "display_name": "Site Visit Reminder",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, your site visit is scheduled for {{2}}. Our team will be ready to show you around. See you then! — {{3}}",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
            {"index": 2, "label": "Visit Date & Time", "auto_fill": "appointment_date"},
            {"index": 3, "label": "Your Name", "auto_fill": "salesperson_name"},
        ],
        "status": "pending",
        "category": "UTILITY",
        "use_case": "Remind lead of a scheduled site visit",
        "buttons": [],
    },
    {
        "name": "interested_confirmation_v1",
        "display_name": "Interest Confirmation",
        "language": "en",
        "language_code": "en",
        "body": "Hi {{1}}, great to hear you're interested! I'm {{2}} and I'll be your point of contact. I'll reach out shortly to discuss the next steps.",
        "variables": [
            {"index": 1, "label": "Lead Name", "auto_fill": "lead_name"},
            {"index": 2, "label": "Your Name", "auto_fill": "salesperson_name"},
        ],
        "status": "pending",
        "category": "MARKETING",
        "use_case": "Confirm interest and introduce salesperson",
        "buttons": [],
    },
]


def format_phone_meta(phone: str) -> str:
    """Return digits-only E.164 (no + prefix) as Meta expects."""
    phone = str(phone or "").strip().replace(" ", "").replace("-", "").replace("+", "")
    if phone.startswith("91") and len(phone) == 12:
        return phone
    if len(phone) == 10:
        return f"91{phone}"
    return phone


def build_components(variables: dict) -> list:
    """Convert {1: 'John', 2: 'Acme'} to Meta body components array."""
    if not variables:
        return []
    sorted_keys = sorted(variables.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
    params = [{"type": "text", "text": str(variables[k])} for k in sorted_keys if str(variables[k]).strip()]
    if not params:
        return []
    return [{"type": "body", "parameters": params}]


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text}


def send_meta_text(
    phone_number_id: str,
    access_token: str,
    to_phone: str,
    text_body: str,
) -> dict:
    """
    Send a free-form text message via Meta Cloud API.
    Only valid within the 24-hour customer service window.
    """
    if not phone_number_id:
        return {"ok": False, "message": "Meta phone_number_id not configured", "meta_message_id": None, "provider_response": {}}
    if not access_token:
        return {"ok": False, "message": "Meta access_token not configured", "meta_message_id": None, "provider_response": {}}

    formatted_to = format_phone_meta(to_phone)
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_to,
        "type": "text",
        "text": {"body": str(text_body).strip()},
    }
    url = f"{META_GRAPH_BASE}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        result = _safe_json(resp)
        ok = 200 <= resp.status_code < 300 and "messages" in result
        meta_message_id = result["messages"][0].get("id") if ok and result.get("messages") else None
        print(f"[META CLOUD TEXT] to={formatted_to} HTTP {resp.status_code}", flush=True)
        error_msg = None
        if not ok:
            err = result.get("error", {})
            error_msg = err.get("message") or result.get("raw_text") or "Unknown Meta API error"
        return {"ok": ok, "http_status": resp.status_code, "meta_message_id": meta_message_id, "message": "sent" if ok else error_msg, "provider_response": result}
    except Exception as exc:
        print(f"[META CLOUD TEXT] Exception: {exc}", flush=True)
        return {"ok": False, "message": str(exc), "meta_message_id": None, "provider_response": {}}


def send_meta_template(
    phone_number_id: str,
    access_token: str,
    to_phone: str,
    template_name: str,
    language_code: str = "en",
    components: list | None = None,
) -> dict:
    """
    Send a WhatsApp template message via Meta Cloud API.
    Returns {ok, meta_message_id, provider_response, message}.
    """
    if not phone_number_id:
        return {
            "ok": False,
            "message": "Meta phone_number_id not configured",
            "meta_message_id": None,
            "provider_response": {},
        }
    if not access_token:
        return {
            "ok": False,
            "message": "Meta access_token not configured",
            "meta_message_id": None,
            "provider_response": {},
        }

    formatted_to = format_phone_meta(to_phone)

    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }

    if components:
        payload["template"]["components"] = components

    url = f"{META_GRAPH_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        result = _safe_json(resp)

        ok = 200 <= resp.status_code < 300 and "messages" in result
        meta_message_id = None
        if ok and result.get("messages"):
            meta_message_id = result["messages"][0].get("id")

        print(f"[META CLOUD] to={formatted_to} template={template_name} HTTP {resp.status_code}", flush=True)
        print(f"[META CLOUD] Response: {result}", flush=True)

        error_msg = None
        if not ok:
            err = result.get("error", {})
            error_msg = err.get("message") or result.get("raw_text") or "Unknown Meta API error"

        return {
            "ok": ok,
            "http_status": resp.status_code,
            "meta_message_id": meta_message_id,
            "message": "sent" if ok else error_msg,
            "provider_response": result,
        }

    except Exception as exc:
        print(f"[META CLOUD] Exception: {exc}", flush=True)
        return {
            "ok": False,
            "message": str(exc),
            "meta_message_id": None,
            "provider_response": {},
        }
