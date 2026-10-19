from app import create_app

app = create_app()
with app.test_client() as client:
    # Test login with superadmin
    resp = client.post('/login', data={'username': 'superadmin', 'password': 'admin123'}, follow_redirects=True)
    print('Login Status:', resp.status_code)
    print('Login Success:', b'dashboard' in resp.data.lower() or b'logout' in resp.data.lower() or resp.request.path != '/login')
    
    # Test login with student (password is student123)
    resp = client.post('/login', data={'username': 'A24DCE07', 'password': 'student123'}, follow_redirects=True)
    print('Student Login Status:', resp.status_code)
    print('Student Login Success:', resp.request.path != '/login')
    if resp.request.path == '/login':
        print('Response:', resp.data[:500])
    
    # Test login with wrong password
    resp = client.post('/login', data={'username': 'superadmin', 'password': 'wrong'}, follow_redirects=False)
    print('Wrong Password Status:', resp.status_code)
    print('Wrong Password Rejected:', resp.status_code == 200 and b'Invalid username or password' in resp.data)