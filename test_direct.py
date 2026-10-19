import sys
sys.path.insert(0, r'd:\major project 14 oct')

from auth import send_email

print("Testing email send to arpanchettri649@gmail.com...")
result = send_email(
    'arpanchettri649@gmail.com', 
    'Test from RemedialPro - Password Reset', 
    'This is a test email to verify password reset works.', 
    '<p>This is a <b>test email</b> to verify password reset works.</p>'
)

print(f"Result: {result}")
if result:
    print("SUCCESS: Email sent!")
else:
    print("FAILED: Check console for errors")