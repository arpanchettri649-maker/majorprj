"""Parser for polytechnic college result Excel files (multi-row header format).

Works for ANY semester — different student counts, subject counts, and column widths.
The layout rules are the same; only the number of subjects/columns changes.
"""

import re

import pandas as pd

from academic import (
    COMPUTER_DEPT_CODE,
    academic_batch_from_enrollment,
    is_computer_department,
    normalize_academic_batch,
)

DEFAULT_PASS_MARKS = 40.0

ENROLLMENT_DEPT_MAP = {
    "DCE": "CO",
    "COE": "CO",
    "CO": "CO",
    "ME": "ME",
    "MEC": "ME",
    "MT": "MT",
    "MCT": "MT",
    "CE": "CE",
    "CIV": "CE",
    "MC": "MC",
    "MLT": "MC",
}

SUMMARY_MARKERS = {"gpa", "cgpa", "sgpa", "total", "remark", "remarks"}


def _norm(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _to_float(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in {"AB", "ABS", "ABSENT", "-", "NA", "N/A"}:
        return None
    try:
        number = float(text)
        return number if pd.notna(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _is_year_back_column(value):
    key = re.sub(r"[^a-z0-9]", "", _norm(value))
    return key in {"yearback", "yearbackstatus", "isyearback", "yearbackflag", "yearbackyn", "yearbackyesno"}


def normalize_year_back_status(value):
    """Only explicit affirmative values identify a year-back student."""
    text = re.sub(r"[\s_-]+", "", _norm(value))
    if text in {"yes", "y", "true", "1", "1.0", "yearback"}:
        return "YES"
    if text in {"no", "n", "false", "0", "0.0"}:
        return "NO"
    return "VERIFY" if text else None


def exclude_year_back_students(records, excluded_ids=()):
    """No-op: year-back exclusion removed as per requirements."""
    return records, []


def _read_tabular(path, ext, **kwargs):
    """Read supported upload types, with a useful error when an optional engine is absent."""
    ext = (ext or str(path).rsplit('.', 1)[-1]).lower().lstrip('.')
    if ext == "csv":
        return pd.read_csv(path, **kwargs)
    if ext not in {"xlsx", "xls", "xlsm"}:
        raise ValueError(f"Unsupported file type: .{ext}")
    try:
        return pd.read_excel(path, engine="openpyxl" if ext in {"xlsx", "xlsm"} else None, **kwargs)
    except ImportError as exc:
        raise ValueError(f"Cannot read .{ext} files because the required Excel dependency is not installed.") from exc


def _normalize_result(value):
    """Use only the Excel Result column. Never infer from marks."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    if not text:
        return None
    if text in {"PASS", "P", "PASSED"} or text.startswith("PASS"):
        return "PASS"
    if text in {"FAIL", "F", "FAILED", "AB", "ABS", "ABSENT"} or text.startswith("FAIL"):
        return "FAIL"
    return None


def _extract_subject_code(subject_cell):
    text = str(subject_cell).strip() if subject_cell is not None and not pd.isna(subject_cell) else ""
    match = re.search(r"\[([A-Z0-9]+)\]", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return text[:12].upper().replace(" ", "") or "SUBJECT"


def _parse_enrollment(enrollment):
    text = str(enrollment).strip().upper()
    section = "A"
    dept_code = None

    # First find dept code from anywhere in text
    for token in sorted(ENROLLMENT_DEPT_MAP, key=len, reverse=True):
        if token in text:
            dept_code = ENROLLMENT_DEPT_MAP[token]
            break

    # Determine section
    parts = [p.strip() for p in text.split("-") if p.strip()]
    if len(parts) >= 3:
        # e.g., 21DCE-A-04 or CO-B-12
        for part in parts[1:-1]:
            if len(part) == 1 and part.isalpha():
                section = part
                break
    elif len(parts) == 2:
        # e.g., A21DCE-04 or DCE-04 or DCE-B
        left, right = parts[0], parts[1]
        if len(right) == 1 and right.isalpha():
            section = right
        else:
            # Check if left prefix has section letter before admission year, e.g. A21DCE, B22CO
            prefix_match = re.match(r"^([A-Z])\d{2}", left)
            if prefix_match:
                section = prefix_match.group(1)
            else:
                section = "A"
    else:
        # e.g., A21DCE04 or DCE2024001
        prefix_match = re.match(r"^([A-Z])\d{2}", text)
        if prefix_match:
            section = prefix_match.group(1)
        else:
            section = "A"

    return dept_code, section


def _parse_semester(exam_title, default=0):
    if not exam_title or pd.isna(exam_title):
        return default
    match = re.search(r"sem(?:ester)?[\s.-]*(\d+)", str(exam_title), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return default


def _looks_like_enrollment(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    text = str(value).strip()
    if len(text) < 4:
        return False
    if _norm(text) in {"enroll/gr no", "enroll no", "gr no", "enrollment"}:
        return False
    return bool(re.search(r"[A-Z0-9]", text, re.IGNORECASE))


def _looks_like_name(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    text = str(value).strip()
    if not text or _norm(text) in {"name", "student name", "#", "s.no", "sno"}:
        return False
    return any(ch.isalpha() for ch in text)


class SheetLayout:
    def __init__(
        self,
        subject_header_row,
        field_header_row,
        data_start_row,
        name_col,
        enroll_col,
        subject_start_col,
        subject_end_col,
    ):
        self.subject_header_row = subject_header_row
        self.field_header_row = field_header_row
        self.data_start_row = data_start_row
        self.name_col = name_col
        self.enroll_col = enroll_col
        self.subject_start_col = subject_start_col
        self.subject_end_col = subject_end_col


def _detect_layout(df):
    """
    Auto-detect sheet structure for any semester.
    Finds the row with 'Result' sub-columns, subject row above it,
    and first student row by enrollment pattern.
    """
    best_field_row = None
    best_result_count = 0

    scan_rows = min(15, len(df))
    for row_idx in range(scan_rows):
        row = df.iloc[row_idx]
        result_count = sum(
            1 for col in range(len(row))
            if _norm(row.iloc[col]) == "result"
        )
        if result_count > best_result_count:
            best_result_count = result_count
            best_field_row = row_idx

    if best_field_row is None or best_result_count < 2:
        return None

    field_header_row = best_field_row
    subject_header_row = field_header_row - 1 if field_header_row > 0 else 0

    name_col, enroll_col = 1, 2
    for row_idx in range(max(0, field_header_row - 3), field_header_row + 1):
        row = df.iloc[row_idx]
        for col in range(min(8, len(row))):
            val = _norm(row.iloc[col])
            if val in {"name", "student name", "name of student"}:
                name_col = col
            if "enroll" in val or val in {"gr no", "gr. no", "grno", "gr number"}:
                enroll_col = col

    subject_start_col = max(name_col, enroll_col) + 1

    subject_end_col = len(df.columns)
    for row_idx in range(max(0, field_header_row - 3), field_header_row + 1):
        row = df.iloc[row_idx]
        for col in range(subject_start_col, len(row)):
            if _norm(row.iloc[col]) in SUMMARY_MARKERS:
                subject_end_col = min(subject_end_col, col)

    if subject_end_col >= len(df.columns):
        field_row = df.iloc[field_header_row]
        result_cols = [
            col for col in range(subject_start_col, len(field_row))
            if _norm(field_row.iloc[col]) == "result"
        ]
        if result_cols:
            subject_end_col = result_cols[-1] + 1

    data_start_row = None
    for row_idx in range(field_header_row + 1, len(df)):
        enroll_val = df.iloc[row_idx, enroll_col] if enroll_col < len(df.columns) else None
        name_val = df.iloc[row_idx, name_col] if name_col < len(df.columns) else None
        if _looks_like_enrollment(enroll_val) and _looks_like_name(name_val):
            data_start_row = row_idx
            break

    if data_start_row is None:
        data_start_row = field_header_row + 1

    return SheetLayout(
        subject_header_row=subject_header_row,
        field_header_row=field_header_row,
        data_start_row=data_start_row,
        name_col=name_col,
        enroll_col=enroll_col,
        subject_start_col=subject_start_col,
        subject_end_col=subject_end_col,
    )


def _forward_fill_subjects(subject_row, start_col, end_col):
    names = []
    current = ""
    for col in range(start_col, end_col):
        cell = subject_row.iloc[col] if col < len(subject_row) else None
        if cell is not None and not pd.isna(cell) and str(cell).strip():
            current = str(cell).strip()
        names.append(current)
    return names


def _build_subject_blocks(subject_row, field_row, start_col, end_col):
    subject_names = _forward_fill_subjects(subject_row, start_col, end_col)
    blocks = []
    col = start_col

    while col < end_col:
        rel = col - start_col
        if rel >= len(subject_names) or not subject_names[rel]:
            col += 1
            continue

        subject_name = subject_names[rel]
        start = col
        end = col
        found = False

        while end < end_col:
            rel_end = end - start_col
            if rel_end >= len(subject_names):
                break
            if end > start and subject_names[rel_end] != subject_name:
                break
            if _norm(field_row.iloc[end]) == "result":
                blocks.append({
                    "name": subject_name,
                    "code": _extract_subject_code(subject_name),
                    "start_col": start,
                    "end_col": end,
                    "columns": _map_block_columns(field_row, start, end),
                })
                col = end + 1
                found = True
                break
            end += 1

        if not found:
            col += 1

    return blocks


def _map_block_columns(field_row, start_col, end_col):
    mapping = {}
    for col in range(start_col, end_col + 1):
        field = _norm(field_row.iloc[col])
        if "internal" in field and "mark" in field:
            mapping["internal"] = col
        elif "external" in field and "mark" in field:
            mapping["external"] = col
        elif "practical" in field and "mark" in field and "internal" not in field:
            mapping.setdefault("marks", col)
        elif "professional" in field or ("final" in field and "exam" in field):
            mapping["marks"] = col
        elif field in {"marks", "mark"} or (
            "mark" in field and "credit" not in field and "point" not in field
        ):
            mapping.setdefault("marks", col)
        elif field == "result":
            mapping["result"] = col
    mapping.setdefault("result", end_col)
    return mapping


def _extract_marks(row, columns):
    internal = _to_float(row.iloc[columns["internal"]]) if "internal" in columns else None
    external = _to_float(row.iloc[columns["external"]]) if "external" in columns else None
    single = _to_float(row.iloc[columns["marks"]]) if "marks" in columns else None

    if internal is not None and external is not None:
        return internal + external
    if internal is not None:
        return internal
    if external is not None:
        return external
    if single is not None:
        return single
    return 0.0


def is_college_format(df):
    if df.shape[0] < 5 or df.shape[1] < 10:
        return False
    return _detect_layout(df) is not None


def is_simple_format(df):
    aliases = {
        "student_id": ("student id", "student_id", "enrollment", "enroll no", "roll no", "rollno", "gr no"),
        "student_name": ("student name", "student_name", "name", "full name"),
        "department": ("department", "dept", "branch", "program"),
        "semester": ("semester", "sem"), "section": ("section", "div", "division"),
        "subject": ("subject", "subject name"), "marks": ("marks", "mark", "score"),
        "pass_marks": ("pass marks", "passing marks", "pass mark"),
        "result": ("result", "status", "pass/fail", "pass fail"),
    }
    keys = {_norm(c) for c in df.columns}
    # Semester is optional for uploads. It is inferred from a college exam
    # title when available, otherwise stored as 0 (not supplied).
    required = {name: names for name, names in aliases.items() if name != "semester"}
    return all(any(alias in keys for alias in names) for names in required.values())


def _column_aliases(df):
    aliases = {
        "student_id": ("student id", "student_id", "enrollment", "enroll no", "roll no", "rollno", "gr no"),
        "student_name": ("student name", "student_name", "name", "full name"),
        "department": ("department", "dept", "branch", "program"),
        "semester": ("semester", "sem"), "section": ("section", "div", "division"),
        "subject": ("subject", "subject name"), "marks": ("marks", "mark", "score"),
        "pass_marks": ("pass marks", "passing marks", "pass mark"), "result": ("result", "status", "pass/fail", "pass fail"),
        "academic_batch": ("academic batch", "batch"),
    }
    normalized = {_norm(c): c for c in df.columns}
    columns = {name: next((normalized[a] for a in names if a in normalized), None) for name, names in aliases.items()}
    return columns


def parse_college_excel(path, default_semester=None, department_override=None, academic_batch_override=None):
    df = _read_tabular(path, str(path).rsplit('.', 1)[-1], header=None)
    layout = _detect_layout(df)
    if not layout:
        raise ValueError("File does not match the expected college result format.")

    institution = str(df.iloc[0, 0]).strip() if not pd.isna(df.iloc[0, 0]) else ""
    exam_title = ""
    for row_idx in range(1, min(4, len(df))):
        cell = df.iloc[row_idx, 0]
        if cell is not None and not pd.isna(cell) and str(cell).strip():
            exam_title = str(cell).strip()
            break

    semester = _parse_semester(exam_title, default_semester or 0)
    year_back_cols = set()
    semester_col = None
    for row_idx in range(layout.field_header_row + 1):
        for col, value in enumerate(df.iloc[row_idx]):
            if _is_year_back_column(value):
                year_back_cols.add(col)
            if _norm(value) in {"semester", "sem", "current semester", "current sem"}:
                semester_col = col
            inferred = _parse_semester(value, None)
            if inferred is not None:
                semester = inferred
    excluded_ids = {
        str(row.iloc[layout.enroll_col]).strip().upper()
        for _, row in df.iloc[layout.data_start_row:].iterrows()
        if any(normalize_year_back_status(row.iloc[col]) == "YES" for col in year_back_cols)
    }
    subject_row = df.iloc[layout.subject_header_row]
    field_row = df.iloc[layout.field_header_row]
    blocks = _build_subject_blocks(
        subject_row,
        field_row,
        layout.subject_start_col,
        layout.subject_end_col,
    )

    if not blocks:
        raise ValueError("Could not detect subject columns from the header rows.")

    records = []
    skipped = []

    for row_idx in range(layout.data_start_row, len(df)):
        row = df.iloc[row_idx]
        enroll = row.iloc[layout.enroll_col] if layout.enroll_col < len(row) else None
        name = row.iloc[layout.name_col] if layout.name_col < len(row) else None

        if not _looks_like_enrollment(enroll):
            continue
        if not _looks_like_name(name):
            skipped.append(f"Row {row_idx + 1}: missing student name")
            continue

        student_id = str(enroll).strip()
        student_name = str(name).strip()
        dept_code, section = _parse_enrollment(student_id)

        if department_override:
            dept_code = department_override
        if not dept_code:
            skipped.append(f"Row {row_idx + 1}: could not detect department from {student_id}")
            continue
        if not is_computer_department(dept_code):
            skipped.append(f"Row {row_idx + 1}: skipped non-Computer Engineering ({student_id})")
            continue
        dept_code = COMPUTER_DEPT_CODE

        academic_batch = normalize_academic_batch(academic_batch_override) or academic_batch_from_enrollment(
            student_id
        )
        if not academic_batch:
            skipped.append(f"Row {row_idx + 1}: could not detect academic batch for {student_id}")
            continue

        for block in blocks:
            result_col = block["columns"]["result"]
            if result_col >= len(row):
                continue
            result = _normalize_result(row.iloc[result_col])
            if result is None:
                skipped.append(
                    f"Row {row_idx + 1}: missing Pass/Fail in Result column for {block['code']}"
                )
                continue

            marks = _extract_marks(row, block["columns"])
            records.append({
                "student_id": student_id,
                "student_name": student_name,
                "department": dept_code,
                "semester": int(_to_float(row.iloc[semester_col]) or semester) if semester_col is not None else semester,
                "section": section,
                "academic_batch": academic_batch,
                "subject": block["name"],
                "subject_code": block["code"],
                "marks": marks,
                "pass_marks": DEFAULT_PASS_MARKS,
                "result": result,
            })

    records, year_back_warnings = exclude_year_back_students(records, excluded_ids)
    skipped = year_back_warnings + skipped
    failures = [r for r in records if r["result"] == "FAIL"]
    return {
        "format": "college_excel",
        "institution": institution,
        "exam_title": exam_title,
        "semester": semester,
        "subjects_found": len(blocks),
        "students_found": len({r["student_id"] for r in records}),
        "records": records,
        "failures": failures,
        "skipped": skipped,
        "layout": {
            "subject_row": layout.subject_header_row + 1,
            "field_row": layout.field_header_row + 1,
            "data_start_row": layout.data_start_row + 1,
            "total_columns": len(df.columns),
        },
    }


def parse_simple_file(path, ext, academic_batch_override=None, default_semester=None):
    df = _read_tabular(path, ext)
    df.columns = [str(c).strip() for c in df.columns]
    cols = _column_aliases(df)
    if not is_simple_format(df):
        raise ValueError(
            "Missing required columns: Student ID, Student Name, Department, "
            "Section, Subject, Marks, Pass Marks, Result"
        )
    if not cols["result"]:
        raise ValueError(
            "The Result column (PASS/FAIL) is required. Failures are taken from "
            "Pass/Fail in the sheet, not from marks."
        )

    records = []
    skipped = []
    year_back_cols = [col for col in df.columns if _is_year_back_column(col)]
    excluded_ids = {
        str(row[cols["student_id"]]).strip().upper()
        for _, row in df.iterrows()
        if any(normalize_year_back_status(row[col]) == "YES" for col in year_back_cols)
    }
    for idx, row in df.iterrows():
        result = _normalize_result(row.get(cols["result"]))
        if result is None:
            skipped.append(f"Row {idx + 2}: Result must be PASS or FAIL")
            continue

        dept_raw = str(row[cols["department"]]).strip()
        if not is_computer_department(dept_raw):
            skipped.append(f"Row {idx + 2}: skipped non-Computer Engineering ({row[cols['student_id']]})")
            continue

        student_id = str(row[cols["student_id"]]).strip()
        academic_batch = (
            normalize_academic_batch(academic_batch_override)
            or normalize_academic_batch(row.get(cols["academic_batch"]))
            or academic_batch_from_enrollment(student_id)
        )
        if not academic_batch:
            skipped.append(f"Row {idx + 2}: could not detect academic batch for {student_id}")
            continue

        semester_value = _to_float(row[cols["semester"]]) if cols["semester"] else None
        semester = int(semester_value) if semester_value is not None else (default_semester if default_semester is not None else 0)
        if semester is None:
            skipped.append(f"Row {idx + 2}: missing semester")
            continue

        marks_value = _to_float(row[cols["marks"]])
        pass_marks_value = _to_float(row[cols["pass_marks"]])
        marks = marks_value if marks_value is not None else 0.0
        pass_marks = pass_marks_value if pass_marks_value is not None else DEFAULT_PASS_MARKS
        year_back_status = None
        if cols.get("year_back_status"):
            year_back_status = normalize_year_back_status(row.get(cols["year_back_status"]))
        records.append({
            "student_id": student_id,
            "student_name": str(row[cols["student_name"]]).strip(),
            "department": COMPUTER_DEPT_CODE,
            "semester": int(semester),
            "section": str(row[cols["section"]]).strip(),
            "academic_batch": academic_batch,
            "subject": str(row[cols["subject"]]).strip(),
            "subject_code": str(row[cols["subject"]]).strip()[:12].upper().replace(" ", ""),
            "marks": marks,
            "pass_marks": pass_marks,
            "result": result,
            "year_back_status": year_back_status,
        })

    records, year_back_warnings = exclude_year_back_students(records, excluded_ids)
    skipped = year_back_warnings + skipped
    return {
        "format": "simple",
        "records": records,
        "failures": [r for r in records if r["result"] == "FAIL"],
        "skipped": skipped,
        "institution": "",
        "exam_title": "",
        "semester": None,
        "subjects_found": len({r["subject"] for r in records}),
        "students_found": len({r["student_id"] for r in records}),
    }


def parse_result_file(
    path,
    ext,
    default_semester=None,
    department_override=None,
    academic_batch_override=None,
):
    if ext == "csv":
        return parse_simple_file(
            path, ext,
            academic_batch_override=academic_batch_override,
            default_semester=default_semester,
        )

    df = _read_tabular(path, ext, header=None)
    if is_college_format(df):
        return parse_college_excel(
            path, default_semester, department_override, academic_batch_override
        )

    return parse_simple_file(
        path, ext,
        academic_batch_override=academic_batch_override,
        default_semester=default_semester,
    )


def _match_header(name, *needles):
    text = _norm(name)
    return any(n in text for n in needles)


def parse_backlog_file(path, ext, default_semester=None, academic_batch_override=None):
    """Parse a dedicated backlog sheet. Missing Result is treated as FAIL."""
    if ext in {"xlsx", "xls", "xlsm"}:
        raw = _read_tabular(path, ext, header=None)
        if is_college_format(raw):
            parsed = parse_college_excel(
                path, default_semester, COMPUTER_DEPT_CODE, academic_batch_override
            )
            fail_records = [r for r in parsed["records"] if r["result"] == "FAIL"]
            parsed["records"] = fail_records
            parsed["failures"] = fail_records
            parsed["format"] = "backlog_college"
            parsed["students_found"] = len({r["student_id"] for r in fail_records})
            parsed["subjects_found"] = len({r["subject"] for r in fail_records})
            return parsed

    df = _read_tabular(path, ext)
    df.columns = [str(c).strip() for c in df.columns]

    col_id = col_name = col_subject = col_code = None
    col_sem = col_section = col_result = col_marks = None
    for col in df.columns:
        if col_id is None and _match_header(col, "enroll", "roll", "student id", "student_id", "gr no", "grno"):
            col_id = col
        elif col_name is None and _match_header(col, "name") and not _match_header(col, "subject"):
            col_name = col
        elif col_subject is None and _match_header(col, "subject") and not _match_header(col, "code"):
            col_subject = col
        elif col_code is None and _match_header(col, "subject code", "code"):
            col_code = col
        elif col_sem is None and _match_header(col, "sem"):
            col_sem = col
        elif col_section is None and _match_header(col, "section", "div"):
            col_section = col
        elif col_result is None and _match_header(col, "result", "pass", "fail", "status"):
            col_result = col
        elif col_marks is None and _match_header(col, "mark"):
            col_marks = col

    if col_id is None or col_subject is None:
        raise ValueError(
            "Backlog sheet must include a Student ID/Roll column and a Subject column."
        )

    records = []
    skipped = []
    for idx, row in df.iterrows():
        sid = str(row[col_id]).strip() if pd.notna(row[col_id]) else ""
        if not sid or sid.lower() in {"nan", "none"}:
            continue
        subject = str(row[col_subject]).strip() if pd.notna(row[col_subject]) else ""
        if not subject or subject.lower() in {"nan", "none"}:
            skipped.append(f"Row {idx + 2}: missing subject for {sid}")
            continue

        result = _normalize_result(row[col_result]) if col_result else "FAIL"
        if result is None:
            result = "FAIL"
        if result != "FAIL":
            continue

        semester = default_semester
        if col_sem and pd.notna(row[col_sem]):
            try:
                semester = int(float(str(row[col_sem]).strip()))
            except ValueError:
                semester = default_semester
        if semester is None:
            skipped.append(f"Row {idx + 2}: missing semester for {sid}")
            continue

        name = str(row[col_name]).strip() if col_name and pd.notna(row[col_name]) else sid
        section = str(row[col_section]).strip() if col_section and pd.notna(row[col_section]) else "A"
        if not section or section.lower() in {"nan", "none"}:
            section = "A"
        code = ""
        if col_code and pd.notna(row[col_code]):
            code = str(row[col_code]).strip()
        marks = _to_float(row[col_marks]) if col_marks else 0.0
        academic_batch = (
            normalize_academic_batch(academic_batch_override)
            or academic_batch_from_enrollment(sid)
        )
        records.append({
            "student_id": sid,
            "student_name": name,
            "department": COMPUTER_DEPT_CODE,
            "semester": int(semester),
            "section": section[:5],
            "academic_batch": academic_batch,
            "subject": subject,
            "subject_code": code or subject[:12].upper().replace(" ", ""),
            "marks": marks if marks is not None else 0.0,
            "pass_marks": DEFAULT_PASS_MARKS,
            "result": "FAIL",
        })

    return {
        "format": "backlog_sheet",
        "records": records,
        "failures": records,
        "skipped": skipped,
        "institution": "",
        "exam_title": "Backlog Sheet",
        "semester": default_semester,
        "subjects_found": len({r["subject"] for r in records}),
        "students_found": len({r["student_id"] for r in records}),
    }


_ATT_ID_KEYS = (
    "student id", "studentid", "enrollment", "enroll no", "enroll/gr", "enroll",
    "roll no", "rollno", "roll", "gr no", "grno",
)
_ATT_SKIP_WIDE = {
    "student", "student id", "studentid", "student name", "full name", "name",
    "enrollment", "enroll no", "enroll", "roll no", "rollno", "roll",
    "gr no", "grno", "id",
    "sr", "sr no", "s.no", "s no", "sno", "no", "#",
    "semester", "sem", "batch", "academic batch", "section",
    "remarks", "remark", "status", "result",
    "total", "total classes", "classes", "held",
    "attended", "classes attended", "present",
    "attendance", "attendance %", "attendance percent", "percent", "%",
    "overall", "overall %", "overall attendance",
}


def _cell_text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _parse_attendance_number(value):
    text = _cell_text(value).replace("%", "").replace(",", "").strip()
    if not text or text.lower() in {"nan", "none", "-", "na", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_attendance_cell(value):
    """Return (percent, total, attended) from 85, 85%, or 18/20 91.67%."""
    text = _cell_text(value)
    if not text or text.lower() in {"nan", "none", "-", "na", "n/a", ""}:
        return None, 0, 0

    # Handle format: "18/20 90%" or "18/20"
    if "/" in text:
        parts = text.split("/")
        if len(parts) >= 2:
            left = parts[0].strip()
            right = parts[1].split()[0].strip()  # Take only the first part after / (ignore percentage if present)
            attended = _parse_attendance_number(left)
            total = _parse_attendance_number(right)
            if attended is None or total is None or total <= 0:
                return None, 0, 0
            pct = round((attended / total) * 100, 1)
            return pct, int(total), int(attended)

    # Handle format: "85%" or "85"
    num = _parse_attendance_number(text)
    if num is None:
        return None, 0, 0
    if 0 < num <= 1:
        num = num * 100
    return round(float(num), 1), 0, 0


def _attendance_id_col(keys):
    for i, key in enumerate(keys):
        if "subject" in key:
            continue
        if any(token == key or token in key for token in _ATT_ID_KEYS):
            return i
        if key in {"id", "student"}:
            return i
    return None


def _attendance_col(keys, *needles, exclude=()):
    for i, key in enumerate(keys):
        if any(ex in key for ex in exclude):
            continue
        for needle in needles:
            if needle == key or needle in key:
                return i
    return None


def parse_attendance_file(path, ext):
    """Parse CSV/XLSX semester attendance into per-student, per-subject records.
    Handles complex Excel formats with merged headers, multiple header rows, and dynamic subjects."""
    if ext == "csv":
        # For CSV, read with header first, then check if we need to detect multi-row headers
        df = _read_tabular(path, ext, dtype=str, keep_default_na=False)
        df = df.fillna("")
        # Assume first row is header for CSV
        header_row_idx = 0
        df_data = df.copy()
    else:
        # For Excel, read without header to detect structure
        df = _read_tabular(path, ext, header=None, dtype=str)
        df = df.fillna("")

        # Detect header row - look for row with student ID/roll no indicators
        header_row_idx = None
        student_id_col = None
        student_name_col = None

        for row_idx in range(min(20, len(df))):
            row = df.iloc[row_idx]
            row_text = [_norm(str(v)) for v in row]

            # Look for row containing student identification headers
            if any(marker in " ".join(row_text) for marker in ["student id", "roll no", "enroll", "gr no", "student name"]):
                header_row_idx = row_idx
                # Find column positions
                for col_idx, val in enumerate(row_text):
                    if not student_id_col and any(marker in val for marker in ["student id", "roll no", "enroll", "gr no"]):
                        student_id_col = col_idx
                    elif not student_name_col and "name" in val and "student" in val:
                        student_name_col = col_idx
                break

        if header_row_idx is None:
            # Fallback: try to find first row with student-like data
            for row_idx in range(min(10, len(df))):
                row = df.iloc[row_idx]
                if _looks_like_enrollment(row.iloc[0]) or _looks_like_name(row.iloc[1]):
                    header_row_idx = row_idx - 1 if row_idx > 0 else 0
                    student_id_col = 0
                    student_name_col = 1
                    break

        if header_row_idx is None:
            return {"records": [], "skipped": ["Could not identify header row in attendance file."], "format": "unknown"}

        # Use header row as column names
        df_data = df.iloc[header_row_idx + 1:].copy()
        df_data.columns = [str(df.iloc[header_row_idx, i]).strip() for i in range(len(df.columns))]

    # Normalize column names
    columns = [_cell_text(c) or f"col_{i}" for i, c in enumerate(df_data.columns)]
    keys = [_norm(c) for c in columns]

    # Find student ID and name columns
    id_col = _attendance_id_col(keys)
    if id_col is None and student_id_col is not None:
        id_col = student_id_col

    name_col = _attendance_col(keys, "student name", "full name", "name", exclude=("id", "code", "subject"))
    if name_col is None and student_name_col is not None:
        name_col = student_name_col

    # Identify subject columns (columns with attendance data like "14/20 82.61%")
    subject_columns = []
    summary_keywords = {"total", "overall", "theory", "practical", "grand", "aggregate", "percent", "%", "summary"}

    for col_idx, key in enumerate(keys):
        # Skip student info columns
        if col_idx == id_col or col_idx == name_col:
            continue
        if any(token in key for token in ("student", "enroll", "roll", "name", "group", "section", "semester", "batch", "sr", "s.no", "sno")):
            continue
        # Skip summary columns
        if any(keyword in key for keyword in summary_keywords):
            continue
        # Check if this column has attendance-like data in the first few data rows
        has_attendance_data = False
        for row_idx in range(min(10, len(df_data))):
            cell_val = _cell_text(df_data.iloc[row_idx, col_idx])
            if "/" in cell_val and re.search(r"\d+/\d+", cell_val):
                has_attendance_data = True
                break
            if re.search(r"\d+%", cell_val):
                has_attendance_data = True
                break
        if has_attendance_data:
            subject_columns.append(col_idx)

    if id_col is None:
        id_col = 0

    records = []
    skipped = []

    for row_idx, row in df_data.iterrows():
        values = [_cell_text(row[df_data.columns[i]]) for i in range(len(columns))]
        sid = values[id_col] if id_col < len(values) else ""

        # Normalize student ID: remove spaces, tabs, special characters
        if sid:
            sid = sid.strip().replace(" ", "").replace("\t", "")

        # Skip header-like rows
        if not sid or sid.lower() in {"nan", "none", "studentid", "roll", "enrollment", "id", "rollno", "studentid"}:
            continue
        if not re.search(r"[A-Za-z0-9]", sid):
            continue

        name = values[name_col] if name_col is not None and name_col < len(values) else ""

        # Extract attendance for each subject column
        added = 0
        for col_idx in subject_columns:
            subject_name = columns[col_idx]
            if not subject_name or subject_name.lower() in {"nan", "none", ""}:
                continue

            cell_value = values[col_idx] if col_idx < len(values) else ""
            pct, total_c, attended_c = _parse_attendance_cell(cell_value)

            if pct is None:
                continue

            # Use exact subject name and code from the Excel file
            # No automatic modifications to preserve exact matching
            header_code = subject_name[:12].upper().replace(" ", "")

            records.append({
                "student_id": sid,
                "student_name": name,
                "subject": subject_name,
                "subject_code": header_code,
                "total_classes": total_c,
                "classes_attended": attended_c,
                "attendance_percent": pct,
            })
            added += 1

        if added == 0:
            skipped.append(f"Row {int(row_idx) + header_row_idx + 2}: {sid} has no valid attendance data")

    return {
        "records": records,
        "skipped": skipped,
        "format": "dynamic_wide",
        "students_found": len({r["student_id"] for r in records}),
        "subjects_found": len({r["subject"] for r in records}),
        "header_row": header_row_idx + 1,
    }


def parse_attc_backlog_file(path, ext, academic_batch_override=None):
    """Parse ATTC Backlog Analysis Excel with 22 columns including CGPA and semester-wise backlogs."""
    if ext not in {"xlsx", "xls", "xlsm"}:
        raise ValueError("ATTC Backlog Analysis format requires an Excel file.")
    try:
        df = _read_tabular(path, ext, sheet_name="Backlog Analysis", header=None)
    except ValueError:
        df = _read_tabular(path, ext, header=None)
    if df.shape[1] < 8:
        raise ValueError("ATTC Backlog Analysis does not contain enough columns.")
    
    # Find header row (contains "Student ID" in column 0)
    header_row_idx = None
    for idx in range(min(10, len(df))):
        cell_val = str(df.iloc[idx, 0]).strip() if not pd.isna(df.iloc[idx, 0]) else ""
        if "student id" in cell_val.lower():
            header_row_idx = idx
            break
    
    if header_row_idx is None:
        raise ValueError("Could not find header row with 'Student ID' in the Backlog Analysis sheet.")
    
    # Read data starting from row after header
    df_data = df.iloc[header_row_idx + 1:].copy()
    df_data.columns = [str(df.iloc[header_row_idx, i]).strip() for i in range(len(df.columns))]
    
    # Map by normalized header labels first; retain positional compatibility with the old template.
    headers = [_norm(v) for v in df.iloc[header_row_idx].tolist()]
    if not any("backlog" in value and ("sem" in value or re.search(r"\d", value)) for value in headers):
        raise ValueError("Could not find semester-wise backlog columns in the ATTC sheet.")
    def header_index(*needles, default=None):
        for i, value in enumerate(headers):
            if any(n in value for n in needles):
                return i
        return default
    COL_STUDENT_ID = header_index("student id", default=0)
    COL_ENROLL_GR = header_index("enroll/gr", "enroll", "roll", default=1)
    COL_NAME = header_index("student name", "full name", default=2)
    COL_PROGRAM_STATUS = header_index("program status", default=3)
    year_back_cols = [i for i, value in enumerate(headers) if _is_year_back_column(value)]
    COL_CURRENT_SEMESTER = header_index("current semester", "current sem", default=5)
    COL_CGPA = header_index("cgpa", default=6)
    def semester_col(sem, kind, fallback):
        needles = (f"semester {sem} {kind}", f"sem {sem} {kind}", f"sem-{sem} {kind}", f"{kind} {sem}")
        return header_index(*needles, default=fallback)
    COL_SEM1_PASSED = semester_col(1, "passed", 7)
    COL_SEM1_BACKLOG = semester_col(1, "backlog", 8)
    COL_SEM1_FAILED = semester_col(1, "failed", 9)
    COL_SEM2_PASSED = semester_col(2, "passed", 10)
    COL_SEM2_BACKLOG = semester_col(2, "backlog", 11)
    COL_SEM2_FAILED = semester_col(2, "failed", 12)
    COL_SEM3_PASSED = semester_col(3, "passed", 13)
    COL_SEM3_BACKLOG = semester_col(3, "backlog", 14)
    COL_SEM3_FAILED = semester_col(3, "failed", 15)
    COL_SEM4_PASSED = semester_col(4, "passed", 16)
    COL_SEM4_BACKLOG = semester_col(4, "backlog", 17)
    COL_SEM4_FAILED = semester_col(4, "failed", 18)
    # Summary: cols 19-21
    COL_TOTAL_PASSED = 19
    COL_TOTAL_BACKLOG = 20
    COL_VERIFICATION_STATUS = 21
    
    records = []
    skipped = []
    student_records = []
    
    for idx, row in df_data.iterrows():
        enroll_gr = str(row.iloc[COL_ENROLL_GR]).strip() if COL_ENROLL_GR < len(row) else ""
        student_id = str(row.iloc[COL_STUDENT_ID]).strip() if COL_STUDENT_ID < len(row) else ""
        name = str(row.iloc[COL_NAME]).strip() if COL_NAME < len(row) else ""
        
        # Use Enroll/GR No as primary identifier, fallback to Student ID
        primary_id = enroll_gr if enroll_gr and enroll_gr.lower() not in {"nan", "none", ""} else student_id
        if not primary_id or primary_id.lower() in {"nan", "none", "", "student id", "enroll/gr no"}:
            continue
        
        # Parse CGPA
        cgpa_raw = row.iloc[COL_CGPA] if COL_CGPA < len(row) else None
        cgpa = _to_float(cgpa_raw)
        
        # Parse year back status
        year_back_values = [normalize_year_back_status(row.iloc[col]) for col in year_back_cols]
        year_back_status = "YES" if "YES" in year_back_values else next(iter(year_back_values), None)
        
        # Parse current semester
        current_sem_raw = row.iloc[COL_CURRENT_SEMESTER] if COL_CURRENT_SEMESTER < len(row) else None
        current_semester = 0
        try:
            current_semester = int(float(str(current_sem_raw).strip())) if current_sem_raw and not pd.isna(current_sem_raw) else 0
        except (ValueError, TypeError):
            pass
        
        # Parse semester-wise backlogs
        semester_data = [
            (1, COL_SEM1_PASSED, COL_SEM1_BACKLOG, COL_SEM1_FAILED),
            (2, COL_SEM2_PASSED, COL_SEM2_BACKLOG, COL_SEM2_FAILED),
            (3, COL_SEM3_PASSED, COL_SEM3_BACKLOG, COL_SEM3_FAILED),
            (4, COL_SEM4_PASSED, COL_SEM4_BACKLOG, COL_SEM4_FAILED),
        ]
        
        student_record = {
            "student_id": primary_id,
            "student_name": name,
            "cgpa": cgpa,
            "year_back_status": year_back_status,
            "current_semester": current_semester,
            "academic_batch": normalize_academic_batch(academic_batch_override) or academic_batch_from_enrollment(primary_id),
            "program_status": str(row.iloc[COL_PROGRAM_STATUS]).strip() if COL_PROGRAM_STATUS < len(row) else "",
            "backlog_verification_status": str(row.iloc[COL_VERIFICATION_STATUS]).strip() if COL_VERIFICATION_STATUS < len(row) else "",
            "semester_backlogs": []
        }
        
        for sem_num, col_passed, col_backlog, col_failed in semester_data:
            backlog_count = _to_float(row.iloc[col_backlog]) if col_backlog < len(row) else 0
            failed_subjects_raw = str(row.iloc[col_failed]).strip() if col_failed < len(row) else ""
            
            # Even if backlog_count is 0, check if failed subjects are listed
            # This handles cases where the backlog count might be incorrect
            has_failed_subjects = bool(failed_subjects_raw and failed_subjects_raw.lower() not in {"nan", "none", "", "none", "—", "-"})
            
            if backlog_count and backlog_count > 0 or has_failed_subjects:
                if not failed_subjects_raw or failed_subjects_raw.lower() in {"nan", "none", ""}:
                    skipped.append(f"Row {idx + header_row_idx + 2}: {primary_id} has {backlog_count} backlog(s) in Sem-{sem_num} but no failed subjects listed - marked for verification")
                    failed_subjects_raw = "NEEDS_VERIFICATION"
                
                # Parse failed subjects (comma, newline, or semicolon separated)
                subjects_list = []
                separators = [",", "\n", ";", "|"]
                for sep in separators:
                    if sep in failed_subjects_raw:
                        parts = failed_subjects_raw.split(sep)
                        for subj in parts:
                            subj = subj.strip()
                            if subj and subj.lower() not in {"nan", "none", "", "needs_verification", "—", "-"}:
                                subjects_list.append(subj)
                        break
                
                # If no separators found, treat the whole string as one subject
                if not subjects_list and failed_subjects_raw.lower() not in {"nan", "none", "", "needs_verification", "—", "-"}:
                    subjects_list = [failed_subjects_raw]
                
                # Use the actual count of parsed subjects if available
                actual_count = len(subjects_list)
                final_count = int(backlog_count) if backlog_count else actual_count
                
                student_record["semester_backlogs"].append({
                    "semester": sem_num,
                    "backlog_count": final_count,
                    "failed_subjects": subjects_list,
                    "needs_verification": failed_subjects_raw == "NEEDS_VERIFICATION" or (backlog_count and actual_count != backlog_count)
                })
        
        # Keep every valid student, including students with no backlog subjects.
        student_records.append(student_record)
    
    student_records, year_back_warnings = exclude_year_back_students(student_records)
    skipped = year_back_warnings + skipped
    return {
        "format": "attc_backlog_analysis",
        "records": student_records,
        "skipped": skipped,
        "students_found": len({r["student_id"].strip().upper() for r in student_records}),
        "total_backlog_subjects": sum(sum(len(s["failed_subjects"]) or s["backlog_count"] for s in r["semester_backlogs"]) for r in student_records),
    }
