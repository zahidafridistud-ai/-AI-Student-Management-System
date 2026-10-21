"""AI Student Management System - Streamlit app.

Python + SQLite hold the official records and do all calculations.
Gemini only explains the verified numbers and makes recommendations.
"""

import os
import time
from datetime import date

import pandas as pd
import streamlit as st

import ai_service
import analytics
import db
import reports

AI_NOTE = "🤖 AI-generated recommendation based on recorded data. It is guidance, not a guaranteed fact."
CATEGORY_LABEL = {"performing_well": "🟢 Performing well", "on_track": "🟡 On track",
                  "needs_support": "🔴 Needs support"}
STUDENT_PAGES = ["My Overview", "AI Insights", "AI Assistant", "My Report"]
STAFF_PAGES = ["Class Overview", "Student Analysis", "Enter Marks", "Enter Attendance", "AI Teacher Assistant"]
ADMIN_PAGES = STAFF_PAGES + ["Manage Data", "Settings"]
QUICK_QUESTIONS = ["How am I performing this semester?", "Which subjects need improvement?",
                   "Create a study plan for my exams.", "What is my current attendance percentage?",
                   "How can I improve my grades?"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
@st.cache_resource
def init_database():
    db.init_db()
    return True


def get_api_key() -> str:
    """Key comes only from Streamlit secrets or environment variables, never from code or the UI."""
    try:
        value = st.secrets["GEMINI_API_KEY"]
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY", "").strip()


def ai_guard(limit=30, window=3600) -> bool:
    """Simple per-session rate limit to protect the API quota."""
    now = time.time()
    calls = [t for t in st.session_state.get("ai_calls", []) if now - t < window]
    if len(calls) >= limit:
        return False
    calls.append(now)
    st.session_state["ai_calls"] = calls
    return True


def run_ai(fn):
    """Run fn(api_key, status_cb). Shows a clear error and returns None on failure."""
    key = get_api_key()
    if not key:
        st.error("The AI service is not configured: add GEMINI_API_KEY to Streamlit Secrets or the environment.")
        return None
    if not ai_guard():
        st.warning("AI request limit reached for this session. Please try again later.")
        return None
    status = st.empty()
    try:
        with st.spinner("Asking Gemini..."):
            return fn(key, lambda msg: status.info(msg))
    except ai_service.AIServiceError as exc:
        st.error(f"AI service unavailable: {exc} No AI result was produced.")
    except Exception:
        st.error("Unexpected error while contacting the AI service. No AI result was produced.")
    finally:
        status.empty()
    return None


def fmt(value, suffix=""):
    return "N/A" if value is None else f"{value}{suffix}"


def flags_for(sub: dict) -> str:
    out = []
    if sub["failing"]:
        out.append("Failing")
    elif sub["needs_attention"]:
        out.append("Needs attention")
    if sub["low_attendance"]:
        out.append("Low attendance")
    return ", ".join(out) or "OK"


def current_user():
    uid = st.session_state.get("uid")
    return db.get_user(uid) if uid else None


def my_snapshot(user, student_id, settings):
    return analytics.build_snapshot(db.student_records(user, student_id), settings)


def inject_css():
    st.markdown("""<style>
    .block-container {padding-top: 2rem; max-width: 1200px;}
    [data-testid="stMetric"] {background: rgba(128,128,128,0.08); padding: 12px 16px; border-radius: 10px;}
    </style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Login and sidebar
# --------------------------------------------------------------------------
def login_page(settings):
    st.title("🎓 AI Student Management System")
    st.caption(settings["institution_name"])
    if settings["demo_data"]:
        st.warning("DEMO DATA: all students, marks and attendance are fictional and for demonstration only.")
        if os.environ.get("SHOW_DEMO_LOGINS", "1") != "0":
            with st.expander("Demo accounts"):
                st.markdown("- Admin: `admin` / `Admin@123`\n- Teacher: `teacher1` or `teacher2` / `Teacher@123`\n"
                            "- Student: `student1` ... `student8` / `Student@123`\n\n"
                            "Change these passwords or remove the demo data before real use.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        user, message = db.authenticate(username, password)
        if user:
            st.session_state["uid"] = user["id"]
            st.rerun()
        else:
            st.error(message)


def sidebar(user, settings):
    with st.sidebar:
        st.markdown(f"### 🎓 {settings['institution_name']}")
        st.markdown(f"**{user['full_name']}**")
        st.caption(user["role"].title())
        pages = {"student": STUDENT_PAGES, "teacher": STAFF_PAGES, "admin": ADMIN_PAGES}[user["role"]]
        page = st.radio("Navigate", pages, key="nav")
        st.caption("AI service: " + ("configured ✅" if get_api_key() else "not configured ⚠️"))
        with st.expander("Change password"):
            with st.form("pw_form"):
                old = st.text_input("Current password", type="password")
                new = st.text_input("New password", type="password")
                again = st.text_input("Repeat new password", type="password")
                if st.form_submit_button("Update"):
                    if new != again:
                        st.error("New passwords do not match.")
                    else:
                        try:
                            db.change_password(user["id"], old, new)
                            st.success("Password updated.")
                        except ValueError as exc:
                            st.error(str(exc))
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
    return page


# --------------------------------------------------------------------------
# Shared views
# --------------------------------------------------------------------------
def overview_view(user, snap, settings):
    if not snap["subjects"]:
        st.info("No subjects or records are available for this semester yet.")
        return
    if user["role"] == "teacher":
        st.caption("Showing records for the subjects you teach only.")
    thr = settings["attendance_threshold"]
    att = snap["overall_attendance_percent"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Average", fmt(snap["average_percent"], "%"))
    c2.metric("Grade", snap["overall_grade"])
    c3.metric("Attendance", fmt(att, "%"),
              delta=None if att is None else f"{att - thr:+.1f} vs {thr:g}% required")
    c4.metric("Status", CATEGORY_LABEL[snap["category"]])
    if snap["risk_reasons"]:
        st.warning("Flags: " + "; ".join(snap["risk_reasons"]))

    df = pd.DataFrame([{
        "Subject": s["name"], "Marks %": s["percent"], "Grade": s["grade"],
        "Attendance %": s["attendance_percent"],
        "Classes attended": f"{s['classes_attended']}/{s['classes_held']}", "Flags": flags_for(s),
    } for s in snap["subjects"]])
    st.dataframe(df, hide_index=True)
    chart = df.set_index("Subject")[["Marks %", "Attendance %"]].apply(pd.to_numeric, errors="coerce").fillna(0)
    st.bar_chart(chart)
    if len(snap["history"]) >= 2:
        st.markdown("**Semester averages**")
        st.line_chart(pd.DataFrame(snap["history"]).set_index("semester"))
    st.caption("Percentages, grades and attendance are calculated by the system from official records.")


def render_report(r):
    st.caption(AI_NOTE)
    st.markdown(r["overview"])
    left, right = st.columns(2)
    with left:
        st.markdown("#### ✅ Strengths")
        for item in r["strengths"] or ["None listed"]:
            st.markdown(f"- {item}")
    with right:
        st.markdown("#### ⚠️ Needs support")
        for item in r["needs_support"] or [{"subject": "None listed", "reason": ""}]:
            st.markdown(f"- **{item['subject']}** {item['reason']}")
    st.markdown("#### 📅 Attendance insight")
    st.markdown(r["attendance_insight"] or "Not available.")
    for title, key in (("💡 Recommendations", "recommendations"), ("🎯 Weekly goals", "weekly_goals"),
                       ("➡️ Next steps", "next_steps")):
        st.markdown(f"#### {title}")
        for item in r[key]:
            st.markdown(f"- {item}")


def render_plan(p):
    st.caption(AI_NOTE)
    st.markdown(p["summary"])
    if p["weekly_plan"]:
        st.dataframe(pd.DataFrame(p["weekly_plan"]).rename(columns={
            "day": "Day", "focus": "Focus", "hours": "Hours", "activities": "Activities"}), hide_index=True)
        st.caption(f"Total planned: {p['total_hours']} h (you have {p['requested_hours']} h available).")
        if p["total_hours"] > p["requested_hours"] * 1.05:
            st.warning("The AI plan exceeds your available study hours. Trim it or regenerate.")
    for title, key in (("🎯 Weekly goals", "weekly_goals"), ("📅 Attendance tips", "attendance_tips"),
                       ("💡 Study tips", "tips")):
        if p[key]:
            st.markdown(f"#### {title}")
            for item in p[key]:
                st.markdown(f"- {item}")


def render_attendance_ai(a):
    st.caption(AI_NOTE)
    st.markdown(a["assessment"])
    st.markdown("**Possible impact on results**")
    st.markdown(a["possible_impact"] or "Not available.")
    st.markdown("**Suggestions**")
    for item in a["suggestions"]:
        st.markdown(f"- {item}")


def ai_insights(user, snap, settings):
    if not snap["subjects"]:
        st.info("No records are available to analyse yet.")
        return
    sid, sem = snap["student"]["id"], snap["student"]["semester"]
    payload = analytics.ai_payload(snap, settings)
    ss = st.session_state
    tab1, tab2, tab3 = st.tabs(["Performance analysis", "Study plan", "Attendance analysis"])

    with tab1:
        key = f"ai_report_{sid}_{sem}"
        if st.button("Regenerate analysis" if key in ss else "Generate analysis", key=f"b_{key}"):
            res = run_ai(lambda k, cb: ai_service.student_report(k, payload, status_cb=cb))
            if res:
                ss[key] = res
        if key in ss:
            render_report(ss[key])

    with tab2:
        key = f"ai_plan_{sid}_{sem}"
        hours = st.slider("Study hours available per week", 2, 40, 12, key=f"h_{key}")
        weeks = st.number_input("Weeks until exams", 1, 20, 4, key=f"w_{key}")
        if st.button("Create study plan", key=f"b_{key}"):
            res = run_ai(lambda k, cb: ai_service.study_plan(k, payload, hours, int(weeks), status_cb=cb))
            if res:
                ss[key] = res
        if key in ss:
            render_plan(ss[key])

    with tab3:
        key = f"ai_att_{sid}_{sem}"
        att = snap["overall_attendance_percent"]
        st.metric("Overall attendance", fmt(att, "%"))
        low = [s["name"] for s in snap["subjects"] if s["low_attendance"]]
        if low:
            st.warning("Below the required threshold in: " + ", ".join(low))
        if st.button("Analyse attendance", key=f"b_{key}"):
            res = run_ai(lambda k, cb: ai_service.attendance_analysis(k, payload, status_cb=cb))
            if res:
                ss[key] = res
        if key in ss:
            render_attendance_ai(ss[key])


def report_tab(user, snap, settings):
    sid, sem = snap["student"]["id"], snap["student"]["semester"]
    key = f"ai_report_{sid}_{sem}"
    payload = analytics.ai_payload(snap, settings)
    if key not in st.session_state and st.button("Generate AI analysis for the report", key=f"rb_{key}"):
        res = run_ai(lambda k, cb: ai_service.student_report(k, payload, status_cb=cb))
        if res:
            st.session_state[key] = res
    ai_report = st.session_state.get(key)
    if ai_report:
        render_report(ai_report)
    else:
        st.info("No AI analysis yet. The PDF will contain only verified figures unless you generate it first.")
    try:
        pdf = reports.build_pdf(snap, settings, ai_report,
                                db.approved_feedback(user) if user["role"] == "student" else None)
        st.download_button("⬇️ Download PDF report", data=pdf,
                           file_name=f"report_{snap['student']['roll_no']}.pdf", mime="application/pdf")
    except Exception:
        st.error("The PDF could not be generated.")


# --------------------------------------------------------------------------
# Student pages
# --------------------------------------------------------------------------
def page_my_overview(user, settings):
    st.header("My Overview")
    snap = my_snapshot(user, user["student_id"], settings)
    overview_view(user, snap, settings)
    feedback = db.approved_feedback(user)
    if feedback:
        st.subheader("Teacher feedback")
        for fb in feedback:
            st.info(fb["text"])


def page_ai_insights(user, settings):
    st.header("AI Insights")
    ai_insights(user, my_snapshot(user, user["student_id"], settings), settings)


def page_assistant(user, settings):
    st.header("AI Student Assistant")
    st.caption("Ask about your own performance and attendance. " + AI_NOTE)
    snap = my_snapshot(user, user["student_id"], settings)
    if not snap["subjects"]:
        st.info("No records are available yet.")
        return
    payload = analytics.ai_payload(snap, settings)  # always the logged-in student's own data
    history = st.session_state.setdefault("chat", [])
    cols = st.columns(len(QUICK_QUESTIONS))
    for i, (col, q) in enumerate(zip(cols, QUICK_QUESTIONS)):
        if col.button(q, key=f"quick_{i}"):
            st.session_state["chat_pending"] = q
    for m in history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    question = st.chat_input("Ask about your marks, attendance or study plan...")
    question = question or st.session_state.pop("chat_pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            answer = run_ai(lambda k, cb: ai_service.chat_answer(k, payload, history, question, status_cb=cb))
            if answer:
                st.markdown(answer)
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": answer})
    if history and st.button("Clear conversation"):
        st.session_state["chat"] = []
        st.rerun()


def page_my_report(user, settings):
    st.header("My Report")
    report_tab(user, my_snapshot(user, user["student_id"], settings), settings)


# --------------------------------------------------------------------------
# Staff pages
# --------------------------------------------------------------------------
def class_data(user, settings):
    snaps = [my_snapshot(user, s["id"], settings) for s in db.visible_students(user)]
    snaps = [s for s in snaps if s["subjects"]]
    return snaps, analytics.class_stats(snaps, settings)


def page_class_overview(user, settings):
    st.header("Class Overview")
    snaps, stats = class_data(user, settings)
    if not snaps:
        st.info("No student records are available yet.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Students", stats["student_count"])
    c2.metric("Class average", fmt(stats["class_average_percent"], "%"))
    c3.metric("Avg attendance", fmt(stats["class_average_attendance_percent"], "%"))
    c4.metric("Need support", stats["students_needing_support"])
    st.subheader("Subjects")
    sdf = pd.DataFrame(stats["subjects"]).rename(columns={
        "subject": "Subject", "students_graded": "Graded", "average_percent": "Average %",
        "students_below_attention_level": "Below attention level", "percent_below_attention_level": "% below",
        "students_failing": "Failing", "average_attendance_percent": "Avg attendance %"})
    st.dataframe(sdf, hide_index=True)
    st.bar_chart(sdf.set_index("Subject")[["Average %"]].apply(pd.to_numeric, errors="coerce").fillna(0))
    st.subheader("Students")
    order = {"needs_support": 0, "on_track": 1, "performing_well": 2}
    rows = sorted(snaps, key=lambda s: order[s["category"]])
    st.dataframe(pd.DataFrame([{
        "Student": s["student"]["name"], "Roll no": s["student"]["roll_no"], "Average %": s["average_percent"],
        "Attendance %": s["overall_attendance_percent"], "Status": CATEGORY_LABEL[s["category"]],
        "Flags": "; ".join(s["risk_reasons"]) or "-"} for s in rows]), hide_index=True)


def page_student_analysis(user, settings):
    st.header("Student Analysis")
    students = db.visible_students(user)
    if not students:
        st.info("No students available.")
        return
    chosen = st.selectbox("Student", students, format_func=lambda s: f"{s['name']} ({s['roll_no']})")
    snap = my_snapshot(user, chosen["id"], settings)
    t1, t2, t3 = st.tabs(["Overview", "AI insights", "Report"])
    with t1:
        overview_view(user, snap, settings)
    with t2:
        ai_insights(user, snap, settings)
    with t3:
        report_tab(user, snap, settings)


def page_enter_marks(user, settings):
    st.header("Enter Marks")
    subjects = db.editable_subjects(user)
    if not subjects:
        st.info("You have no subjects assigned.")
        return
    sub = st.selectbox("Subject", subjects, format_func=lambda s: f"{s['code']} - {s['name']} (Semester {s['semester']})")
    c1, c2 = st.columns(2)
    assessment = c1.text_input("Assessment name", "Quiz").strip()
    max_marks = c2.number_input("Maximum marks", 1.0, 1000.0, 20.0)
    students = db.enrolled_students(user, sub["id"])
    if not students:
        st.info("No students are enrolled in this subject.")
        return
    existing = db.get_marks_entry(user, sub["id"], sub["semester"], assessment)
    df = pd.DataFrame({
        "student_id": [s["id"] for s in students], "Roll no": [s["roll_no"] for s in students],
        "Student": [s["name"] for s in students],
        "Marks obtained": pd.Series([existing.get(s["id"], float("nan")) for s in students], dtype="float64"),
    })
    edited = st.data_editor(df, hide_index=True, disabled=["student_id", "Roll no", "Student"],
                            column_config={"student_id": None,
                                           "Marks obtained": st.column_config.NumberColumn(min_value=0.0, max_value=float(max_marks), step=0.5)},
                            key=f"marks_{sub['id']}_{assessment}")
    st.caption("Marks you save become official records. The AI cannot change them.")
    if st.button("Save marks", type="primary"):
        values = {int(r["student_id"]): (None if pd.isna(r["Marks obtained"]) else float(r["Marks obtained"]))
                  for _, r in edited.iterrows()}
        try:
            n = db.save_marks(user, sub["id"], sub["semester"], assessment, float(max_marks), values)
            st.success(f"Saved marks for {n} student(s).")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


def page_enter_attendance(user, settings):
    st.header("Enter Attendance")
    subjects = db.editable_subjects(user)
    if not subjects:
        st.info("You have no subjects assigned.")
        return
    sub = st.selectbox("Subject", subjects, format_func=lambda s: f"{s['code']} - {s['name']}", key="att_sub")
    day = st.date_input("Class date", value=date.today(), max_value=date.today())
    students = db.enrolled_students(user, sub["id"])
    if not students:
        st.info("No students are enrolled in this subject.")
        return
    existing = db.get_attendance_entry(user, sub["id"], day)
    df = pd.DataFrame({"student_id": [s["id"] for s in students], "Roll no": [s["roll_no"] for s in students],
                       "Student": [s["name"] for s in students],
                       "Present": [existing.get(s["id"], True) for s in students]})
    edited = st.data_editor(df, hide_index=True, disabled=["student_id", "Roll no", "Student"],
                            column_config={"student_id": None, "Present": st.column_config.CheckboxColumn()},
                            key=f"att_{sub['id']}_{day}")
    if st.button("Save attendance", type="primary"):
        values = {int(r["student_id"]): bool(r["Present"]) for _, r in edited.iterrows()}
        try:
            db.save_attendance(user, sub["id"], day, values)
            st.success(f"Attendance saved for {len(values)} student(s) on {day}.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))


def page_teacher_assistant(user, settings):
    st.header("AI Teacher Assistant")
    st.caption(AI_NOTE + " Feedback drafts must be reviewed by a teacher before they are published.")
    t1, t2, t3 = st.tabs(["Class summary", "Draft feedback", "Review & publish"])

    with t1:
        snaps, stats = class_data(user, settings)
        if not snaps:
            st.info("No student records are available yet.")
        else:
            struggling = [r for r in stats["subjects"]
                          if (r["percent_below_attention_level"] or 0) >= 40 or r["students_failing"] > 0]
            st.markdown("**Subjects where many students are struggling (calculated):** " +
                        (", ".join(r["subject"] for r in struggling) or "none"))
            key = f"ai_class_{user['id']}"
            if st.button("Regenerate AI summary" if key in st.session_state else "Generate AI class summary"):
                res = run_ai(lambda k, cb: ai_service.class_summary(k, stats, status_cb=cb))
                if res:
                    st.session_state[key] = res
            r = st.session_state.get(key)
            if r:
                st.markdown(r["overview"])
                st.markdown("#### Struggling subjects")
                for item in r["struggling_subjects"]:
                    st.markdown(f"- **{item['subject']}**: {item['evidence']}")
                st.markdown("#### Revision session ideas")
                for item in r["revision_suggestions"]:
                    st.markdown(f"- {item}")
                st.markdown("#### Suggested additional lessons")
                for item in r["additional_lessons"]:
                    st.markdown(f"- {item}")
                st.markdown(f"**Attendance:** {r['attendance_note']}")

    with t2:
        students = db.visible_students(user)
        if not students:
            st.info("No students available.")
        else:
            chosen = st.selectbox("Student", students, key="fb_student",
                                  format_func=lambda s: f"{s['name']} ({s['roll_no']})")
            sid = chosen["id"]
            snap = my_snapshot(user, sid, settings)
            text_key = f"fb_text_{sid}"
            if st.button("Generate AI draft"):
                payload = analytics.ai_payload(snap, settings)
                draft = run_ai(lambda k, cb: ai_service.feedback_draft(k, payload, status_cb=cb))
                if draft:
                    st.session_state[text_key] = draft
            if text_key in st.session_state:
                st.text_area("Draft feedback (edit before publishing)", key=text_key, height=220)
                c1, c2 = st.columns(2)
                if c1.button("Save as draft"):
                    db.save_feedback(user, sid, st.session_state[text_key])
                    st.success("Draft saved. It is not visible to the student yet.")
                if c2.button("Approve & publish to student", type="primary"):
                    db.save_feedback(user, sid, st.session_state[text_key], approve=True)
                    st.success("Feedback approved and published.")

    with t3:
        items = db.list_feedback(user)
        if not items:
            st.info("No feedback saved yet.")
        for it in items:
            label = "✅ Approved" if it["status"] == "approved" else "📝 Draft"
            with st.expander(f"{label} - {it['student_name']} ({it['created_at']})"):
                new = st.text_area("Text", value=it["text"], key=f"edit_{it['id']}", height=160)
                c1, c2 = st.columns(2)
                if c1.button("Save changes", key=f"sv_{it['id']}"):
                    db.save_feedback(user, it["student_id"], new, approve=it["status"] == "approved", draft_id=it["id"])
                    st.success("Saved.")
                if it["status"] != "approved" and c2.button("Approve & publish", key=f"ap_{it['id']}"):
                    db.save_feedback(user, it["student_id"], new, approve=True, draft_id=it["id"])
                    st.rerun()


# --------------------------------------------------------------------------
# Admin pages
# --------------------------------------------------------------------------
def page_manage(user, settings):
    st.header("Manage Data")
    t1, t2, t3, t4 = st.tabs(["Students", "Subjects", "Enrolments", "Users"])
    with t1:
        st.dataframe(pd.DataFrame(db.all_students()), hide_index=True)
        with st.form("add_student"):
            c1, c2 = st.columns(2)
            roll, name = c1.text_input("Roll number"), c2.text_input("Full name")
            email, program = c1.text_input("Email"), c2.text_input("Program")
            sem = st.number_input("Current semester", 1, 12, 1)
            if st.form_submit_button("Add student"):
                try:
                    db.add_student(user, roll, name, email, program, sem)
                    st.success("Student added.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with t2:
        st.dataframe(pd.DataFrame(db.all_subjects()).drop(columns=["id", "teacher_id"], errors="ignore"), hide_index=True)
        teachers = [{"id": None, "full_name": "(none)"}] + db.teachers()
        with st.form("add_subject"):
            c1, c2 = st.columns(2)
            code, name = c1.text_input("Subject code"), c2.text_input("Subject name")
            sem = c1.number_input("Semester", 1, 12, 1)
            teacher = c2.selectbox("Teacher", teachers, format_func=lambda t: t["full_name"])
            if st.form_submit_button("Add subject"):
                try:
                    db.add_subject(user, code, name, sem, teacher["id"])
                    st.success("Subject added.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with t3:
        students, subjects = db.all_students(), db.all_subjects()
        if students and subjects:
            stu = st.selectbox("Student", students, key="enr_stu", format_func=lambda s: f"{s['name']} ({s['roll_no']})")
            current = set(db.enrollment_ids(stu["id"]))
            chosen = st.multiselect("Enrolled subjects", subjects, key=f"enr_{stu['id']}",
                                    default=[s for s in subjects if s["id"] in current],
                                    format_func=lambda s: f"{s['code']} - {s['name']} (Sem {s['semester']})")
            if st.button("Save enrolments"):
                db.set_enrollments(user, stu["id"], [s["id"] for s in chosen])
                st.success("Enrolments saved.")
        else:
            st.info("Add students and subjects first.")
    with t4:
        st.dataframe(pd.DataFrame(db.list_users()).drop(columns=["student_id"], errors="ignore"), hide_index=True)
        students = db.all_students()
        with st.form("add_user"):
            c1, c2 = st.columns(2)
            uname, full = c1.text_input("Username"), c2.text_input("Full name")
            role = c1.selectbox("Role", list(db.ROLES), index=2)
            pw = c2.text_input("Initial password (min 8 chars)", type="password")
            link = st.selectbox("Linked student record (students only)", [None] + students,
                                format_func=lambda s: "(none)" if s is None else f"{s['name']} ({s['roll_no']})")
            if st.form_submit_button("Create user"):
                try:
                    db.create_user(uname, pw, role, full, link["id"] if link else None)
                    st.success("User created.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        users = db.list_users()
        target = st.selectbox("Activate / deactivate account", users, format_func=lambda u: f"{u['username']} ({u['role']})")
        if st.button("Toggle active status"):
            try:
                db.set_user_active(user, target["id"], not target["active"])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def page_settings(user, settings):
    st.header("Settings")
    with st.form("settings"):
        name = st.text_input("Institution name", settings["institution_name"])
        att = st.number_input("Minimum attendance (%)", 0.0, 100.0, float(settings["attendance_threshold"]))
        pas = st.number_input("Pass percentage (%)", 0.0, 100.0, float(settings["pass_percentage"]))
        weak = st.number_input("'Needs attention' below (%)", 0.0, 100.0, float(settings["weak_threshold"]))
        if st.form_submit_button("Save settings"):
            try:
                db.save_settings(user, {"institution_name": name, "attendance_threshold": att,
                                        "pass_percentage": pas, "weak_threshold": weak})
                st.success("Settings saved.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if settings["demo_data"]:
        st.subheader("Demo data")
        st.warning("This deletes ALL demo students, subjects, marks, attendance, feedback and every user except you.")
        if st.checkbox("I understand") and st.button("Remove demo data"):
            db.remove_demo_data(user)
            st.success("Demo data removed. Change your admin password in the sidebar.")
            st.rerun()
    st.caption("Note: on Streamlit Community Cloud the SQLite file is not permanent and resets when the app restarts. "
               "Use an external database for production.")


PAGE_FUNCS = {
    "My Overview": page_my_overview, "AI Insights": page_ai_insights, "AI Assistant": page_assistant,
    "My Report": page_my_report, "Class Overview": page_class_overview,
    "Student Analysis": page_student_analysis, "Enter Marks": page_enter_marks,
    "Enter Attendance": page_enter_attendance, "AI Teacher Assistant": page_teacher_assistant,
    "Manage Data": page_manage, "Settings": page_settings,
}


def main():
    st.set_page_config(page_title="AI Student Management System", page_icon="🎓", layout="wide")
    inject_css()
    init_database()
    settings = db.get_settings()
    user = current_user()
    if not user:
        login_page(settings)
        return
    page = sidebar(user, settings)
    if settings["demo_data"]:
        st.info("DEMO DATA: all records shown are fictional.")
    try:
        PAGE_FUNCS[page](user, db.get_settings())
    except PermissionError as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
