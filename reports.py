"""PDF report generation (ReportLab). Official figures come from the database;
AI text is included only if it was actually generated, and is clearly labelled."""

import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _p(text, style):
    return Paragraph(escape(str(text if text is not None else "")), style)


def build_pdf(snapshot: dict, settings: dict, ai_report: dict = None, teacher_feedback=None) -> bytes:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#1f3a5f"))
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.8 * cm, title="Student Report")
    st = snapshot["student"]
    s = []
    s.append(_p(f"{settings['institution_name']} - Student Academic Report", ss["Title"]))
    if settings.get("demo_data"):
        s.append(_p("DEMO DATA - fictional student for demonstration only.", small))
    s.append(_p(f"{st['name']} | Roll no: {st['roll_no']} | {st['program']} | Semester {st['semester']} | "
                f"Generated {date.today().isoformat()}", body))
    s.append(Spacer(1, 8))

    avg = snapshot["average_percent"]
    att = snapshot["overall_attendance_percent"]
    s.append(_p("Verified figures (calculated from official records)", h2))
    s.append(_p(f"Average: {avg if avg is not None else 'N/A'}% (grade {snapshot['overall_grade']}) | "
                f"Attendance: {att if att is not None else 'N/A'}% "
                f"(required: {settings['attendance_threshold']:g}%)", body))
    rows = [["Subject", "Marks %", "Grade", "Attended", "Attendance %"]]
    for sub in snapshot["subjects"]:
        rows.append([sub["name"], sub["percent"] if sub["percent"] is not None else "N/A", sub["grade"],
                     f"{sub['classes_attended']}/{sub['classes_held']}",
                     sub["attendance_percent"] if sub["attendance_percent"] is not None else "N/A"])
    table = Table([[_p(c, body) for c in r] for r in rows], repeatRows=1,
                  colWidths=[6 * cm, 2.5 * cm, 2 * cm, 3 * cm, 3 * cm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe8f5")),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    s += [Spacer(1, 6), table]
    if snapshot["risk_reasons"]:
        s.append(_p("Flags: " + "; ".join(snapshot["risk_reasons"]), body))

    s.append(_p("AI-generated analysis", h2))
    if ai_report:
        s.append(_p("AI-generated recommendations, not guaranteed facts. Review with your teacher or advisor.", small))
        s.append(_p(ai_report["overview"], body))
        sections = [("Strengths", ai_report["strengths"]),
                    ("Recommendations", ai_report["recommendations"]),
                    ("Weekly goals", ai_report["weekly_goals"]),
                    ("Next steps", ai_report["next_steps"])]
        if ai_report["needs_support"]:
            s.append(_p("Subjects needing support", ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold")))
            for item in ai_report["needs_support"]:
                s.append(Paragraph(f"<b>{escape(item['subject'])}:</b> {escape(item['reason'])}", bullet, bulletText="-"))
        s.append(_p("Attendance insight", ParagraphStyle("h3b", parent=body, fontName="Helvetica-Bold")))
        s.append(_p(ai_report["attendance_insight"], body))
        for title, items in sections:
            if items:
                s.append(_p(title, ParagraphStyle("h3c", parent=body, fontName="Helvetica-Bold")))
                for it in items:
                    s.append(Paragraph(escape(it), bullet, bulletText="-"))
    else:
        s.append(_p("No AI analysis was generated for this report.", body))

    if teacher_feedback:
        s.append(_p("Teacher feedback (reviewed and approved)", h2))
        for fb in teacher_feedback:
            s.append(_p(fb["text"], body))
    doc.build(s)
    return buf.getvalue()
