# 🎓 AI Student Management System

A Streamlit app for managing students, marks and attendance, with real AI insights from **Google Gemini Flash**.

> **Demo data:** on first start the app fills an empty database with clearly marked, fictional DEMO data so you can try everything. Remove it from **Settings → Remove demo data** before real use.

## How it works (important design rule)
- **Python + SQLite** hold the official records and do **all calculations** (percentages, averages, grades, attendance, risk flags).
- **Gemini** only receives the finished numbers (no names, roll numbers or emails) and **explains, spots patterns and recommends**.
- The AI can **never change** marks, attendance or grades. AI output is labelled as a recommendation, not a guaranteed fact.
- If Gemini is unavailable, you see a clear error. The app never shows a made-up AI answer.

## Features
| Area | What you get |
|---|---|
| Authentication | Username/password login (PBKDF2-hashed), lock-out after 5 failed attempts, roles: **admin, teacher, student**, change password |
| Student | Overview (average, grade, attendance, charts, semester history), AI performance analysis, AI study plan (based on your weekly hours), AI attendance analysis, **AI chatbot** (own records only), PDF report, approved teacher feedback |
| Teacher | Class overview, per-student analysis, enter marks and attendance for own subjects, **AI class summary** (struggling subjects, revision/extra lesson ideas), **AI feedback drafts** that must be reviewed and approved before students see them |
| Admin | Everything above for all subjects, plus manage students, subjects, enrolments, users and settings (attendance threshold, pass %, attention level) |
| Reports | Downloadable PDF with verified figures, AI analysis (if generated) and approved teacher feedback |

## Project files
```
app.py            # Streamlit UI and navigation
db.py             # SQLite schema, login, role-based access, demo data
analytics.py      # All official calculations
ai_service.py     # Gemini calls (retry on 503/429, backup model, JSON parsing)
reports.py        # PDF generation
requirements.txt  # Dependencies
.gitignore        # Keeps secrets and database files out of GitHub
```

## Run locally
1. Install Python 3.10 or newer.
2. Get a free Gemini API key: https://aistudio.google.com/apikey
3. Provide the key (never put it in code):
   - Create `.streamlit/secrets.toml` containing `GEMINI_API_KEY = "your-key"`, **or**
   - set an environment variable `GEMINI_API_KEY`.
4. Run:
   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```

## Demo accounts (demo data only)
| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `Admin@123` |
| Teacher | `teacher1`, `teacher2` | `Teacher@123` |
| Student | `student1` … `student8` | `Student@123` |

`student1` (Ayesha Khan) matches the example in the specification: Mathematics about 45%, Programming about 82%, Database Systems about 58% and low attendance. **Change these passwords or remove the demo data before any real use.** To hide the demo login hints, set the environment variable `SHOW_DEMO_LOGINS=0`.

## Upload to GitHub (website only)
1. Sign in at https://github.com → **+** → **New repository**. Name it (for example `ai-student-management`), choose **Public**, click **Create repository**.
2. Click **uploading an existing file** and drag in: `app.py`, `db.py`, `analytics.py`, `ai_service.py`, `reports.py`, `requirements.txt`, `README.md`.
3. Click **Commit changes**.
4. Add the ignore file: **Add file → Create new file**, name it `.gitignore`, paste the contents from the `.gitignore` file, commit.

Never upload your API key, `secrets.toml` or any `.db` file.

## Deploy on Streamlit Community Cloud
1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app** → pick your repository, branch `main`, main file `app.py`.
3. **Advanced settings → Secrets**, paste:
   ```toml
   GEMINI_API_KEY = "your-gemini-api-key"
   ```
4. Click **Deploy**. Log in with the demo admin account, change its password, then use **Settings** to configure the institution.

## Important limitations
- **Data persistence:** Streamlit Community Cloud storage is temporary. The SQLite file **resets when the app restarts or redeploys**, which brings back the demo data. For real student records, switch `db.py` to a hosted database (for example PostgreSQL or Supabase) and keep regular backups.
- Free Gemini quotas are limited. The app retries temporary errors (503/429), then tries a backup model, and limits each session to 30 AI requests per hour.
- Student data protection laws (for example FERPA or GDPR) may apply to real data. Check your institution's policy before storing real records or sending them to a third-party AI service.
- Model names change over time. If you see "model not found", edit `PRIMARY_MODEL` and `FALLBACK_MODEL` at the top of `ai_service.py`.

## Troubleshooting
| Problem | Fix |
|---|---|
| "AI service is not configured" | Add `GEMINI_API_KEY` to Secrets or the environment |
| "Gemini rejected the request" | The key is wrong or not enabled |
| `503 UNAVAILABLE` / high demand | Temporary. The app retries and uses a backup model; try again in a minute |
| Locked out after failed logins | Wait 5 minutes, or ask an admin to help |
| Demo data came back | The cloud storage reset; see "Data persistence" |
