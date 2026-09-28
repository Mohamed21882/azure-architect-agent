"""Background knowledge base updater — spawned by the admin panel, never run in Streamlit.

    python -m brain.kb.updater              # git pull + incremental ingest
    python -m brain.kb.updater --dry-run    # everything except pull/ingest (testing)

Holds an exclusive flock on data/kb_update/update.lock for its whole lifetime, so a
second run exits immediately (code 3) without touching the status file. Progress is
streamed to data/kb_update/status.json; each run's summary is kept in history.json
(last 10 runs).
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
import uuid

from brain.kb.status import (
    HISTORY_LIMIT,
    HISTORY_PATH,
    KB_DIR,
    LOCK_PATH,
    LOG_PATH,
    read_history,
    snapshot,
    utcnow,
    write_json,
    STATUS_PATH,
    _read_json,
)

_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]\s+(\w+)\s+(.*)$")
_TAIL = 25


def _acquire_lock():
    os.makedirs(KB_DIR, exist_ok=True)
    fh = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def main() -> int:
    parser = argparse.ArgumentParser(description="TE-1 knowledge base updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="skip git pull and ingest; exercise lock/status/history only")
    parser.add_argument("--hold", type=float, default=5.0,
                        help="dry run only: seconds to hold the lock")
    args = parser.parse_args()

    lock = _acquire_lock()
    if lock is None:
        print("Knowledge base update already running — not starting a second one.", file=sys.stderr)
        return 3

    run_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    status: dict = {
        "run_id": run_id, "state": "running", "pid": os.getpid(), "dry_run": args.dry_run,
        "started_at": utcnow(), "phase": "snapshot (before)", "progress": None, "log_tail": [],
    }
    tail: list[str] = []

    def publish(**changes) -> None:
        status.update(changes, log_tail=tail[-_TAIL:], updated_at=utcnow())
        write_json(STATUS_PATH, status)

    publish()
    exit_code = 0
    error = ""
    summary: dict = {}
    before = snapshot()
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as log:
            if args.dry_run:
                publish(phase="dry run (no pull, no ingest)")
                line = f"dry run: holding lock for {args.hold}s"
                log.write(line + "\n")
                tail.append(line)
                time.sleep(args.hold)
            else:
                summary_path = os.path.join(KB_DIR, f"summary-{run_id}.json")
                proc = subprocess.Popen(
                    [sys.executable, "-u", "-m", "brain.ingest.pipeline",
                     "--incremental", "--summary-json", summary_path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                status["child_pid"] = proc.pid
                last_publish = 0.0
                for raw in proc.stdout:
                    line = raw.rstrip()
                    log.write(line + "\n")
                    log.flush()
                    if not line.strip():
                        continue
                    if line.startswith("::phase:: "):
                        status["phase"] = line[len("::phase:: "):]
                        status["progress"] = None
                        last_publish = 0.0
                    else:
                        tail.append(line)
                        m = _PROGRESS.match(line)
                        if m:
                            status["progress"] = {"done": int(m.group(1)), "total": int(m.group(2)),
                                                  "current": m.group(4)[-120:]}
                    if time.monotonic() - last_publish > 2:
                        publish()
                        last_publish = time.monotonic()
                exit_code = proc.wait()
                summary = _read_json(summary_path, {})
                try:
                    os.remove(summary_path)
                except OSError:
                    pass
                if exit_code != 0:
                    error = f"ingest pipeline exited with code {exit_code}"
    except Exception as exc:
        exit_code = exit_code or 1
        error = f"{type(exc).__name__}: {exc}"[:300]

    publish(phase="snapshot (after)", progress=None)
    after = snapshot()

    repos = summary.get("repos", {})
    totals = {k: sum(r.get(k, 0) for r in repos.values())
              for k in ("added", "updated", "superseded", "chunks_added", "chunks_removed", "embed_failed")}
    record = {
        "run_id": run_id,
        "dry_run": args.dry_run,
        "state": "failed" if error else "succeeded",
        "error": error,
        "started_at": status["started_at"],
        "finished_at": utcnow(),
        "duration_s": round(time.perf_counter() - t0, 1),
        "before": before,
        "after": after,
        "repos": repos,
        "totals": totals,
        "decay_updates": summary.get("decay_updates"),
    }
    history = [record] + [h for h in read_history() if h.get("run_id") != run_id]
    write_json(HISTORY_PATH, history[:HISTORY_LIMIT])
    publish(state=record["state"], phase="done", error=error,
            finished_at=record["finished_at"], duration_s=record["duration_s"])
    lock.close()  # releases the flock
    return 0 if not error else 1


if __name__ == "__main__":
    sys.exit(main())
