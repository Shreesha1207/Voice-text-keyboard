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
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER or "xvoicekeyboard@gmail.com")

def send_email(to_email: str, subject: str, html_body: str):
    """Core function to send an email using SMTP."""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.info("--- 📧 DEBUG EMAIL SENT (SMTP NOT CONFIGURED) ---")
        logger.info(f"To: {to_email}")
        logger.info(f"Subject: {subject}")
        logger.info(f"Body: {html_body[:200]}...") # truncate for logs
        logger.info("------------------------------------------------")
        return True

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
    subject = "Welcome to Xvoice! 🎉"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Welcome to Xvoice, {name}!</h2>
        <p>We are thrilled to have you on board. You've just unlocked the fastest way to type—using your voice.</p>
        <p>Your <strong>14-day free trial</strong> starts right now. Make sure to download the desktop application and try it out!</p>
        <p>If you need any help, just reply to this email.</p>
        <br/>
        <p>Happy dictating,</p>
        <p><strong>The Xvoice Team</strong></p>
      </body>
    </html>
    """
    return send_email(to_email, subject, html)

def send_trial_expired_email(to_email: str, display_name: str = None):
    name = display_name or "there"
    subject = "Your Xvoice trial has ended ⏳"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Hi {name},</h2>
        <p>We hope you've enjoyed your 14-day free trial of Xvoice.</p>
        <p>Your trial has officially ended, but you don't have to stop talking! Upgrade to Xvoice Pro today to keep your voice-to-text access uninterrupted.</p>
        <p><a href="https://xvoicekeyboard.com/dashboard" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Upgrade to Pro</a></p>
        <br/>
        <p>Thanks for trying Xvoice!</p>
        <p><strong>The Xvoice Team</strong></p>
      </body>
    </html>
    """
    return send_email(to_email, subject, html)

def send_password_reset_email(to_email: str, reset_link: str, display_name: str = None):
    name = display_name or "there"
    subject = "Reset your Xvoice password 🔐"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f9f9f9; padding: 20px;">
        <div style="max-width: 520px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
          <h2 style="margin-top: 0;">Hi {name}, forgot your password?</h2>
          <p>No worries — it happens! Click the button below to set a new password. This link is valid for <strong>15 minutes</strong>.</p>
          <p style="text-align: center; margin: 32px 0;">
            <a href="{reset_link}"
               style="background-color: #7c3aed; color: #fff; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px; display: inline-block;">
              Reset Password
            </a>
          </p>
          <p style="font-size: 13px; color: #888;">If the button doesn't work, copy and paste this link into your browser:<br/>
            <a href="{reset_link}" style="color: #7c3aed; word-break: break-all;">{reset_link}</a>
          </p>
          <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;"/>
          <p style="font-size: 12px; color: #aaa;">If you didn't request a password reset, you can safely ignore this email. Your password won't change.</p>
          <p style="margin-bottom: 0;"><strong>The Xvoice Team</strong></p>
        </div>
      </body>
    </html>
    """
    return send_email(to_email, subject, html)
