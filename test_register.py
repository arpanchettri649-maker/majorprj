from app import create_app

app = create_app()
with app.test_client() as client:
    resp = client.get('/register')
    print('Status:', resp.status_code)
    print('Departments in response:', b'Computer Engineering' in resp.data and b'Mechanical Engineering' in resp.data)
    # Check if select options are present
    if b'<option value="1"' in resp.data and b'<option value="2"' in resp.data:
        print('Department dropdown options found!')
    else:
        print('WARNING: Department dropdown options NOT found')
        # Print part of response for debugging
        print(resp.data[:2000])