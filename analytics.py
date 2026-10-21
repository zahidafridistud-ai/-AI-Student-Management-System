"""All official calculations (percentages, averages, grades, attendance, risk flags).

These are computed in plain Python from database records. The AI model only
receives the finished numbers and explains them.
"""

import statistics


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def grade_for(percent, pass_percentage=40):
    if percent is None:
        return "N/A"
    if percent >= 90:
        return "A+"
    if percent >= 80:
        return "A"
    if percent >= 70:
        return "B"
    if percent >= 60:
        return "C"
    if percent >= pass_percentage:
        return "D"
    return "F"


def _corr(xs, ys):
    """Pearson correlation, or None when it cannot be computed."""
    if len(xs) < 3:
        return None
    try:
        return round(statistics.correlation(xs, ys), 2)
    except (statistics.StatisticsError, ValueError):
        return None


def build_snapshot(records: dict, settings: dict, semester=None) -> dict:
    student = records["student"]
    sem = semester or student["semester"]
    threshold = settings["attendance_threshold"]
    weak = settings["weak_threshold"]
    pass_pct = settings["pass_percentage"]

    subjects = []
    for sub in [s for s in records["subjects"] if s["semester"] == sem]:
        marks = [m for m in records["marks"] if m["subject_id"] == sub["id"] and m["semester"] == sem]
        obtained = sum(m["obtained"] for m in marks)
        maximum = sum(m["max_marks"] for m in marks)
        percent = pct(obtained, maximum)
        att = [a for a in records["attendance"] if a["subject_id"] == sub["id"]]
        present = sum(1 for a in att if a["present"])
        att_pct = pct(present, len(att))
        low_marks = percent is not None and percent < weak
        low_att = att_pct is not None and att_pct < threshold
        subjects.append({
            "code": sub["code"], "name": sub["name"],
            "obtained": round(obtained, 1), "maximum": round(maximum, 1),
            "percent": percent, "grade": grade_for(percent, pass_pct),
            "assessments": [{"name": m["assessment"], "obtained": m["obtained"],
                             "max": m["max_marks"], "percent": pct(m["obtained"], m["max_marks"])}
                            for m in marks],
            "classes_held": len(att), "classes_attended": present,
            "attendance_percent": att_pct,
            "failing": percent is not None and percent < pass_pct,
            "needs_attention": low_marks,
            "low_attendance": low_att,
            "low_marks_and_low_attendance": low_marks and low_att,
        })

    graded = [s for s in subjects if s["percent"] is not None]
    average = round(sum(s["percent"] for s in graded) / len(graded), 1) if graded else None
    held = sum(s["classes_held"] for s in subjects)
    attended = sum(s["classes_attended"] for s in subjects)
    overall_att = pct(attended, held)

    both = [s for s in subjects if s["percent"] is not None and s["attendance_percent"] is not None]
    correlation = _corr([s["attendance_percent"] for s in both], [s["percent"] for s in both])

    # semester history (all semesters the user is allowed to see)
    by_sem = {}
    for sub in records["subjects"]:
        ms = [m for m in records["marks"] if m["subject_id"] == sub["id"]]
        if ms:
            by_sem.setdefault(sub["semester"], []).append(
                pct(sum(m["obtained"] for m in ms), sum(m["max_marks"] for m in ms)))
    history = [{"semester": k, "average_percent": round(sum(v) / len(v), 1)}
               for k, v in sorted(by_sem.items())]

    risk = []
    if average is not None and average < weak:
        risk.append(f"Average {average}% is below {weak:g}%")
    failing = [s["name"] for s in subjects if s["failing"]]
    if failing:
        risk.append("Failing: " + ", ".join(failing))
    if overall_att is not None and overall_att < threshold:
        risk.append(f"Attendance {overall_att}% is below {threshold:g}%")

    if risk:
        category = "needs_support"
    elif average is not None and average >= 75 and not any(s["needs_attention"] for s in subjects):
        category = "performing_well"
    else:
        category = "on_track"

    ranked = sorted(graded, key=lambda s: s["percent"])
    return {
        "student": {"id": student["id"], "name": student["name"], "roll_no": student["roll_no"],
                    "program": student.get("program") or "", "semester": sem},
        "subjects": subjects,
        "average_percent": average,
        "overall_grade": grade_for(average, pass_pct),
        "classes_held": held, "classes_attended": attended,
        "overall_attendance_percent": overall_att,
        "attendance_below_threshold": overall_att is not None and overall_att < threshold,
        "strongest_subject": ranked[-1]["name"] if ranked else None,
        "weakest_subject": ranked[0]["name"] if ranked else None,
        "attendance_marks_correlation": correlation,
        "history": history,
        "risk_reasons": risk,
        "category": category,
    }


def ai_payload(snapshot: dict, settings: dict) -> dict:
    """Minimum data sent to the AI model: no name, roll number or contact details."""
    return {
        "semester": snapshot["student"]["semester"],
        "institution_rules": {
            "attendance_threshold_percent": settings["attendance_threshold"],
            "pass_percentage": settings["pass_percentage"],
            "needs_attention_below_percent": settings["weak_threshold"],
        },
        "overall": {
            "average_percent": snapshot["average_percent"],
            "grade": snapshot["overall_grade"],
            "attendance_percent": snapshot["overall_attendance_percent"],
            "attendance_below_threshold": snapshot["attendance_below_threshold"],
            "strongest_subject": snapshot["strongest_subject"],
            "weakest_subject": snapshot["weakest_subject"],
            "status": snapshot["category"],
            "risk_reasons": snapshot["risk_reasons"],
            "attendance_marks_correlation_across_subjects": snapshot["attendance_marks_correlation"],
        },
        "subjects": [{
            "name": s["name"], "percent": s["percent"], "grade": s["grade"],
            "assessments": [{"name": a["name"], "percent": a["percent"]} for a in s["assessments"]],
            "attendance_percent": s["attendance_percent"],
            "classes_held": s["classes_held"], "classes_attended": s["classes_attended"],
            "failing": s["failing"], "needs_attention": s["needs_attention"],
            "low_attendance": s["low_attendance"],
            "low_marks_and_low_attendance": s["low_marks_and_low_attendance"],
        } for s in snapshot["subjects"]],
        "semester_history": snapshot["history"],
    }


def class_stats(snapshots: list, settings: dict) -> dict:
    """Aggregate statistics for a group of students (all calculated in Python)."""
    subjects = {}
    for snap in snapshots:
        for s in snap["subjects"]:
            d = subjects.setdefault(s["name"], {"percents": [], "atts": [], "failing": 0, "weak": 0})
            if s["percent"] is not None:
                d["percents"].append(s["percent"])
                d["failing"] += s["failing"]
                d["weak"] += s["needs_attention"]
            if s["attendance_percent"] is not None:
                d["atts"].append(s["attendance_percent"])
    rows = []
    for name, d in subjects.items():
        n = len(d["percents"])
        rows.append({
            "subject": name, "students_graded": n,
            "average_percent": round(sum(d["percents"]) / n, 1) if n else None,
            "students_below_attention_level": d["weak"],
            "percent_below_attention_level": pct(d["weak"], n),
            "students_failing": d["failing"],
            "average_attendance_percent": round(sum(d["atts"]) / len(d["atts"]), 1) if d["atts"] else None,
        })
    rows.sort(key=lambda r: (r["average_percent"] is None, r["average_percent"]))
    avgs = [s["average_percent"] for s in snapshots if s["average_percent"] is not None]
    atts = [s["overall_attendance_percent"] for s in snapshots if s["overall_attendance_percent"] is not None]
    paired = [(s["overall_attendance_percent"], s["average_percent"]) for s in snapshots
              if s["average_percent"] is not None and s["overall_attendance_percent"] is not None]
    return {
        "student_count": len(snapshots),
        "class_average_percent": round(sum(avgs) / len(avgs), 1) if avgs else None,
        "class_average_attendance_percent": round(sum(atts) / len(atts), 1) if atts else None,
        "students_needing_support": sum(1 for s in snapshots if s["category"] == "needs_support"),
        "students_performing_well": sum(1 for s in snapshots if s["category"] == "performing_well"),
        "students_below_attendance_threshold": sum(1 for s in snapshots if s["attendance_below_threshold"]),
        "attendance_vs_average_correlation": _corr([p[0] for p in paired], [p[1] for p in paired]) if len(paired) >= 5 else None,
        "attendance_threshold_percent": settings["attendance_threshold"],
        "subjects": rows,
    }
