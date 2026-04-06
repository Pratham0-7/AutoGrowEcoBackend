import os
import requests
from dotenv import load_dotenv

load_dotenv()

PLATFORM_MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY", "")


def format_mobile(mobile: str) -> str:
    """Normalize mobile to 12-digit format (91XXXXXXXXXX)."""
    mobile = str(mobile).strip().replace(" ", "").replace("-", "").replace("+", "")
    if mobile.startswith("91") and len(mobile) == 12:
        return mobile
    if len(mobile) == 10:
        return f"91{mobile}"
    return mobile


def send_sms_msg91(
    mobile: str,
    template_id: str,
    variables: dict,
    auth_key: str = None,
) -> dict:
    """
    Send a transactional SMS via MSG91 Flow API (DLT compliant).

    Args:
        mobile:      Recipient phone number (any reasonable format).
        template_id: DLT-approved MSG91 template ID.
        variables:   Dict of template variables, e.g. {"var1": "John", "var2": "https://..."}
        auth_key:    MSG91 auth key. Falls back to platform-level key if not supplied.

    Returns:
        dict: MSG91 API response (or an error dict if sending fails/is skipped).
    """
    key = auth_key or PLATFORM_MSG91_AUTH_KEY

    if not key:
        print("[MSG91] No auth key configured — SMS skipped.", flush=True)
        return {"type": "skipped", "message": "MSG91 not configured"}

    if not template_id:
        print("[MSG91] No template_id supplied — SMS skipped.", flush=True)
        return {"type": "skipped", "message": "No template_id"}

    formatted = format_mobile(mobile)
    recipient = {"mobiles": formatted}
    recipient.update(variables)

    payload = {
        "template_id": template_id,
        "short_url": "0",
        "recipients": [recipient],
    }

    headers = {
        "authkey": key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/flow/",
            json=payload,
            headers=headers,
            timeout=10,
        )
        result = resp.json()
        print(f"[MSG91] Response for {formatted}: {result}", flush=True)
        return result
    except Exception as exc:
        print(f"[MSG91] Error sending to {formatted}: {exc}", flush=True)
        return {"type": "error", "message": str(exc)}
