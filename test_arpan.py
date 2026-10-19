import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import send_email

result = send_email(
    'arpanchettri649@gmail.com', 
    'Test Email from RemedialPro', 
    'This is a test email to verify password reset works.', 
    '<p>This is a <b>test email</b> to verify password reset works.</p>'
)

print(f"Result: {result}")