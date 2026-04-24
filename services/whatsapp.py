import os
import requests
from dotenv import load_dotenv

load_dotenv()

PLATFORM_WA_AUTH_KEY = os.getenv("MSG91_WHATSAPP_AUTH_KEY") or os.getenv("MSG91_AUTH_KEY", "")


def format_phone_wa(phone: str) -> str:
    phone = str(phone or "").strip().replace(" ", "").replace("-", "").replace("+", "")
    if phone.startswith("91") and len(phone) == 12:
        return phone
    if len(phone) == 10:
        return f"91{phone}"
    return phone


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text}


def send_whatsapp_text(phone: str, message: str, integrated_number: str, auth_key: str | None = None) -> dict:
    key = auth_key or PLATFORM_WA_AUTH_KEY

    if not key:
        return {"ok": False, "type": "skipped", "message": "WhatsApp auth key not configured"}

    if not integrated_number:
        return {"ok": False, "type": "skipped", "message": "Integrated WhatsApp number not set"}

    formatted_to = format_phone_wa(phone)
    formatted_from = format_phone_wa(integrated_number)

    payload = {
        "integrated_number": formatted_from,
        "content_type": "text",
        "payload": [
            {
                "to": formatted_to,
                "type": "text",
                "text": {
                    "body": message
                }
            }
        ]
    }

    headers = {
        "authkey": key,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=headers,
            timeout=20,
        )

        result = _safe_json(resp)

        print(f"[WA TEXT] HTTP {resp.status_code}", flush=True)
        print(f"[WA TEXT] Payload: {payload}", flush=True)
        print(f"[WA TEXT] Response: {result}", flush=True)

        ok = 200 <= resp.status_code < 300

        return {
            "ok": ok,
            "http_status": resp.status_code,
            "type": "success" if ok else "error",
            "message": (
                result.get("message")
                or result.get("error")
                or result.get("errors")
                or result.get("raw_text")
                or "Unknown response"
            ),
            "provider_response": result,
        }

    except Exception as exc:
        print(f"[WA TEXT] Exception: {exc}", flush=True)
        return {
            "ok": False,
            "type": "error",
            "message": str(exc),
            "provider_response": {},
        }


def send_whatsapp_template(
    phone: str,
    template_name: str,
    template_params: dict,
    integrated_number: str,
    auth_key: str | None = None,
    language_code: str = "en",
) -> dict:
    key = auth_key or PLATFORM_WA_AUTH_KEY

    if not key:
        return {"ok": False, "type": "skipped", "message": "WhatsApp auth key not configured"}

    if not integrated_number:
        return {"ok": False, "type": "skipped", "message": "Integrated WhatsApp number not set"}

    if not template_name:
        return {"ok": False, "type": "skipped", "message": "Template name not set"}

    formatted_to = format_phone_wa(phone)
    formatted_from = format_phone_wa(integrated_number)

    components = {
        param_name: {
            "type": "text",
            "value": str(value),
            "parameter_name": param_name,
        }
        for param_name, value in (template_params or {}).items()
    }

    payload = {
        "integrated_number": formatted_from,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code,
                    "policy": "deterministic"
                },
                "namespace": None,
                "to_and_components": [
                    {
                        "to": [formatted_to],
                        "components": components,
                    }
                ],
            }
        }
    }

    headers = {
        "authkey": key,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/",
            json=payload,
            headers=headers,
            timeout=20,
        )

        result = _safe_json(resp)

        print(f"[WA TEMPLATE] HTTP {resp.status_code}", flush=True)
        print(f"[WA TEMPLATE] Payload: {payload}", flush=True)
        print(f"[WA TEMPLATE] Response: {result}", flush=True)

        ok = 200 <= resp.status_code < 300

        return {
            "ok": ok,
            "http_status": resp.status_code,
            "type": "success" if ok else "error",
            "message": (
                result.get("message")
                or result.get("error")
                or result.get("errors")
                or result.get("raw_text")
                or "Unknown response"
            ),
            "provider_response": result,
        }

    except Exception as exc:
        print(f"[WA TEMPLATE] Exception: {exc}", flush=True)
        return {
            "ok": False,
            "type": "error",
            "message": str(exc),
            "provider_response": {},
        }