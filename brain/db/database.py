from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import bcrypt

_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_ROOT, "store", "te1.db")

_SESSION_TTL_DAYS = 30


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            last_login    TEXT
        );

        CREATE TABLE IF NOT EXISTS architectures (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title                  TEXT    NOT NULL,
            description            TEXT,
            region                 TEXT,
            compliance             TEXT,
            budget                 TEXT,
            additional_constraints TEXT,
            messages_json          TEXT    NOT NULL,
            mermaid_diagram        TEXT,
            bicep_code             TEXT,
            quality_score          REAL,
            created_at             TEXT    NOT NULL,
            updated_at             TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token      TEXT    NOT NULL UNIQUE,
            expires_at TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id               INTEGER,
            session_id            TEXT,
            rating                TEXT CHECK(rating IN ('positive','negative','skipped')),
            category              TEXT,
            feedback_text         TEXT,
            retrieved_chunk_ids   TEXT,
            auto_score_overall    REAL,
            auto_score_constraints REAL,
            auto_score_security   REAL,
            auto_score_completeness REAL,
            architecture_summary  TEXT,
            form_values_json      TEXT,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS chunk_feedback_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id        TEXT NOT NULL UNIQUE,
            flag_count      INTEGER DEFAULT 0,
            positive_count  INTEGER DEFAULT 0,
            last_flagged_at TIMESTAMP,
            last_positive_at TIMESTAMP,
            quarantined     INTEGER DEFAULT 0
        );
        """)


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(username: str, email: str, password: str) -> dict | None:
    """Create a new user. Returns user dict or None if username/email already taken."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO users (username, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, email, pw_hash, now),
            )
            return {"id": cur.lastrowid, "username": username, "email": email}
    except sqlite3.IntegrityError:
        return None


def authenticate_user(username: str, password: str) -> dict | None:
    """Return user dict if credentials are valid, else None."""
    with _conn() as con:
        row = con.execute(
            "SELECT id, username, email, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return None
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
    return {"id": row["id"], "username": row["username"], "email": row["email"]}


# ── Sessions ───────────────────────────────────────────────────────────────

def create_session_token(user_id: int) -> str:
    """Create and persist a new session token. Returns the token string."""
    token   = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires),
        )
    return token


def validate_session(token: str) -> dict | None:
    """Return user dict if the token is valid and unexpired, else None."""
    with _conn() as con:
        row = con.execute(
            "SELECT s.user_id, s.expires_at, u.username, u.email "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
        return None
    return {"id": row["user_id"], "username": row["username"], "email": row["email"]}


def delete_session(token: str) -> None:
    """Invalidate a session token (logout)."""
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ── Architectures ──────────────────────────────────────────────────────────

def save_architecture(
    user_id: int,
    title: str,
    form_values: dict,
    llm_history: list[dict],
    mermaid_diagram: str = "",
    bicep_code: str = "",
) -> int:
    """Persist an architecture. Returns the new row id.

    form_values and llm_history are stored together in messages_json so that
    every form field (including hub_vnet) is fully restored on load.
    """
    now = datetime.now(timezone.utc).isoformat()
    fv  = form_values
    payload = json.dumps({"v": 1, "form_values": form_values, "llm_history": llm_history})
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO architectures
               (user_id, title, description, region, compliance, budget,
                additional_constraints, messages_json, mermaid_diagram, bicep_code,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                title,
                fv.get("description", ""),
                fv.get("region", ""),
                fv.get("compliance", ""),
                fv.get("budget", ""),
                fv.get("additional_constraints", ""),
                payload,
                mermaid_diagram,
                bicep_code,
                now, now,
            ),
        )
    return cur.lastrowid


def get_user_architectures(user_id: int) -> list[dict]:
    """Return metadata for all architectures owned by user_id, newest first."""
    with _conn() as con:
        rows = con.execute(
            "SELECT id, title, region, compliance, budget, description, "
            "created_at, updated_at "
            "FROM architectures WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_architecture(arch_id: int, user_id: int) -> dict | None:
    """Load a full architecture. Returns None if not found or not owned by user_id.

    Returned dict has top-level keys: form_values (dict) and llm_history (list),
    decoded from messages_json.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM architectures WHERE id = ? AND user_id = ?",
            (arch_id, user_id),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    raw  = json.loads(data.pop("messages_json"))
    if isinstance(raw, dict) and "llm_history" in raw:
        data["form_values"] = raw.get("form_values", {})
        data["llm_history"] = raw.get("llm_history", [])
    else:
        # Backwards-compat: old rows stored a bare list of messages
        data["form_values"] = {}
        data["llm_history"] = raw if isinstance(raw, list) else []
    return data


def delete_architecture(arch_id: int, user_id: int) -> bool:
    """Delete an architecture. Returns True if a row was deleted."""
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM architectures WHERE id = ? AND user_id = ?",
            (arch_id, user_id),
        )
    return cur.rowcount > 0


# ── Evaluations ────────────────────────────────────────────────────────────

def save_evaluation(
    user_id: int | None,
    session_id: str,
    rating: str,
    category: str,
    feedback_text: str,
    retrieved_chunk_ids: list,
    auto_scores: dict,
    architecture_summary: str,
    form_values: dict,
) -> int:
    """Persist a human evaluation. Returns the new row id."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO evaluations
               (user_id, session_id, rating, category, feedback_text,
                retrieved_chunk_ids, auto_score_overall, auto_score_constraints,
                auto_score_security, auto_score_completeness,
                architecture_summary, form_values_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                session_id,
                rating,
                category,
                feedback_text,
                json.dumps(retrieved_chunk_ids),
                auto_scores.get("overall", 0.5),
                auto_scores.get("constraint_adherence", 0.5),
                auto_scores.get("security_posture", 0.5),
                auto_scores.get("completeness", 0.5),
                architecture_summary,
                json.dumps(form_values),
            ),
        )
    return cur.lastrowid


def update_chunk_feedback_log(
    chunk_ids: list,
    rating: str,
    category: str = "",
) -> None:
    """Update per-chunk feedback counters. Quarantines a chunk after 3 negative flags."""
    now = datetime.now(timezone.utc).isoformat()
    technical = {"wrong_service_behaviour", "wrong_region_availability"}

    with _conn() as con:
        for chunk_id in chunk_ids:
            con.execute(
                "INSERT OR IGNORE INTO chunk_feedback_log (chunk_id) VALUES (?)",
                (chunk_id,),
            )
            if rating == "positive":
                con.execute(
                    "UPDATE chunk_feedback_log "
                    "SET positive_count = positive_count + 1, last_positive_at = ? "
                    "WHERE chunk_id = ?",
                    (now, chunk_id),
                )
            elif rating == "negative" and category in technical:
                con.execute(
                    "UPDATE chunk_feedback_log "
                    "SET flag_count = flag_count + 1, last_flagged_at = ? "
                    "WHERE chunk_id = ?",
                    (now, chunk_id),
                )
                row = con.execute(
                    "SELECT flag_count FROM chunk_feedback_log WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()
                if row and row["flag_count"] >= 3:
                    con.execute(
                        "UPDATE chunk_feedback_log SET quarantined = 1 WHERE chunk_id = ?",
                        (chunk_id,),
                    )


def get_eval_dashboard_stats(user_id: int | None = None) -> dict:
    """Return aggregate evaluation statistics and top flagged chunks."""
    where  = "WHERE user_id = ?" if user_id is not None else ""
    params = (user_id,) if user_id is not None else ()

    with _conn() as con:
        row = con.execute(
            f"""SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN rating = 'positive' THEN 1 ELSE 0 END) AS positive_count,
                    SUM(CASE WHEN rating = 'negative' THEN 1 ELSE 0 END) AS negative_count,
                    SUM(CASE WHEN rating = 'skipped'  THEN 1 ELSE 0 END) AS skip_count,
                    AVG(auto_score_overall) AS avg_auto_score
                FROM evaluations {where}""",
            params,
        ).fetchone()

        total          = row["total"]         or 0
        positive_count = row["positive_count"] or 0
        negative_count = row["negative_count"] or 0
        skip_count     = row["skip_count"]     or 0
        avg_auto_score = row["avg_auto_score"] or 0.0
        skip_rate      = skip_count / total if total > 0 else 0.0

        top_flagged = con.execute(
            "SELECT chunk_id, flag_count, quarantined "
            "FROM chunk_feedback_log "
            "ORDER BY flag_count DESC LIMIT 10"
        ).fetchall()

    return {
        "total_evals":       total,
        "positive_count":    positive_count,
        "negative_count":    negative_count,
        "skip_count":        skip_count,
        "avg_auto_score":    avg_auto_score,
        "skip_rate":         skip_rate,
        "top_flagged_chunks": [dict(r) for r in top_flagged],
    }


def get_recent_evaluations(limit: int = 20) -> list[dict]:
    """Return the most recent evaluations with user info."""
    with _conn() as con:
        rows = con.execute(
            """SELECT e.id, e.session_id, e.rating, e.category, e.feedback_text,
                      e.auto_score_overall, e.created_at, u.username
               FROM evaluations e
               LEFT JOIN users u ON u.id = e.user_id
               ORDER BY e.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
