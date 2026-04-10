from datetime import datetime
import boto3
import os

SES_REGION = "ap-south-1"
RESPONSE_BASE_URL = os.getenv("RESPONSE_BASE_URL", "http://127.0.0.1:5000")


def render_message(template, lead):
    return template.replace("{{name}}", lead.get("name", "there"))


def build_response_links(lead_id):
    yes_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/yes"
    no_link = f"{RESPONSE_BASE_URL}/respond/{lead_id}/no"
    return yes_link, no_link


def build_email_html(final_message, yes_link, no_link):
    safe_message = final_message.replace("\n", "<br>")
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; background: #0a0a0a; color: white; padding: 24px;">
        <div style="max-width: 600px; margin: 0 auto; background: #111; padding: 24px; border-radius: 12px; border: 1px solid #222;">
          <p style="font-size: 16px; line-height: 1.6; margin-bottom: 24px;">
            {safe_message}
          </p>

          <div style="margin-top: 24px;">
            <a href="{yes_link}"
               style="display: inline-block; background: #22c55e; color: black; text-decoration: none; padding: 12px 20px; border-radius: 10px; font-weight: bold; margin-right: 12px;">
              Yes
            </a>

            <a href="{no_link}"
               style="display: inline-block; background: #ef4444; color: white; text-decoration: none; padding: 12px 20px; border-radius: 10px; font-weight: bold;">
              No
            </a>
          </div>

          <p style="margin-top: 24px; font-size: 12px; color: #aaa;">
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

    body = {"Text": {"Data": body_text}}
    if html_body:
        body["Html"] = {"Data": html_body}

    return ses_client.send_email(
        FromEmailAddress=sender_email,
        Destination={"ToAddresses": [to_email]},
        Content={"Simple": {"Subject": {"Data": subject}, "Body": body}},
    )