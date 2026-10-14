"""Test parser with Sem 6 (79 cols) and Sem 4 (fewer cols/subjects/students) layouts."""

import pandas as pd
from pathlib import Path

from excel_parser import parse_college_excel


def build_sem6():
    cols = 79
    rows = [[None] * cols for _ in range(33)]
    rows[0][0] = "ABC Polytechnic"
    rows[1][0] = "Diploma Semester 6 Regular Examination 2025"
    for base, name in [(3, "Mobile Computing [COPE301]"), (10, "Network Forensics [COPE311]")]:
        rows[3][base] = name
        for i, f in enumerate(["Internal Marks", "External Marks", "Credit", "Grade Point", "Credit Point", "Grade", "Result"]):
            rows[4][base + i] = f
    rows[2][77], rows[2][78] = "GPA", "CGPA"
    rows[5][0], rows[5][1], rows[5][2] = 1, "Anush Kumar Rai", "A21DCE-04"
    rows[5][3], rows[5][4], rows[5][9] = 18, 10, "FAIL"
    rows[5][10], rows[5][11], rows[5][16] = 20, 22, "PASS"
    rows[6][0], rows[6][1], rows[6][2] = 2, "Sita Gurung", "A21DCE-05"
    rows[6][3], rows[6][4], rows[6][9] = 20, 22, "PASS"
    rows[6][10], rows[6][11], rows[6][16] = 15, 10, "FAIL"
    return pd.DataFrame(rows)


def build_sem4():
    """Smaller sheet: 5 subjects, 15 students, 45 columns."""
    cols = 45
    rows = [[None] * cols for _ in range(20)]
    rows[0][0] = "ABC Polytechnic"
    rows[1][0] = "Diploma Semester 4 Regular Examination 2025"
    subjects = [
        ("Engineering Mathematics [MA401]", 7),
        ("Data Structures [CO401]", 7),
        ("Digital Electronics [CO402]", 7),
        ("Communication Skills [HS401]", 7),
        ("Programming Lab [CO403]", 7),
    ]
    col = 3
    for name, width in subjects:
        rows[3][col] = name
        fields = ["Internal Marks", "External Marks", "Credit", "Grade Point", "Credit Point", "Grade", "Result"]
        for i, f in enumerate(fields):
            rows[4][col + i] = f
        col += width
    rows[2][38], rows[2][39] = "GPA", "CGPA"

    for i in range(15):
        r = 5 + i
        rows[r][0] = i + 1
        rows[r][1] = f"Student {i + 1}"
        rows[r][2] = f"A22DCE-{i + 1:02d}"
        rows[r][3], rows[r][4], rows[r][9] = 15, 12, "FAIL" if i == 0 else "PASS"

    return pd.DataFrame(rows)


if __name__ == "__main__":
    for label, builder in [("Sem 6", build_sem6), ("Sem 4", build_sem4)]:
        path = Path(f"test_{label.replace(' ', '_').lower()}.xlsx")
        builder().to_excel(path, index=False, header=False)
        result = parse_college_excel(path)
        print(f"{label}: {result['semester']=}, students={result['students_found']}, "
              f"subjects={result['subjects_found']}, failures={len(result['failures'])}")
        assert result["students_found"] >= 1
        assert result["subjects_found"] >= 1
    print("ALL TESTS PASSED")
