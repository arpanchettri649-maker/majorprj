"""Computer Engineering diploma batches and pass/fail helpers."""

import re

COMPUTER_DEPT_CODE = "CO"
COMPUTER_DEPT_ALIASES = {"CO", "DCE", "COE", "COMPUTER", "COMPUTER ENGINEERING"}

# 3-year diploma: admission year → pass-out year
ACADEMIC_BATCHES = ["2024-2027", "2025-2028", "2026-2029"]
SEMESTERS = [1, 2, 3, 4, 5, 6]
DIPLOMA_DURATION_YEARS = 3
LOW_ATTENDANCE_PERCENT = 75


def is_computer_department(value):
    if value is None:
        return False
    text = str(value).strip().upper()
    if text in COMPUTER_DEPT_ALIASES:
        return True
    return "COMPUTER" in text


def batch_label(start_year):
    if not start_year:
        return None
    return f"{int(start_year)}-{int(start_year) + DIPLOMA_DURATION_YEARS}"


def parse_admission_year(enrollment):
    """Extract admission year from enrollment / GR numbers."""
    if enrollment is None:
        return None
    text = str(enrollment).strip().upper()
    if not text:
        return None

    four = re.search(r"(20[2-3]\d)", text)
    if four:
        return int(four.group(1))

    two = re.search(r"(?:^|[^0-9])[A-Z]?([2][0-9])(?=[A-Z])", text)
    if two:
        return 2000 + int(two.group(1))

    return None


def academic_batch_from_enrollment(enrollment, fallback=None):
    year = parse_admission_year(enrollment)
    label = batch_label(year) if year else None
    return label or fallback


def normalize_academic_batch(value, fallback=None):
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if text in ACADEMIC_BATCHES:
        return text
    match = re.search(r"(20\d{2})\s*[-–/]\s*(20\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    year = parse_admission_year(text)
    if year:
        return batch_label(year)
    return fallback


def scope_upload_records(records, academic_batch, semester):
    """Force all records to one batch/semester; skip rows from other batches."""
    scoped = []
    skipped = []
    semester = int(semester)
    for idx, rec in enumerate(records, start=1):
        enrollment_batch = academic_batch_from_enrollment(rec.get("student_id"))
        if enrollment_batch and enrollment_batch != academic_batch:
            skipped.append(
                f"Record {idx}: {rec['student_id']} belongs to batch {enrollment_batch}, "
                f"not {academic_batch} — skipped to prevent mixed batches"
            )
            continue
        updated = dict(rec)
        updated["academic_batch"] = academic_batch
        updated["semester"] = semester
        scoped.append(updated)
    return scoped, skipped
