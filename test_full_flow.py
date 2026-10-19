from app import create_app

app = create_app()
with app.test_client() as client:
    # Test registration page loads with departments
    resp = client.get('/register')
    print('Register Page Status:', resp.status_code)
    print('Has departments:', b'Computer Engineering' in resp.data)
    
    # Test registration with valid data
    resp = client.post('/register', data={
        'roll_number': 'TEST001',
        'full_name': 'Test Student',
        'email': 'test@example.com',
        'password': 'test123',
        'confirm_password': 'test123',
        'semester': '1',
        'section': 'A',
        'department_id': '1'
    }, follow_redirects=True)
    print('Registration Status:', resp.status_code)
    print('Registration Success:', b'login' in resp.data.lower() or resp.request.path == '/login')
    
    # Test login with new student
    resp = client.post('/login', data={'username': 'TEST001', 'password': 'test123'}, follow_redirects=True)
    print('New Student Login Status:', resp.status_code)
    print('New Student Login Success:', resp.request.path != '/login')