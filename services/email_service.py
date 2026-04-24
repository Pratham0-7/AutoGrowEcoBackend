from datetime import datetime
import boto3
import os
import re
import html

SES_REGION = "ap-south-1"
RESPONSE_BASE_URL = os.getenv("RESPONSE_BASE_URL", "http://127.0.0.1:5000")


def render_message(template, lead, variables=None):
    """
    Replaces lead placeholders + campaign variable placeholders.

    Supports both new backend template keys:
    {{your_name}}, {{product_service}}, {{help_with}}, {{main_problem}}, {{call_to_action}}

    And old frontend keys:
    {{sender_name}}, {{company_service}}, {{value_prop}}, {{pain_point}}, {{cta}}
    """
    result = template or ""

    lead = lead or {}
    variables = variables or {}

    replacements = {
        "name": lead.get("name") or "there",
        "their_company": lead.get("company") or lead.get("company_name") or "your company",
        "email": lead.get("email") or "",
        "phone": lead.get("phone") or "",
    }

    if isinstance(variables, dict):
        for key, value in variables.items():
            replacements[key] = "" if value is None else str(value)

    # Backward compatibility aliases
    aliases = {
        "your_name": ["sender_name", "your_name"],
        "product_service": ["company_service", "product_service"],
        "help_with": ["value_prop", "help_with"],
        "main_problem": ["pain_point", "main_problem"],
        "call_to_action": ["cta", "call_to_action"],
    }

    for canonical_key, possible_keys in aliases.items():
        if replacements.get(canonical_key):
            continue

        for possible_key in possible_keys:
            if replacements.get(possible_key):
                replacements[canonical_key] = replacements[possible_key]
                break

    for key, value in replacements.items():
        result = result.replace("{{" + key + "}}", str(value))

    # Remove any leftover {{placeholder}} so users/prospects never see raw template tags.
    result = re.sub(r"\{\{[^{}]+\}\}", "", result)

    return result


def build_response_links(lead_id):
    yes_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/yes"
    no_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/no"
    return yes_link, no_link


def build_email_html(final_message, yes_link, no_link):
    safe_message = html.escape(final_message or "").replace("\n", "<br>")
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background: #0D1F24; color: #FFFFFF; padding: 24px;">
        <div style="max-width: 600px; margin: 0 auto; background: #142830; padding: 28px; border-radius: 14px; border: 1px solid #1E3D47;">
          <p style="font-size: 15px; line-height: 1.7; margin-bottom: 28px; color: #FFFFFF;">
            {safe_message}
          </p>

          <div style="margin-top: 24px;">
            <a href="{yes_link}"
               style="display: inline-block; background: #22C55E; color: #0D1F24; text-decoration: none; padding: 12px 24px; border-radius: 10px; font-weight: 700; margin-right: 12px;">
              Yes
            </a>

            <a href="{no_link}"
               style="display: inline-block; background: #EF4444; color: #FFFFFF; text-decoration: none; padding: 12px 24px; border-radius: 10px; font-weight: 700;">
              No
            </a>
          </div>

          <p style="margin-top: 28px; font-size: 12px; color: #6B8E95; border-top: 1px solid #1E3D47; padding-top: 16px;">
            If the buttons don’t work, use these links:<br>
            Yes: {yes_link}<br>
            No: {no_link}
          </p>
        </div>
      </body>
    </html>
    """


def send_email_ses(to_email, subject, body_text, sender_email, html_body=None):
    ses_client = boto3.client("sesv2", region_name=SES_REGION)

    body = {"Text": {"Data": body_text or ""}}
    if html_body:
        body["Html"] = {"Data": html_body}

    return ses_client.send_email(
        FromEmailAddress=sender_email,
        Destination={"ToAddresses": [to_email]},
        Content={"Simple": {"Subject": {"Data": subject or "Follow-up"}, "Body": body}},
    )