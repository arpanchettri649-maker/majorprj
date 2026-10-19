# Email Configuration Guide for RemedialPro

This guide explains how to configure Gmail SMTP for password reset emails in your RemedialPro project.

---

## 📧 Overview

The password reset feature now sends secure emails via Gmail SMTP with the following configuration:
- **SMTP Server**: smtp.gmail.com
- **Port**: 465 (SSL)
- **Token Expiry**: 15 minutes
- **Security**: Single-use tokens, SSL encryption

---

## 🔐 Step 1: Generate Gmail App Password

Google no longer allows using your regular Gmail password for SMTP. You must generate an App Password.

### Instructions:
1. Go to your Google Account settings: https://myaccount.google.com/
2. Click **Security** in the left sidebar
3. Under "2-Step Verification", ensure it's enabled (required for App Passwords)
4. Scroll down to "App passwords" and click on it
5. If prompted, enter your Google password
6. Under "Select app", choose **Mail**
7. Under "Select device", choose **Other (Custom name)**
8. Enter a name like "RemedialPro" and click **GENERATE**
9. Copy the 16-character password (shown as xxxx xxxx xxxx xxxx) - **save this securely**

⚠️ **Important**: You will only see this password once. Save it in a secure location.

---

## 💻 Step 2: Configure Environment Variables (Local Development)

### Windows (Command Prompt)
```cmd
set EMAIL_ADDRESS=your-email@gmail.com
set EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### Windows (PowerShell)
```powershell
$env:EMAIL_ADDRESS = "your-email@gmail.com"
$env:EMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"
```

### Windows (System Environment Variables - Recommended)
1. Press `Win + R`, type `sysdm.cpl`, and press Enter
2. Click the **Advanced** tab
3. Click **Environment Variables**
4. Under "User variables", click **New**
5. Variable name: `EMAIL_ADDRESS`
6. Variable value: `your-email@gmail.com`
7. Click **OK**
8. Repeat for `EMAIL_APP_PASSWORD` with your generated app password
9. Click **OK** on all dialogs
10. **Restart your terminal/command prompt** for changes to take effect

### Mac/Linux (Terminal)
```bash
export EMAIL_ADDRESS=your-email@gmail.com
export EMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

To make it permanent, add to your shell profile (~/.bashrc, ~/.zshrc, etc.):
```bash
echo 'export EMAIL_ADDRESS=your-email@gmail.com' >> ~/.bashrc
echo 'export EMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"' >> ~/.bashrc
source ~/.bashrc
```

---

## 🌐 Step 3: Configure Environment Variables (Render Production)

### Method 1: Using Render Dashboard (Recommended)
1. Go to your Render dashboard: https://dashboard.render.com/
2. Select your web service
3. Go to the **Environment** tab
4. Click **Add Environment Variable**
5. Add the following:
   - **Key**: `EMAIL_ADDRESS`
   - **Value**: `your-email@gmail.com`
   - Click **Save**
6. Add another environment variable:
   - **Key**: `EMAIL_APP_PASSWORD`
   - **Value**: `xxxx xxxx xxxx xxxx` (your generated app password)
   - Click **Save**
7. Your service will automatically restart with the new variables

### Method 2: Using Render CLI
```bash
render env set EMAIL_ADDRESS=your-email@gmail.com
render env set EMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

### Method 3: Using render.yaml
Add to your `render.yaml` file:
```yaml
services:
  - type: web
    name: remedialpro
    envVars:
      - key: EMAIL_ADDRESS
        value: your-email@gmail.com
      - key: EMAIL_APP_PASSWORD
        value: xxxx xxxx xxxx xxxx
```

---

## ✅ Step 4: Verify Configuration

### Test Locally
1. Open a terminal/command prompt
2. Run your application:
   ```bash
   python app.py
   ```
3. Go to http://localhost:5000/forgot-password
4. Enter a registered roll number (e.g., the one you created during registration)
5. Check your Gmail inbox (and spam folder) for the reset email
6. Click the link in the email to reset your password

### Test on Production
1. Deploy your updated code to Render
2. Go to your Render URL (e.g., https://your-app.onrender.com/forgot-password)
3. Enter a registered roll number
4. Check your Gmail inbox for the reset email
5. Click the link to reset your password

---

## 🔍 Troubleshooting

### Issue: "EMAIL_ADDRESS or EMAIL_APP_PASSWORD environment variables not set"
**Solution**: Ensure both environment variables are set correctly. Restart your terminal after setting them.

### Issue: "Authentication failed" or "Invalid credentials"
**Solution**:
- Verify your Gmail App Password is correct
- Ensure 2-Step Verification is enabled on your Google Account
- Regenerate the App Password if you suspect it's compromised

### Issue: Email not received
**Solution**:
- Check your Gmail spam folder
- Verify the email address in the database is correct
- Check application logs for SMTP errors
- Ensure your network allows SMTP connections (port 465)

### Issue: "SSL connection error"
**Solution**:
- Ensure port 465 is not blocked by your firewall
- Check if your ISP blocks SMTP ports (some do)
- Try using a different network

### Issue: Tokens expire too quickly
**Solution**: Currently set to 15 minutes. You can change this in `auth.py`:
```python
# In generate_reset_token function
def generate_reset_token(user_id, expiry_minutes=15):  # Change 15 to desired minutes
```

---

## 🔒 Security Best Practices

1. **Never commit credentials to git**: Environment variables are not in your code
2. **Use separate email**: Consider using a dedicated Gmail account for the app
3. **Rotate passwords**: Change your App Password periodically
4. **Monitor logs**: Check for unauthorized email sending attempts
5. **Enable 2FA**: Ensure 2-Step Verification is always enabled on your Google Account
6. **Limit permissions**: The App Password only has email sending permissions

---

## 📝 Notes

- The password reset link expires in **15 minutes** for security
- Each token can only be used **once**
- Old tokens are automatically cleaned up when requesting a new reset
- The system uses **SSL encryption** for all email communications
- For **local testing**, the reset URL will use `http://localhost:5000`
- For **production**, the reset URL will use your Render URL automatically

---

## 🔄 Database Migration

The email field has been added to the `students` table. Existing students will have `NULL` for email. They will need to update their email through their profile (if you add that feature) or re-register.

To add email to existing students via SQL:
```sql
UPDATE students SET email = 'student-email@gmail.com' WHERE student_id = 'ROLL_NUMBER';
```

---

## 🧪 Testing Checklist

- [ ] Set environment variables locally
- [ ] Test password reset with a registered student account
- [ ] Verify email is received in Gmail inbox
- [ ] Verify email is received in Gmail spam folder (for testing)
- [ ] Click the reset link and set a new password
- [ ] Try logging in with the new password
- [ ] Try using an expired token (should show error)
- [ ] Try using the same token twice (should show error)
- [ ] Configure environment variables on Render
- [ ] Deploy and test on production
- [ ] Verify production emails are sent successfully

---

## 📞 Support

If you encounter issues:
1. Check the application console/logs for error messages
2. Verify your Gmail App Password is valid
3. Ensure environment variables are set correctly
4. Check Render logs for deployment errors
5. Review Google Account security settings

---

## 🎯 Next Steps

After configuration is complete:
1. Students will need to provide their email during registration
2. Existing students may need to update their email address
3. Consider adding an "Update Email" feature to the student profile
4. Monitor email delivery rates and spam reports
5. Set up email templates for other notifications (attendance alerts, batch assignments, etc.)

---

**Generated for RemedialPro Project**
