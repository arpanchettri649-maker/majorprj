# Major Project 1C — Remedial Class Allocation System

AI Remedial Class Management & Automatic Scheduling System for polytechnic colleges.

## Features
- Admin uploads Excel/CSV → failure list extracted automatically
- 5 department admins (one per trade) + super admin
- AI timetable generation with **manual routine editing**
- Mentor (teacher) marks attendance and sees upcoming classes this week
- Student views attendance, routine, and submits feedback

## Roles & Demo Logins
| Role | Username | Password |
|------|----------|----------|
| Super Admin | superadmin | admin123 |
| CO Admin | coadmin | admin123 |
| ME Admin | meadmin | admin123 |
| MT Admin | mtadmin | admin123 |
| CE Admin | ceadmin | admin123 |
| MC Admin | mcadmin | admin123 |
| Mentor/Teacher | cot01 | teacher123 |
| Student | (auto-created on upload) | student123 |

## Setup
```bash
cd "major project 1c"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python seed_data.py
python app.py
```

Open http://localhost:5000

## Upload Format

### College Excel (automatic detection)
Your 79-column result sheet is supported:
- **Row 1–2:** Institution name & exam title
- **Row 4:** Subject names (e.g. `Mobile Computing [COPE301]`)
- **Row 5:** Field names (`Internal Marks`, `External Marks`, `Result`, …)
- **Row 6+:** Student data (`#`, Name, Enroll/GR No, marks, GPA, CGPA)

Enrollment numbers like `A21DCE-04` are parsed automatically (department + section).

### Simple CSV (alternative)
Columns: `Student ID`, `Student Name`, `Department`, `Semester`, `Section`, `Subject`, `Marks`, `Pass Marks`

Use `sample_results.csv` to test the simple format.
