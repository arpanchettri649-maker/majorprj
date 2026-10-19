from werkzeug.security import check_password_hash

hash = 'scrypt:32768:8:1$p1wMGwYtLZSmtbnh$e25c03af01327e1d0e7da5f71e48337a84bf34e88cca3ee65f2e5c4a60ab81a3423900b32b9191e593b32098bcffc442db80a8e7998c321e6381ab38eb3e6b61'
print('student123:', check_password_hash(hash, 'student123'))
print('admin123:', check_password_hash(hash, 'admin123'))