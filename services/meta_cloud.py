import requests

META_GRAPH_BASE = "https://graph.facebook.com/v19.0"

PREBUILT_TEMPLATES = [
    {
        "name": "hello_world",
        "display_name": "Hello World (Meta test default)",
        "language": "en_US",
        "body": "Hello World",
        "variables": [],
        "status": "approved",
        "category": "UTILITY",
    },
    {
        "name": "age_intro_v1",
        "display_name": "AGE Intro",
        "language": "en",
        "body": "Hi {{1}}, I'm reaching out from AutoGrowthEco. We help businesses automate their lead follow-up. Would you be open to a quick chat? Reply YES.",
        "variables": [{"index": 1, "label": "Lead Name"}],
        "status": "approved",
        "category": "MARKETING",
    },
    {
        "name": "age_followup_v1",
        "display_name": "AGE Follow-up",
        "language": "en",
        "body": "Hi {{1}}, just following up on my earlier message. Is this a good time to connect? I'd love to show you how we can help {{2}} grow faster.",
        "variables": [{"index": 1, "label": "Lead Name"}, {"index": 2, "label": "Company Name"}],
        "status": "approved",
        "category": "MARKETING",
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
