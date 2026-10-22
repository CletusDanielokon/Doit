"""Sending email without extra packages.

Free hosts such as Render block normal SMTP ports, so the default is Brevo's HTTPS API
(free: 300 emails/day). Plain SMTP is supported as a fallback for your own computer or paid hosts.
"""
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage


class MailError(Exception):
    pass


def mail_enabled():
    return bool(os.environ.get("MAIL_FROM") and (os.environ.get("BREVO_API_KEY") or os.environ.get("SMTP_HOST")))


def send_email(to, subject, html, text=""):
    if not mail_enabled():
        raise MailError("Email is not set up on this server yet.")
    sender = os.environ["MAIL_FROM"]
    name = os.environ.get("MAIL_FROM_NAME", "Do.iT")
    if os.environ.get("BREVO_API_KEY"):
        return _brevo(to, subject, html, text, sender, name)
    return _smtp(to, subject, html, text, sender, name)


def _brevo(to, subject, html, text, sender, name):
    payload = json.dumps({"sender": {"name": name, "email": sender}, "to": [{"email": to}],
                          "subject": subject, "htmlContent": html, "textContent": text or subject}).encode()
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=payload, method="POST",
                                 headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json",
                                          "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "ignore")[:300]
        raise MailError(f"Brevo refused the email ({err.code}): {detail}")
    except (urllib.error.URLError, TimeoutError) as err:
        raise MailError(f"Could not reach the email service: {err}")


def _smtp(to, subject, html, text, sender, name):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"{name} <{sender}>", to
    msg.set_content(text or subject)
    msg.add_alternative(html, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as err:
        raise MailError(f"SMTP error: {err}")
