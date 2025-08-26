#!/usr/bin/env python3
"""
Test the beautified email template by creating a sample HTML file.
"""

import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def create_sample_email_html():
    """Create a sample email HTML file to preview the design."""

    # Sample data
    username = "Dr. Smith"
    today = datetime.now().strftime("%Y-%m-%d")

    # Sample overdue flips
    overdue_flips = [
        'Stock: <a href="#">Wild Type Control</a> (ID: WT001, Tray A - Position 12)',
        "Cross: Canton-S x Oregon-R (ID: CR002, Tray B - Position 5)",
        "Stock: UAS-GCaMP6s (ID: GC003, Tray C - Position 18)",
    ]

    # Sample today's flips
    today_schedule = [
        'Stock: <a href="#">w1118</a> (ID: W001, Tray A - Position 1)',
        "Cross: Driver x Effector (ID: DE004, Tray D - Position 22)",
    ]

    # Create the email HTML
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
                </div>
                
                <div class="section">
                    <h2 class="section-title urgent">
                        🚨 Overdue Flips
                        <span class="count-badge urgent-badge">{len(overdue_flips)}</span>
                    </h2>
                    <ul class="flip-list">
                        {''.join(f'<li class="flip-item overdue">{item}</li>' for item in overdue_flips)}
                    </ul>
                </div>
                <div class="divider"></div>
                
                <div class="section">
                    <h2 class="section-title today">
                        📅 Today's Schedule ({today})
                        <span class="count-badge today-badge">{len(today_schedule)}</span>
                    </h2>
                    <ul class="flip-list">
                        {''.join(f'<li class="flip-item today">{item}</li>' for item in today_schedule)}
                    </ul>
                </div>
            </div>
            
            <div class="footer">
                <p>This is an automated message from <strong><em>D. manager</em></strong></p>
                <p>🔬 Keeping your fly lines organized, one flip at a time</p>
            </div>
        </div>
    </body>
    </html>"""

    # Write to file
    with open("email_preview.html", "w", encoding="utf-8") as f:
        f.write(email_body)

    print("✅ Sample email HTML created: email_preview.html")
    print("🌐 Open this file in your browser to preview the email design")

    # Also create a "no flips" version
    no_flips_body = (
        email_body.replace('<div class="section">', '<div class="section">')
        .replace("📅 Today's Schedule", "📅 Today's Schedule")
        .replace(
            '<ul class="flip-list">',
            '<div class="no-flips-message">🎉 No flips scheduled for today! You can take a breather.</div><!--<ul class="flip-list">',
        )
        .replace("</ul>", "--></ul>")
    )

    with open("email_preview_no_flips.html", "w", encoding="utf-8") as f:
        f.write(
            no_flips_body.replace(overdue_flips[0], "").replace(
                '<div class="section">', '<div class="section" style="display:none;">'
            )
        )

    print("✅ No-flips email HTML created: email_preview_no_flips.html")


if __name__ == "__main__":
    create_sample_email_html()
