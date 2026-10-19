import os
import sys
import socket
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test sending an email using port 587 with STARTTLS (alternative to 465)
to_email = "remedialclassattc@gmail.com"
subject = "Test Email from RemedialPro (port 587)"
body = "This is a test email to verify the email configuration works on port 587."
html_body = "<p>This is a <b>test email</b> to verify the email configuration works on port 587.</p>"

email_address = os.environ.get('EMAIL_ADDRESS')
email_password = os.environ.get('EMAIL_APP_PASSWORD')

print(f"Testing Gmail SMTP on port 587 with STARTTLS")
print(f"Sending test email to: {to_email}")
print(f"From: {email_address}")
print("-" * 50)

try:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_address
    msg["To"] = to_email
    
    text_part = MIMEText(body, "plain")
    msg.attach(text_part)
    
    if html_body:
        html_part = MIMEText(html_body, "html")
        msg.attach(html_part)
    
    print("Connecting to Gmail SMTP on port 587 (timeout=30s)...")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        print("Connected. Starting TLS...")
        server.starttls()
        print("TLS started. Logging in...")
        server.login(email_address, email_password)
        print("Login successful. Sending message...")
        server.send_message(msg)
        print("Message sent. Closing connection...")
        server.quit()
    
    print("-" * 50)
    print("✓ Email sent successfully on port 587!")
    
except smtplib.SMTPAuthenticationError as e:
    print("-" * 50)
    print(f"✗ SMTP Authentication FAILED: {e}")
    print("\nPossible causes:")
    print("1. Invalid App Password - regenerate it at https://myaccount.google.com/apppasswords")
    print("2. 2-Step Verification not enabled on Google Account")
    print("3. App Password was revoked or expired")
    
except smtplib.SMTPConnectError as e:
    print("-" * 50)
    print(f"✗ SMTP Connection FAILED: {e}")
    print("Network/firewall issue")
    
except smtplib.SMTPRecipientsRefused as e:
    print("-" * 50)
    print(f"✗ Recipient refused: {e}")
    
except socket.timeout:
    print("-" * 50)
    print("✗ Connection TIMED OUT after 30 seconds")
    print("Firewall/antivirus may be blocking the connection")
    
except Exception as e:
    print("-" * 50)
    print(f"✗ Email FAILED with error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()