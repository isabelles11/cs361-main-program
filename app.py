from flask import Flask, render_template, request, redirect, url_for, flash, abort
import sqlite3
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
app.secret_key = "dev_secret_key_change_me"

# Database location
DB_DIR = Path("data")
DB_PATH = DB_DIR / "medimate.db"


def now_iso() -> str:
    """Current timestamp in ISO format (seconds precision)."""
    return datetime.now().isoformat(timespec="seconds")


def get_db_connection() -> sqlite3.Connection:
    """Open a SQLite connection with dict-like rows."""
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist (schema uses time_of_day)."""
    conn = get_db_connection()
    cur = conn.cursor()

    # meds: simple list of meds
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dose TEXT NOT NULL,
            time_of_day TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        );
    """)

    # logs: every "mark taken" creates a row
    cur.execute("""
        CREATE TABLE IF NOT EXISTS taken_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            med_id INTEGER NOT NULL,
            taken_at TEXT NOT NULL,
            FOREIGN KEY (med_id) REFERENCES meds (id) ON DELETE CASCADE
        );
    """)

    conn.commit()
    conn.close()


def validate_med_form(name: str, dose: str, schedule: str) -> tuple[bool, str]:
    """Basic server-side validation."""
    if not name or not dose or not schedule:
        return False, "Please fill in Name, Dose, and Schedule."
    if len(name) > 60:
        return False, "Medication name is too long (max 60 chars)."
    if len(dose) > 60:
        return False, "Dose is too long (max 60 chars)."
    if len(schedule) > 40:
        return False, "Schedule is too long (max 40 chars)."
    return True, ""


@app.route("/")
def index():
    """
    Show all medications.
    IMPORTANT: our DB column is time_of_day, but templates expect schedule.
    So we alias time_of_day AS schedule.
    """
    conn = get_db_connection()
    meds = conn.execute("""
        SELECT
            m.*,
            m.time_of_day AS schedule,
            (SELECT MAX(t.taken_at)
             FROM taken_logs t
             WHERE t.med_id = m.id) AS last_taken
        FROM meds m
        ORDER BY m.time_of_day ASC, m.name ASC;
    """).fetchall()
    conn.close()

    return render_template("index.html", meds=meds, title="MediMate • Medications")


@app.route("/add", methods=["GET", "POST"])
def add_med():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        dose = (request.form.get("dose") or "").strip()

        # Your form field is named "schedule" in the templates
        schedule = (request.form.get("schedule") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        ok, msg = validate_med_form(name, dose, schedule)
        if not ok:
            flash(msg, "error")
            return render_template("add_edit.html", mode="add", med=None, title="MediMate • Add")

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO meds (name, dose, time_of_day, notes, created_at)
            VALUES (?, ?, ?, ?, ?);
        """, (name, dose, schedule, notes, now_iso()))
        conn.commit()
        conn.close()

        flash("Medication added.", "success")
        return redirect(url_for("index"))

    return render_template("add_edit.html", mode="add", med=None, title="MediMate • Add")


@app.route("/edit/<int:med_id>", methods=["GET", "POST"])
def edit_med(med_id: int):
    conn = get_db_connection()
    med = conn.execute("""
        SELECT m.*, m.time_of_day AS schedule
        FROM meds m
        WHERE m.id = ?;
    """, (med_id,)).fetchone()

    if med is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        dose = (request.form.get("dose") or "").strip()
        schedule = (request.form.get("schedule") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        ok, msg = validate_med_form(name, dose, schedule)
        if not ok:
            flash(msg, "error")
            conn.close()
            # Re-render with the original med object
            return render_template("add_edit.html", mode="edit", med=med, title="MediMate • Edit")

        conn.execute("""
            UPDATE meds
            SET name = ?, dose = ?, time_of_day = ?, notes = ?
            WHERE id = ?;
        """, (name, dose, schedule, notes, med_id))
        conn.commit()
        conn.close()

        flash("Medication updated.", "success")
        return redirect(url_for("index"))

    conn.close()
    return render_template("add_edit.html", mode="edit", med=med, title="MediMate • Edit")


@app.route("/take/<int:med_id>", methods=["POST"])
def mark_taken(med_id: int):
    conn = get_db_connection()

    exists = conn.execute("SELECT id FROM meds WHERE id = ?;", (med_id,)).fetchone()
    if exists is None:
        conn.close()
        flash("Medication not found.", "error")
        return redirect(url_for("index"))

    conn.execute("""
        INSERT INTO taken_logs (med_id, taken_at)
        VALUES (?, ?);
    """, (med_id, now_iso()))
    conn.commit()
    conn.close()

    flash("✅ Marked as taken.", "success")
    return redirect(url_for("index"))


@app.route("/delete/<int:med_id>", methods=["POST"])
def delete_med(med_id: int):
    conn = get_db_connection()

    # delete logs first (safe even without cascade)
    conn.execute("DELETE FROM taken_logs WHERE med_id = ?;", (med_id,))
    conn.execute("DELETE FROM meds WHERE id = ?;", (med_id,))
    conn.commit()
    conn.close()

    flash("Medication deleted.", "success")
    return redirect(url_for("index"))


@app.route("/history")
def history():
    """
    History supports optional filter:
      /history?day=today
    """
    day = (request.args.get("day") or "").strip().lower()

    conn = get_db_connection()

    if day == "today":
        logs = conn.execute("""
            SELECT
                t.taken_at,
                m.name,
                m.dose,
                m.time_of_day AS schedule
            FROM taken_logs t
            JOIN meds m ON m.id = t.med_id
            WHERE date(t.taken_at) = date('now', 'localtime')
            ORDER BY t.taken_at DESC
            LIMIT 250;
        """).fetchall()
    else:
        logs = conn.execute("""
            SELECT
                t.taken_at,
                m.name,
                m.dose,
                m.time_of_day AS schedule
            FROM taken_logs t
            JOIN meds m ON m.id = t.med_id
            ORDER BY t.taken_at DESC
            LIMIT 250;
        """).fetchall()

    conn.close()
    return render_template("history.html", logs=logs, day=day, title="MediMate • History")


@app.route("/about")
def about():
    return render_template("about.html", title="MediMate • About")


@app.errorhandler(404)
def not_found(_):
    return render_template("about.html", title="MediMate • Not Found", not_found=True), 404


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
