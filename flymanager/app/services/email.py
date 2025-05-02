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
    today = datetime.now().strftime('%Y-%m-%d')
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
        if overdue_flips:
            email_subject = f"URGENT: Overdue Flips and Today's Flip Reminder for {today}"
        elif today_schedule:
             email_subject = f"Daily Flip Reminder for {today}"
        else:
             email_subject = f"<em>D. manager</em> Update: No Flips Due Today ({today})"


        # Start composing the email body with HTML
        email_body = f"<html><body>"
        email_body += f"<h2><em>D. manager</em> Daily Flip Reminders for {username} - {today}</h2>"

        # Add overdue flips
        if overdue_flips:
            email_body += "<h3 style='color: red;'>Overdue Flips:</h3><ul>"
            for overdue_item in overdue_flips:
                email_body += f"<li style='color: red;'>{overdue_item}</li>"
            email_body += "</ul><hr>" # Separator
        # else:
            # email_body += "<p>No overdue flips.</p><br>" # Optional

        # Add today's flip schedule
        email_body += f"<h3>Flip Schedule for Today ({today}):</h3>"
        if today_schedule:
            email_body += "<ul>"
            for item in today_schedule:
                email_body += f"<li>{item}</li>"
            email_body += "</ul>"
        else:
            email_body += "<p>No flips scheduled for today.</p>"

        email_body += "<br><p><small>This is an automated message from <em>D. manager</em>.</small></p>"
        email_body += "</body></html>"

        # Send the email using the 'mail' object from the app context
        msg = Message(subject=email_subject, recipients=[user_email], html=email_body)
        mail.send(msg)
        print(f"Flip reminder email sent to {username} at {user_email}")

    except Exception as e:
        print(f"Failed to send flip reminder email to {username}: {e}")
        import traceback
        traceback.print_exc() # Log full error for debugging