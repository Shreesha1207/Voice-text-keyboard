import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER or "noreply@xvoicekeyboard.com")

def send_email(to_email: str, subject: str, html_body: str):
    """Core function to send an email using SMTP."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning(f"SMTP not configured. Skipping email to {to_email}: {subject}")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Successfully sent email to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}. Error: {e}")
        return False

def send_welcome_email(to_email: str, display_name: str = None):
    name = display_name or "there"
    subject = "Welcome to XVoice! 🎉"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Welcome to XVoice, {name}!</h2>
        <p>We are thrilled to have you on board. You've just unlocked the fastest way to type—using your voice.</p>
        <p>Your <strong>14-day free trial</strong> starts right now. Make sure to download the desktop application and try it out!</p>
        <p>If you need any help, just reply to this email.</p>
        <br/>
        <p>Happy dictating,</p>
        <p><strong>The XVoice Team</strong></p>
      </body>
    </html>
    """
    return send_email(to_email, subject, html)

def send_trial_expired_email(to_email: str, display_name: str = None):
    name = display_name or "there"
    subject = "Your XVoice trial has ended ⏳"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Hi {name},</h2>
        <p>We hope you've enjoyed your 14-day free trial of XVoice.</p>
        <p>Your trial has officially ended, but you don't have to stop talking! Upgrade to XVoice Pro today to keep your voice-to-text access uninterrupted.</p>
        <p><a href="https://xvoicekeyboard.com/dashboard" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Upgrade to Pro</a></p>
        <br/>
        <p>Thanks for trying XVoice!</p>
        <p><strong>The XVoice Team</strong></p>
      </body>
    </html>
    """
    return send_email(to_email, subject, html)
