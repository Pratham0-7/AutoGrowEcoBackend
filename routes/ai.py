import os
import json
from flask import Blueprint, request, jsonify
import requests

ai_bp = Blueprint("ai", __name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")


def clean_ai_output(text):
    if not text:
        return text

    text = text.strip()

    bad_prefixes = [
        "Here's an improved version:",
        "Here’s an improved version:",
        "Improved message:",
        "Rewritten message:",
        "Final message:",
        "Here's a revised version:",
        "Here’s a revised version:",
        "Here's a generated message:",
        "Here’s a generated message:",
    ]

    for prefix in bad_prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    bad_suffix_markers = [
        "\n\nThis revised message",
        "\n\nThis message",
        "\n\nExplanation:",
        "\n\nWhy this works:",
        "\n\nThis version",
    ]

    for marker in bad_suffix_markers:
        if marker in text:
            text = text.split(marker)[0].strip()

    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    return text


def call_openrouter(system_prompt, user_prompt):
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY is missing")

    response = requests.post(
        url=OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 180
        }),
        timeout=120
    )

    if not response.ok:
        raise Exception(f"OpenRouter error {response.status_code}: {response.text}")

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


@ai_bp.route("/generate_message", methods=["POST"])
def generate_message():
    try:
        data = request.json or {}

        business_type = data.get("business_type", "business")
        tone = data.get("tone", "professional and friendly")
        goal = data.get("goal", "follow up with a lead")
        extra_context = data.get("extra_context", "")

        system_prompt = """
You write outbound follow-up messages for businesses.

Rules:
- Keep the message under 100 words
- Sound natural and professional
- Do not sound spammy
- Use exactly {{name}} in the message
- Suitable for SMS or email
- End with a yes or no question to encourage response
- Return ONLY the final message text
- Do NOT explain your answer
- Do NOT add phrases like "Here's an improved version"
- Do NOT add quotation marks
- Do NOT add notes, commentary, labels, or formatting
- No subject line
- No bullet points
- No markdown
""".strip()

        user_prompt = f"""
Business type: {business_type}
Tone: {tone}
Goal: {goal}
Extra context: {extra_context}

Write one short follow-up message now.
""".strip()

        message = call_openrouter(system_prompt, user_prompt)
        message = clean_ai_output(message)

        return jsonify({"message": message}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_bp.route("/improve_message", methods=["POST"])
def improve_message():
    try:
        data = request.json or {}

        original_message = data.get("message", "").strip()
        tone = data.get("tone", "professional and friendly")
        goal = data.get("goal", "improve clarity and conversions")

        if not original_message:
            return jsonify({"error": "Message is required"}), 400

        system_prompt = """
You improve outbound follow-up messages for businesses.

Rules:
- Keep the message under 100 words
- Make it clearer, more natural, and more persuasive
- Do not sound spammy
- Preserve {{name}} if it exists
- Suitable for SMS or email
- Return ONLY the final improved message
- Do NOT explain your answer
- Do NOT add phrases like "Here's an improved version"
- Do NOT add quotation marks
- Do NOT add notes, commentary, labels, or formatting
- No subject line
- No bullet points
- No markdown
""".strip()

        user_prompt = f"""
Tone: {tone}
Goal: {goal}

Improve this message:

{original_message}
""".strip()

        message = call_openrouter(system_prompt, user_prompt)
        message = clean_ai_output(message)

        return jsonify({"message": message}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500