from datetime import datetime

from flask_mail import Message

# Import app context variables and mongo utils
from flymanager.app import db, mail
from flymanager.utils.mongo import get_flip_schedule, get_user_email


def send_flip_reminder_email(username):
    """
    Sends an email reminder to the user about today's flip schedule and any overdue flips.
    Expects to be run within a Flask application context.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        # These functions need access to the 'db' object from the app context
        schedule = get_flip_schedule(username, db)
        user_email = get_user_email(username, db)

        if not user_email:
            print(f"No email found for user {username}. Skipping reminder.")
            return

        overdue_flips = []
        for date, items in schedule.items():
            if date < today:
                overdue_flips.extend(items)

        today_schedule = schedule.get(today, [])
        num_vials = len(today_schedule) + len(overdue_flips)

        if num_vials == 0:
            # Optionally send a "No flips today" email or just log
            print(f"No flips scheduled or overdue for {today} for {username}")
            # return # Uncomment if you don't want emails when there's nothing to flip

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
                        Hello <strong>{username}</strong>,
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
                email_body += f'<li class="flip-item overdue">{overdue_item}</li>'
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
                email_body += f'<li class="flip-item today">{item}</li>'
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
        mail.send(msg)
        print(f"Flip reminder email sent to {username} at {user_email}")

    except Exception as e:
        print(f"Failed to send flip reminder email to {username}: {e}")
        import traceback

        traceback.print_exc()  # Log full error for debugging
