import os

email = os.environ.get('EMAIL_ADDRESS')
password = os.environ.get('EMAIL_APP_PASSWORD')

print(f"EMAIL_ADDRESS: {repr(email)}")
print(f"EMAIL_APP_PASSWORD: {'SET' if password else 'NOT SET'}")

if email and password:
    print("\n✓ Both environment variables are loaded!")
else:
    print("\n✗ Environment variables NOT loaded!")
    print("Make sure to RESTART your terminal/VS Code after setting them.")