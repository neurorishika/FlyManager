from datetime import datetime
from html import escape

from flask import current_app
from flask_mail import Message

# Import app context variables and mongo utils
from flymanager.app import db, mail
from flymanager.app.services.labels import build_label_pdf
from flymanager.utils.mongo import get_flip_schedule, get_user_email

# Labels for a large overdue backlog can grow past what an SMTP relay will
# accept. Past this, send the reminder without the PDF rather than have the
# whole message bounce.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def mail_config_status():
    """Report each mail setting as set or missing, for the admin readout.

    In 2026-09 a redeploy dropped MAIL_SUPPRESS_SEND=0 and SMTP_SENDER from
    the production stack. Sending stopped for days because the only signal
    was an INFO log below the configured level. This turns that state into
    something an admin can read off a page.

    Never includes the password value -- only whether one is set.
    """
    config = current_app.config
    suppressed = bool(config.get("MAIL_SUPPRESS_SEND"))
    server = config.get("MAIL_SERVER")
    sender = config.get("MAIL_DEFAULT_SENDER")

    problems = []
    if suppressed:
        problems.append(
            "MAIL_SUPPRESS_SEND is on, so no mail is sent. Set MAIL_SUPPRESS_SEND=0."
        )
    if not server:
        problems.append("SMTP_SERVER is not set.")
    if not sender:
        problems.append("SMTP_SENDER is not set.")

    return {
        "suppressed": suppressed,
        "server": server or "",
        "port": config.get("MAIL_PORT"),
        "sender": sender or "",
        "username": config.get("MAIL_USERNAME") or "",
        "password_set": bool(config.get("MAIL_PASSWORD")),
        "use_tls": bool(config.get("MAIL_USE_TLS")),
        "problems": problems,
        "ready": not problems,
    }


def mail_is_configured():
    """Return True when outbound mail has enough configuration to send."""
    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        return False

    required_values = (
        current_app.config.get("MAIL_SERVER"),
        current_app.config.get("MAIL_DEFAULT_SENDER"),
    )
    return all(required_values)


def send_test_email(recipient):
    """Send a short test message to one address.

    Deliberately lets SMTP exceptions propagate: the caller shows the error
    to the admin. A test that swallows its own failure is the bug this
    feature exists to prevent.
    """
    msg = Message(
        subject="✅ D. manager test email",
        recipients=[recipient],
        html=(
            "<html><body style=\"font-family: Arial, sans-serif; color: #1f2933;\">"
            "<h2>Mail is working</h2>"
            "<p>This is a test message from <strong><em>D. manager</em></strong>. "
            "If you are reading it, outbound email is configured correctly and "
            "flip reminders can be delivered.</p>"
            f"<p style=\"font-size: 0.9rem; color: #52606d;\">Sent {escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</p>"
            "</body></html>"
        ),
    )
    mail.send(msg)


def _attach_flip_labels(msg, username, today, schedule):
    """Attach print-ready labels for everything due today or overdue.

    Every failure here is swallowed on purpose: a label problem must never
    cost the user the reminder itself, which is the part that tells them
    what to flip.
    """
    try:
        dates = sorted(date for date in schedule if date <= today)
        if not dates:
            return

        pdf_bytes, count = build_label_pdf(username, dates, db)
        if not pdf_bytes:
            return

        if len(pdf_bytes) > MAX_ATTACHMENT_BYTES:
            current_app.logger.warning(
                "Flip label PDF for %s is %d bytes, over the %d limit. Sending "
                "the reminder without it.",
                username, len(pdf_bytes), MAX_ATTACHMENT_BYTES,
            )
            return

        msg.attach(
            f"flip_labels_{today}.pdf", "application/pdf", pdf_bytes
        )
        current_app.logger.info(
            "Attached %d flip labels to the reminder for %s", count, username
        )
    except Exception as e:
        current_app.logger.exception(
            "Could not attach flip labels for %s, sending reminder without them: %s",
            username, e,
        )


def send_flip_reminder_email(username):
    """
    Sends an email reminder to the user about today's flip schedule and any overdue flips.
    Expects to be run within a Flask application context.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if not mail_is_configured():
            current_app.logger.info("Mail is not configured. Skipping reminder email delivery.")
            return

        # These functions need access to the 'db' object from the app context
        schedule = get_flip_schedule(username, db)
        user_email = get_user_email(username, db)

        if not user_email:
            current_app.logger.info("No email found for user %s. Skipping reminder.", username)
            return

        overdue_flips = []
        for date, items in schedule.items():
            if date < today:
                overdue_flips.extend(items)

        today_schedule = schedule.get(today, [])
        num_vials = len(today_schedule) + len(overdue_flips)

        if num_vials == 0:
            # Optionally send a "No flips today" email or just log
            current_app.logger.info(
                "No flips scheduled or overdue for %s for %s", today, username
            )
            # return # Uncomment if you don't want emails when there's nothing to flip

        safe_username = escape(username)

        # Compose the email subject
        if overdue_flips and today_schedule:
            email_subject = f"🚨 URGENT: {len(overdue_flips)} Overdue + {len(today_schedule)} Due Today | D. manager"
        elif overdue_flips:
            email_subject = f"🚨 URGENT: {len(overdue_flips)} Overdue Flip{'s' if len(overdue_flips) > 1 else ''} | D. manager"
        elif today_schedule:
            email_subject = f"📅 {len(today_schedule)} Flip{'s' if len(today_schedule) > 1 else ''} Due Today ({today}) | D. manager"
        else:
            email_subject = f"🎉 No Flips Today ({today}) | D. manager"

        # Start composing the email body with HTML and CSS
        email_body = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 700px;
                    margin: 0 auto;
                    background-color: #f8f9fa;
                    padding: 0;
                }}
                .email-container {{
                    background-color: #ffffff;
                    margin: 20px;
                    border-radius: 10px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px 25px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 28px;
                    font-weight: 300;
                }}
                .header .subtitle {{
                    margin: 5px 0 0 0;
                    font-size: 16px;
                    opacity: 0.9;
                }}
                .content {{
                    padding: 30px 25px;
                }}
                .greeting {{
                    font-size: 18px;
                    margin-bottom: 25px;
                    color: #444;
                }}
                .section {{
                    margin-bottom: 30px;
                }}
                .section-title {{
                    font-size: 20px;
                    font-weight: 600;
                    margin-bottom: 15px;
                    padding-bottom: 8px;
                    border-bottom: 2px solid #e9ecef;
                }}
                .urgent {{
                    color: #dc3545;
                    border-bottom-color: #dc3545;
                }}
                .today {{
                    color: #28a745;
                    border-bottom-color: #28a745;
                }}
                .no-flips {{
                    color: #6c757d;
                    border-bottom-color: #6c757d;
                }}
                .flip-list {{
                    list-style: none;
                    padding: 0;
                }}
                .flip-item {{
                    background-color: #f8f9fa;
                    border: 1px solid #e9ecef;
                    border-radius: 6px;
                    padding: 12px 15px;
                    margin-bottom: 8px;
                    transition: all 0.2s ease;
                }}
                .flip-item:hover {{
                    background-color: #e9ecef;
                    transform: translateX(2px);
                }}
                .flip-item.overdue {{
                    background-color: #f8d7da;
                    border-color: #f5c6cb;
                    color: #721c24;
                }}
                .flip-item.today {{
                    background-color: #d4edda;
                    border-color: #c3e6cb;
                    color: #155724;
                }}
                .no-flips-message {{
                    text-align: center;
                    padding: 20px;
                    background-color: #e3f2fd;
                    border-radius: 6px;
                    color: #1976d2;
                    font-style: italic;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    text-align: center;
                    padding: 20px 25px;
                    border-top: 1px solid #e9ecef;
                    color: #6c757d;
                    font-size: 14px;
                }}
                .divider {{
                    height: 2px;
                    background: linear-gradient(90deg, transparent, #dc3545, transparent);
                    margin: 20px 0;
                }}
                .count-badge {{
                    display: inline-block;
                    background-color: #007bff;
                    color: white;
                    padding: 4px 8px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: bold;
                    margin-left: 8px;
                }}
                .urgent-badge {{
                    background-color: #dc3545;
                }}
                .today-badge {{
                    background-color: #28a745;
                }}
                a {{
                    color: #007bff;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header">
                    <h1>🧬 <em>D. manager</em></h1>
                    <p class="subtitle">Daily Flip Reminder System</p>
                </div>
                
                <div class="content">
                    <div class="greeting">
                        Hello <strong>{safe_username}</strong>,
                    </div>"""

        # Add overdue flips section
        if overdue_flips:
            email_body += f"""
                    <div class="section">
                        <h2 class="section-title urgent">
                            🚨 Overdue Flips
                            <span class="count-badge urgent-badge">{len(overdue_flips)}</span>
                        </h2>
                        <ul class="flip-list">"""
            for overdue_item in overdue_flips:
                email_body += f'<li class="flip-item overdue">{escape(overdue_item)}</li>'
            email_body += """
                        </ul>
                    </div>
                    <div class="divider"></div>"""

        # Add today's flip schedule
        email_body += f"""
                    <div class="section">
                        <h2 class="section-title {'today' if today_schedule else 'no-flips'}">
                            📅 Today's Schedule ({today})
                            {f'<span class="count-badge today-badge">{len(today_schedule)}</span>' if today_schedule else ''}
                        </h2>"""

        if today_schedule:
            email_body += '<ul class="flip-list">'
            for item in today_schedule:
                email_body += f'<li class="flip-item today">{escape(item)}</li>'
            email_body += "</ul>"
        else:
            email_body += '<div class="no-flips-message">🎉 No flips scheduled for today! You can take a breather.</div>'

        email_body += """
                    </div>
                </div>
                
                <div class="footer">
                    <p>This is an automated message from <strong><em>D. manager</em></strong></p>
                    <p>🔬 Keeping your fly lines organized, one flip at a time</p>
                </div>
            </div>
        </body>
        </html>"""

        # Send the email using the 'mail' object from the app context
        msg = Message(subject=email_subject, recipients=[user_email], html=email_body)
        _attach_flip_labels(msg, username, today, schedule)
        mail.send(msg)
        current_app.logger.info("Flip reminder email sent to %s at %s", username, user_email)

    except Exception as e:
        current_app.logger.exception(
            "Failed to send flip reminder email to %s: %s", username, e
        )


def send_password_reset_email(username, reset_url):
    """Send a password reset link to the user's stored recovery email."""
    try:
        if not mail_is_configured():
            current_app.logger.info(
                "Mail is not configured. Skipping password reset email delivery."
            )
            return False

        user_email = get_user_email(username, db)
        if not user_email:
            current_app.logger.info(
                "No recovery email found for user %s. Skipping password reset email.",
                username,
            )
            return False

        safe_username = escape(username)
        safe_reset_url = escape(reset_url)
        email_subject = "Reset your D. manager password"
        email_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #1f2933; line-height: 1.6;">
            <div style="max-width: 640px; margin: 0 auto; padding: 24px;">
                <h2 style="margin-bottom: 8px;">Password reset requested</h2>
                <p>Hello <strong>{safe_username}</strong>,</p>
                <p>A request was made to reset your D. manager password. Use the button below within the next hour to choose a new password.</p>
                <p style="margin: 28px 0;">
                    <a href="{safe_reset_url}" style="background: #2563eb; color: #ffffff; padding: 12px 18px; border-radius: 8px; text-decoration: none; font-weight: 600;">Reset password</a>
                </p>
                <p>If you did not request this, you can ignore this email.</p>
                <p style="font-size: 0.9rem; color: #52606d;">If the button does not work, paste this link into your browser:</p>
                <p style="font-size: 0.9rem; word-break: break-all; color: #52606d;">{safe_reset_url}</p>
            </div>
        </body>
        </html>
        """
        msg = Message(subject=email_subject, recipients=[user_email], html=email_body)
        mail.send(msg)
        current_app.logger.info(
            "Password reset email sent to %s at %s", username, user_email
        )
        return True
    except Exception as e:
        current_app.logger.exception(
            "Failed to send password reset email to %s: %s", username, e
        )
        return False
