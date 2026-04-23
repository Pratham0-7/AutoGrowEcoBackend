import os
import requests
from dotenv import load_dotenv

load_dotenv()

PLATFORM_WA_AUTH_KEY = os.getenv("MSG91_WHATSAPP_AUTH_KEY", os.getenv("MSG91_AUTH_KEY", ""))


def format_phone_wa(phone: str) -> str:
    """Normalize phone to 12-digit format (91XXXXXXXXXX) for WhatsApp."""
    phone = str(phone).strip().replace(" ", "").replace("-", "").replace("+", "")
    if phone.startswith("91") and len(phone) == 12:
        return phone
    if len(phone) == 10:
        return f"91{phone}"
    return phone


def send_whatsapp_text(
    phone: str,
    message: str,
    integrated_number: str,
    auth_key: str = None,
) -> dict:
    """
    Send a free-form WhatsApp text message via MSG91.
    Works within the 24-hour customer service window or for approved utility messages.
    """
    key = auth_key or PLATFORM_WA_AUTH_KEY
    if not key:
        print("[WA] No auth key — skipped.", flush=True)
        return {"type": "skipped", "message": "WhatsApp auth key not configured"}
    if not integrated_number:
        print("[WA] No integrated_number — skipped.", flush=True)
        return {"type": "skipped", "message": "Integrated WhatsApp number not set"}

    formatted = format_phone_wa(phone)
    payload = {
        "integrated_number": format_phone_wa(integrated_number),
        "content_type": "text",
        "payload": [{
            "to": formatted,
            "type": "text",
            "text": {"body": message},
        }],
    }

    headers = {"authkey": key, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=headers,
            timeout=10,
        )
        result = resp.json()
        print(f"[WA] Text → {formatted}: {result}", flush=True)
        return result
    except Exception as exc:
        print(f"[WA] Error sending to {formatted}: {exc}", flush=True)
        return {"type": "error", "message": str(exc)}


def send_whatsapp_template(
    phone: str,
    template_name: str,
    template_params: list,
    integrated_number: str,
    auth_key: str = None,
    language_code: str = "en",
) -> dict:
    """
    Send a WhatsApp template message via MSG91.
    Required for business-initiated messages outside the 24-hour window.
    """
    key = auth_key or PLATFORM_WA_AUTH_KEY
    if not key:
        return {"type": "skipped", "message": "WhatsApp auth key not configured"}
    if not integrated_number:
        return {"type": "skipped", "message": "Integrated WhatsApp number not set"}
    if not template_name:
        return {"type": "skipped", "message": "Template name not set"}

    formatted = format_phone_wa(phone)
    components = []
    if template_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in template_params],
        })

    payload = {
        "integrated_number": format_phone_wa(integrated_number),
        "content_type": "template",
        "payload": [{
            "to": formatted,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code, "policy": "deterministic"},
                "components": components,
            },
        }],
    }

    headers = {"authkey": key, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=headers,
            timeout=10,
        )
        result = resp.json()
        print(f"[WA] Template → {formatted}: {result}", flush=True)
        return result
    except Exception as exc:
        print(f"[WA] Error: {exc}", flush=True)
        return {"type": "error", "message": str(exc)}
