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
