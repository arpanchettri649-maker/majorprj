# RemedialPro - Remedial Class Monitoring System

## 📋 Project Overview

**RemedialPro** is a comprehensive web-based academic support system designed to identify students who need additional help, organize remedial classes, and track attendance and progress. The system helps educational institutions manage weak students through structured remedial programs.

### Purpose
- Identify students with poor attendance or failed subjects
- Organize remedial classes with assigned teachers
- Track daily attendance for remedial sessions
- Monitor student progress and provide feedback
- Enable department-wise monitoring for administrators

---

## 🏗️ System Architecture

### Technology Stack
- **Backend**: Python with Flask framework
- **Database**: SQLite (remedial.db)
- **Frontend**: HTML templates with Bootstrap 5.3
- **Authentication**: Secure password hashing (werkzeug)
- **File Processing**: pandas for Excel/CSV parsing
- **UI Framework**: Bootstrap 5 + Custom CSS with Inter font

### Project Structure
```
D:\major project 14 oct\
├── app.py                 # Flask application factory
├── auth.py                # Authentication & password reset logic
├── db.py                  # Database connection & migrations
├── routes.py              # All route handlers (admin, teacher, student, auth)
├── academic.py            # Academic batch helpers
├── excel_parser.py        # Excel/CSV file parsing
├── ai_engine.py           # AI-powered student analysis
├── schema.sql             # Database schema definition
├── seed_data.py           # Initial data seeding
├── instance/
│   └── remedial.db        # SQLite database file
├── static/
│   ├── css/
│   │   └── style.css       # Custom styles
│   └── js/
│       └── app.js         # Client-side scripts
└── templates/
    ├── base.html           # Base template with sidebar
    ├── login.html         # Login page
    ├── register.html       # Student registration
    ├── forgot_password.html
    ├── reset_password.html
    ├── admin/              # Admin dashboard & pages
    ├── teacher/            # Teacher dashboard & pages
    └── student/            # Student dashboard & pages
```

---

## 👥 User Roles & Permissions

### 1. Super Admin
**Access**: System-wide monitoring and configuration
- View all departments' statistics (students, batches, attendance, failures)
- Manage departments and department administrators
- View failed login attempts
- Clear database (with confirmation)
- **Cannot**: Upload results, manage individual departments' data

### 2. Department Admin (Dept Admin)
**Access**: Department-level management
- Upload result sheets (Excel/CSV)
- Upload semester attendance data
- View failure list filtered by batch/semester
- Manage students in their department
- Create remedial batches
- Assign teachers to batches
- Schedule weekly timetable
- Manage peer mentors
- View analytics

### 3. Teacher
**Access**: Classroom management
- View assigned remedial batches
- Mark daily attendance for remedial classes
- Mark extra class attendance
- Submit teaching remarks
- View upcoming class schedule
- Track batch completion status

### 4. Student
**Access**: Personal dashboard
- View their failed subjects
- View overall attendance by subject
- View remedial class schedule
- View peer mentor information
- Submit feedback on remedial classes
- Enroll in extra classes

---

## 🎯 Core Features

### 1. Result Upload & Analysis
- **ATTC Backlog Analysis**: Upload Excel with semester-wise backlog data
- **Standard Result Format**: Upload result sheets with marks
- **Automatic Weak Student Identification**:
  - Students with failed subjects
  - Students with attendance below threshold (default 75%)
  - AI-powered analysis

### 2. Batch Management
- **Create Remedial Batches**: Organize students by subject and semester
- **Assign Teachers**: Link batches to faculty members
- **Assign Classrooms**: Allocate rooms with capacity limits
- **Track Status**: PENDING → SCHEDULED → IN_PROGRESS → COMPLETED
- **Batch-wise Organization**: All views organized by academic batches (2024-2027, 2025-2028, 2026-2029)

### 3. Timetable & Scheduling
- **Weekly Routine**: Define day-wise class schedules
- **Auto-Generate**: AI-powered timetable generation
- **Manual Editing**: Add/modify time slots
- **Room Allocation**: Assign classrooms to sessions

### 4. Attendance Tracking
- **Semester Attendance**: Upload department-wide attendance by subject
- **Daily Class Attendance**: Teachers mark attendance for each class
- **Extra Class Attendance**: Separate tracking for additional classes
- **Attendance Reports**: View attendance by student, subject, or batch

### 5. Peer Mentoring
- **Assign Mentors**: Top performers assigned to help weak students
- **Manual/Auto Assignment**: System can auto-assign or admins can manually assign
- **Track Mentorship**: One mentor per remedial batch

### 6. Student Feedback
- **Rating System**: Students rate remedial classes (1-5 stars)
- **Comments**: Provide qualitative feedback
- **Track Satisfaction**: Monitor teaching quality

### 7. Notifications
- **User Notifications**: Alerts for batch assignments, schedule changes
- **Unread Counter**: Badge showing unread notification count
- **System Messages**: Attendance below threshold, batch completion

### 8. Authentication & Security
- **Secure Login**: Username, employee ID, or roll number
- **Password Reset**: Secure token-based password recovery
- **Failed Login Tracking**: Log unsuccessful attempts
- **Audit Logging**: Track all system actions
- **Session Management**: 24-hour persistent sessions

---

## 🎨 Design & UI/UX

### Visual System
**Color Palette**:
- Primary: Blue (#2563eb) - Actions, links, buttons
- Teal: (#0f766e) - Success states, badges
- Navy (#0f172a) - Sidebar background
- Canvas: (#f8fafc) - Page background
- Surface: (#ffffff) - Card backgrounds

**Typography**:
- Font: Inter (Google Fonts)
- Weights: 300, 400, 500, 600, 700
- Clean, modern, professional

### Layout Components

#### 1. Sidebar Navigation
- **Fixed Sidebar**: Left-side navigation (260px width)
- **Dark Theme**: Navy gradient background
- **Active States**: Blue highlight with left border
- **Section Labels**: Uppercase, muted color
- **Responsive**: Hides on mobile, accessible via hamburger menu

#### 2. Dashboard Cards
- **Metric Cards**: Display key statistics (students, batches, attendance)
- **Stat Cards**: Highlight important metrics with colored left border
- **Shadow Effects**: Subtle elevation for depth
- **Rounded Corners**: 0.85rem radius for modern look

#### 3. Tables
- **Hover Effects**: Row highlighting on hover
- **Badges**: Status indicators (success, danger, warning)
- **Responsive**: Horizontal scroll on mobile
- **Striped Rows**: Alternating row colors

#### 4. Forms
- **Input Groups**: Password toggle buttons
- **Validation**: Client-side and server-side
- **Error Messages**: Alert boxes with color coding
- **Accessibility**: Proper labels and ARIA attributes

### Responsive Design
- **Mobile-First**: Works on all screen sizes
- **Breakpoints**:
  - Desktop: Full sidebar and content
  - Tablet: Collapsible sidebar
  - Mobile: Full-width sidebar with overlay
- **Touch-Friendly**: Large tap targets on mobile
- **Flexible Grid**: Bootstrap responsive columns

---

## 📊 Database Schema

### Core Tables

#### 1. departments
- id, code, name
- Stores academic departments (CO, ME, MT, CE, MC)

#### 2. users
- id, username, password_hash, role, full_name, department_id, is_active, created_at
- All user accounts (super_admin, dept_admin, teacher, student)

#### 3. students
- id, student_id, full_name, department_id, semester, section, academic_batch, registration_status, user_id, cgpa
- Student profiles linked to users table

#### 4. teachers
- id, employee_id, full_name, department_id, user_id
- Teacher profiles linked to users table

#### 5. subjects
- id, code, name, department_id
- Academic subjects with codes

#### 6. results
- id, student_id, student_name, department_id, semester, section, academic_batch, subject_id, marks, pass_marks, result, uploaded_at
- Semester exam results (PASS/FAIL)

#### 7. remedial_batches
- id, batch_code, department_id, subject_id, semester, section, academic_batch, teacher_id, classroom_id, status, created_at
- Remedial class groups

#### 8. remedial_students
- id, batch_id, student_id
- Students enrolled in remedial batches

#### 9. timetable
- id, batch_id, day_of_week, start_time, end_time, classroom_id, teacher_id
- Weekly class schedules

#### 10. attendance
- id, timetable_id, batch_id, student_id, attendance_date, status, marked_by
- Daily class attendance records

#### 11. semester_attendance
- id, student_id, semester, academic_batch, subject_id, attendance_percent, total_classes, classes_attended, updated_at
- Department-wide attendance by subject

#### 12. extra_class_attendance
- id, batch_id, student_id, attendance_date, status, marked_by, remarks, created_at
- Extra/remedial class attendance

#### 13. teacher_reports
- id, batch_id, teacher_id, remarks, completion_status, submitted_at
- Teacher progress reports

#### 14. student_feedback
- id, student_id, batch_id, rating, comments, submitted_at
- Student feedback on classes

#### 15. peer_mentors
- id, mentor_student_id, batch_id, assigned_by, assigned_at
- Peer mentor assignments

#### 16. notifications
- id, user_id, message, is_read, created_at
- User notifications

#### 17. audit_logs
- id, user_id, action, details, created_at
- System audit trail

#### 18. password_reset_tokens
- id, user_id, token, expires_at, used_at, created_at
- Password recovery tokens

#### 19. failed_logins
- id, username, ip_address, user_agent, attempted_at
- Failed login attempts

#### 20. system_settings
- key, value, updated_at
- Configurable settings (e.g., min attendance threshold)

---

## 🔄 Workflow

### 1. Result Upload Workflow
1. **Admin uploads** Excel/CSV result sheet
2. **System parses** file and extracts student data
3. **Preview** shows extracted data for verification
4. **Admin confirms** import
5. **System identifies** weak students (failed subjects, low attendance)
6. **AI engine** creates remedial batches automatically
7. **Admin reviews** and assigns teachers/rooms
8. **Timetable** generated for weekly classes

### 2. Class Attendance Workflow
1. **Teacher opens** batch detail page
2. **Selects date** from date picker
3. **Marks attendance** (Present/Absent) for each student
4. **Adds remarks** (optional)
5. **Submits** - saves to database
6. **System tracks** attendance percentage

### 3. Student Registration Workflow
1. **Student visits** registration page
2. **Enters** roll number, name, semester, section, department, password
3. **System validates** input
4. **Creates** user account (role: student)
5. **Creates** student profile
6. **Links** user to student via user_id
7. **Student can login** with roll number

### 4. Password Reset Workflow
1. **User clicks** "Forgot Password?"
2. **Enters** username/roll number/employee ID
3. **System generates** secure token (1-hour expiry)
4. **System shows** reset link (email in production)
5. **User clicks** link and enters new password
6. **System validates** token and updates password
7. **Token marked** as used
8. **User can login** with new password

---

## 🎯 Key Features by Role

### Super Admin Dashboard
- Department monitoring overview table
- Statistics: Students, batches, scheduled classes, avg attendance, failures
- Department management (create departments, admins, teachers)
- Failed login attempts monitoring
- Database clearing (with confirmation)

### Department Admin Dashboard
- Upload Result (ATTC Backlog Analysis)
- Upload Semester Attendance
- Failure List (filtered by batch/semester, export CSV)
- Students management
- Remedial Batches (create, assign teacher/room, view students)
- Timetable (weekly schedule, auto-generate)
- Peer Mentors (assign top performers)
- Teachers management
- Analytics (subject-wise failures, attendance trends)

### Teacher Dashboard
- Upcoming classes schedule
- Assigned remedial batches
- Mark attendance (daily class attendance)
- Extra class attendance checklist
- Teaching remarks and progress notes
- Batch completion tracking

### Student Dashboard
- Personal welcome message with academic info
- Failed subjects list
- Overall attendance by subject (bar chart)
- Remedial class schedule
- Peer mentor information
- Submit feedback on remedial classes
- Extra classes enrollment

---

## 🔒 Security Features

1. **Password Hashing**: werkzeug secure password hashing
2. **Token-Based Reset**: Secure 32-byte tokens with expiration
3. **Failed Login Tracking**: Log all unsuccessful attempts
4. **Audit Logging**: Track all system actions
5. **Session Management**: 24-hour persistent sessions
6. **Role-Based Access**: Login_required decorators with role checks
7. **SQL Injection Prevention**: Parameterized queries
8. **CSRF Protection**: Flask-WTF (if configured)
9. **Input Validation**: Server-side validation on all forms
10. **Foreign Key Constraints**: Database integrity

---

## 📱 Responsive Design

### Desktop (>992px)
- Full sidebar navigation
- Multi-column layouts
- Full feature availability

### Tablet (768px - 992px)
- Collapsible sidebar
- Adjusted grid layouts
- Touch-friendly controls

### Mobile (<768px)
- Full-width sidebar with overlay
- Single-column layouts
- Large tap targets
- Hamburger menu toggle
- Optimized table scrolling

---

## 🎨 Design Highlights

### Color Coding
- **Success**: Green badges for good attendance, completed batches
- **Danger**: Red badges for failures, low attendance
- **Warning**: Yellow badges for pending items
- **Info**: Blue badges for informational items
- **Primary**: Blue for primary actions

### Micro-interactions
- **Hover Effects**: Links, buttons, table rows
- **Focus States**: Form inputs, buttons
- **Transitions**: Smooth 0.28s animations
- **Active States**: Sidebar navigation highlights

### Visual Hierarchy
- **Typography**: Bold headings, muted secondary text
- **Spacing**: Consistent margins and padding
- **Shadows**: Subtle elevation for depth
- **Borders**: Light borders for separation

---

## 🚀 Running the Application

### Development
```bash
# Install dependencies
pip install flask pandas openpyxl

# Initialize database
python seed_data.py

# Run application
python app.py
```

### Access
- **URL**: http://localhost:5000
- **Super Admin**: superadmin / admin123
- **Dept Admins**: coadmin, meadmin, mtadmin, ceadmin, mcadmin / admin123
- **Teachers**: cot01, met01, etc. / teacher123

### Database Location
- **Path**: `D:\major project 14 oct\instance\remedial.db`
- **Format**: SQLite
- **Schema**: `schema.sql`

---

## 📈 Future Enhancements

1. **Email Integration**: Send password reset links via email
2. **SMS Notifications**: Send attendance alerts
3. **Parent Portal**: Allow parents to view student progress
4. **Mobile App**: Native mobile application
5. **Advanced Analytics**: Predictive analysis for at-risk students
6. **Certificate Generation**: Generate completion certificates
7. **Resource Sharing**: Upload study materials
8. **Video Conferencing**: Integration with online meeting tools

---

## 📝 Configuration

### Session Configuration
- **Secret Key**: "major-project-1c-remedial-secret-key"
- **Session Lifetime**: 24 hours
- **File Upload Limit**: 16MB

### Academic Batches
- **2024-2027**: First-year students
- **2025-2028**: Second-year students
- **2026-2029**: Third-year students

### Attendance Threshold
- **Default**: 75%
- **Configurable**: Via system_settings table

---

## 🛠️ Technical Details

### Python Version
- **Recommended**: Python 3.8+

### Dependencies
- Flask (web framework)
- pandas (Excel/CSV processing)
- openpyxl (Excel file support)
- werkzeug (security)
- Bootstrap 5.3 (UI framework)
- Chart.js (data visualization)

### Database
- **SQLite**: Lightweight, file-based
- **WAL Mode**: Better concurrency
- **Foreign Keys**: Enforced for data integrity
- **Indexes**: On frequently queried columns

---

## 📄 File Processing

### Supported Formats
- **Excel**: .xlsx, .xls, .xlsm
- **CSV**: .csv

### Parsing Features
- **Multi-row Headers**: Handles complex Excel formats
- **Dynamic Columns**: Adapts to varying column counts
- **Subject Detection**: Automatic subject column identification
- **Attendance Parsing**: Handles percentage and fraction formats
- **Backlog Analysis**: ATTC Backlog Analysis format support

---

## 🎓 Academic Features

### CGPA Tracking
- **Storage**: Students table
- **Display**: Student dashboard
- **Updates**: From ATTC Backlog Analysis

### Academic Batches
- **Calculation**: From enrollment/GR numbers
- **Formats**: 2024-2027, 2025-2028, 2026-2029
- **Normalization**: Handles various input formats

### Semester Management
- **Range**: 1-6 semesters
- **Section**: A, B, C, etc.
- **Year-Back**: Status tracking

---

## 🔍 AI-Powered Features

### Weak Student Identification
- **Criteria**: Failed subjects + Low attendance
- **Automatic Batch Creation**: AI creates remedial batches
- **Smart Allocation**: Assigns students to appropriate batches
- **Teacher Assignment**: Balances teacher workload

### Analytics
- **Subject Difficulty**: Most failed subjects
- **Attendance Trends**: Department-wide patterns
- **Batch Performance**: Student success rates

---

## 📊 Reports & Exports

### Failure List
- **Filter**: By batch, semester, subject
- **Export**: CSV format
- **Columns**: Student ID, Name, Batch, Semester, Subject, Result

### Attendance Reports
- **Per Subject**: Department-wide attendance
- **Per Student**: Individual attendance records
- **Low Attendance**: Below threshold students

---

## 🌟 User Experience

### Onboarding
1. **Seed Data**: Run seed_data.py to create initial accounts
2. **Registration**: Students self-register with roll number
3. **Teacher Accounts**: Created by department admin
4. **Access**: Role-based dashboards

### Navigation
- **Sidebar**: Consistent navigation across all dashboards
- **Breadcrumbs**: Not implemented (flat hierarchy)
- **Quick Links**: Frequently used features on dashboards

### Feedback
- **Flash Messages**: Success/error/info notifications
- **Form Validation**: Real-time feedback
- **Confirmation**: Destructive actions require confirmation

---

## 🎨 UI Components

### Cards
- **Shadow Cards**: Subtle elevation
- **Stat Cards**: Colored left border
- **Metric Cards**: Key statistics display

### Tables
- **Striped**: Alternating row colors
- **Hover**: Row highlighting
- **Responsive**: Horizontal scroll on mobile
- **Badges**: Status indicators

### Forms
- **Input Groups**: Combined inputs with buttons
- **Validation**: Client and server-side
- **Toggle Password**: Show/hide password
- **Selects**: Department, semester, batch dropdowns

### Charts
- **Bar Charts**: Attendance by subject
- **Library**: Chart.js 4.4.1
- **Responsive**: Adapts to container

---

## 🔧 Maintenance

### Database Maintenance
- **Backup**: Copy `instance/remedial.db`
- **Migration**: Automatic on app start
- **Cleanup**: Expired tokens cleanup

### Logs
- **Audit Logs**: All user actions
- **Failed Logins**: Security monitoring
- **System Errors**: Error tracking

---

## 📞 Support

### Default Credentials
- **Super Admin**: superadmin / admin123
- **Dept Admin**: coadmin / admin123
- **Teacher**: cot01 / teacher123

### Common Issues
1. **Registration Fails**: Run seed_data.py to create departments
2. **Login Fails**: Check credentials, ensure account is active
3. **Upload Fails**: Check file format (xlsx, xls, xlsm, csv)
4. **Database Locked**: Ensure only one app instance running

---

## 🎓 Academic Context

### Diploma Programs
- **Duration**: 3 years
- **Semesters**: 6 semesters
- **Departments**: Computer, Mechanical, Mechatronics, Civil, Medical Lab Technology

### Remedial Classes
- **Timing**: As per department schedule
- **Duration**: Until backlog cleared
- **Frequency**: Based on timetable
- **Monitoring**: Continuous attendance and progress tracking

---

## 🌟 Conclusion

RemedialPro is a comprehensive, production-ready academic support system that helps educational institutions identify at-risk students, organize remedial programs, and track student progress. The system features a modern, responsive design, robust security, and AI-powered analytics to ensure students receive the support they need to succeed academically.

The system is fully functional with all core features implemented, including result upload, batch management, attendance tracking, peer mentoring, student feedback, and comprehensive dashboards for all user roles.
