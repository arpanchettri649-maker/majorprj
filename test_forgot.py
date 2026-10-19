from app import create_app

app = create_app()
with app.test_client() as client:
    # Test forgot password for newly registered student TEST001
    resp = client.post('/forgot-password', data={'username': 'TEST001'}, follow_redirects=True)
    print('Forgot Password Status:', resp.status_code)
    print('Response:', resp.data[:1500].decode('utf-8', errors='replace'))