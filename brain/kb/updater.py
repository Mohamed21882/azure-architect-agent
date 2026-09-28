"""Background knowledge base updater — spawned by the admin panel, never run in Streamlit.

    python -m brain.kb.updater              # git pull + incremental ingest (active index)
    python -m brain.kb.updater --rebuild    # pull all repos + full rebuild into a NEW index
    python -m brain.kb.updater --dry-run    # lock/status/history only (testing)

Holds an exclusive flock on data/kb_update/update.lock for its whole lifetime, so a
second run of either mode exits immediately (code 3) without touching the status file.
Progress is streamed to data/kb_update/status.json; each run's summary is kept in
history.json (last 10 runs).

A rebuild never touches the serving index: it builds azure_wiki_<timestamp> plus
brain/store/bm25_<name>.pkl, verifies it, compares it with the serving index
(counts + sample searches) and records it in rebuild.json as a candidate. Switching
is a separate, explicit step (brain.kb.index_switch or the admin panel).
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import os
import re
import subprocess
import sys
import time
import uuid

from brain.config import CONFIG, BrainConfig
from brain.kb.status import (
    HISTORY_LIMIT,
    HISTORY_PATH,
    KB_DIR,
    LOCK_PATH,
    LOG_PATH,
    MS_REPOS,
    REBUILD_PATH,
    STATUS_PATH,
    _read_json,
    read_history,
    rebuild_gate,
    snapshot,
    utcnow,
    write_json,
)

_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]\s+(\w+)\s+(.*)$")
_TQDM = re.compile(r"^(Chunking|Embedding|Qdrant)\s+\[([^\]]+)\]:.*?(\d+)/(\d+)")
_TAIL = 25

SAMPLE_QUERIES = (
    "hub-spoke network topology with Azure Firewall",
    "private endpoint for Azure AI Search",
    "AKS baseline architecture",
    "Azure OpenAI availability in Qatar Central",
    "Microsoft Fabric reliability capacity planning",
    "az network vnet create",
    "Azure AI Foundry hub project managed network",
    "production-grade RAG hub-spoke design approved session",
)


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


class Run:
    """Status publishing + subprocess streaming shared by every mode."""

    def __init__(self, kind: str, dry_run: bool) -> None:
        self.run_id = uuid.uuid4().hex[:8]
        self.t0 = time.perf_counter()
        self.tail: list[str] = []
        self.status: dict = {
            "run_id": self.run_id, "kind": kind, "state": "running", "pid": os.getpid(),
            "dry_run": dry_run, "started_at": utcnow(), "phase": "starting",
            "progress": None, "log_tail": [],
        }
        self.log = open(LOG_PATH, "w", encoding="utf-8")
        self.publish()

    def publish(self, **changes) -> None:
        self.status.update(changes, log_tail=self.tail[-_TAIL:], updated_at=utcnow())
        write_json(STATUS_PATH, self.status)

    def note(self, line: str) -> None:
        self.log.write(line + "\n")
        self.log.flush()
        self.tail.append(line)

    def stream(self, cmd: list[str]) -> int:
        env = dict(os.environ, PYTHONUNBUFFERED="1", TQDM_MININTERVAL="5")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=env)
        self.status["child_pid"] = proc.pid
        last = 0.0
        for raw in proc.stdout:  # universal newlines: tqdm's \\r refreshes arrive as lines
            line = raw.rstrip()
            if not line.strip():
                continue
            self.log.write(line + "\n")
            if line.startswith("::phase:: "):
                self.status["phase"] = line[len("::phase:: "):]
                self.status["progress"] = None
                last = 0.0
            elif m := _TQDM.match(line):
                self.status["progress"] = {"done": int(m.group(3)), "total": int(m.group(4)),
                                           "current": f"{m.group(1)} {m.group(2)}"}
            else:
                self.tail.append(line)
                if m := _PROGRESS.match(line):
                    self.status["progress"] = {"done": int(m.group(1)), "total": int(m.group(2)),
                                               "current": m.group(4)[-120:]}
            if time.monotonic() - last > 2:
                self.log.flush()
                self.publish()
                last = time.monotonic()
        return proc.wait()

    def finish(self, record: dict) -> None:
        record.setdefault("run_id", self.run_id)
        record.setdefault("kind", self.status["kind"])
        record.setdefault("dry_run", self.status["dry_run"])
        record.setdefault("started_at", self.status["started_at"])
        record["finished_at"] = utcnow()
        record["duration_s"] = round(time.perf_counter() - self.t0, 1)
        history = [record] + [h for h in read_history() if h.get("run_id") != self.run_id]
        write_json(HISTORY_PATH, history[:HISTORY_LIMIT])
        self.publish(state=record["state"], phase="done", error=record.get("error", ""),
                     finished_at=record["finished_at"], duration_s=record["duration_s"])
        self.log.close()


# ── Incremental update (active index) ─────────────────────────────────────

def run_incremental_update(run: Run, dry_run: bool, hold: float) -> dict:
    run.publish(phase="snapshot (before)")
    before = snapshot()
    error, summary = "", {}
    if dry_run:
        run.publish(phase="dry run (no pull, no ingest)")
        run.note(f"dry run: holding lock for {hold}s")
        time.sleep(hold)
    else:
        summary_path = os.path.join(KB_DIR, f"summary-{run.run_id}.json")
        code = run.stream([sys.executable, "-u", "-m", "brain.ingest.pipeline",
                           "--incremental", "--summary-json", summary_path])
        summary = _read_json(summary_path, {})
        try:
            os.remove(summary_path)
        except OSError:
            pass
        if code != 0:
            error = f"ingest pipeline exited with code {code}"
    run.publish(phase="snapshot (after)", progress=None)
    after = snapshot()
    repos = summary.get("repos", {})
    totals = {k: sum(r.get(k, 0) for r in repos.values())
              for k in ("added", "updated", "superseded", "chunks_added", "chunks_removed", "embed_failed")}
    return {"state": "failed" if error else "succeeded", "error": error, "before": before,
            "after": after, "repos": repos, "totals": totals,
            "decay_updates": summary.get("decay_updates")}


# ── Full rebuild into a new index ──────────────────────────────────────────

def _git(args: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _pull_all(run: Run) -> tuple[dict, str]:
    pulls: dict = {}
    for repo in MS_REPOS:
        path = CONFIG.source_dirs[repo]
        run.publish(phase=f"git pull {repo}")
        old = _git(["rev-parse", "HEAD"], path).stdout.strip()
        try:
            r = _git(["pull", "--ff-only"], path, timeout=CONFIG.git_pull_timeout)
            ok = r.returncode == 0
            msg = (r.stdout.strip() or r.stderr.strip()).splitlines()
            msg = msg[0] if msg else ""
        except subprocess.TimeoutExpired:
            ok, msg = False, f"timed out after {CONFIG.git_pull_timeout}s"
        new = _git(["rev-parse", "HEAD"], path).stdout.strip()
        pulls[repo] = {"ok": ok, "message": msg[:200], "old_commit": old, "new_commit": new}
        run.note(f"[{repo}] git pull {'ok' if ok else 'FAILED'}: {msg[:160]} ({old[:10]} -> {new[:10]})")
        if not ok:
            return pulls, f"git pull failed for {repo}: {msg[:160]}"
    return pulls, ""


def _source_checks(cfg: BrainConfig) -> dict:
    """Excluded folders absent (articles/foundry, articles/foundry-local under azure-ai),
    every foundry-classic vector tagged legacy, and how many crystallised chunks made it."""
    from brain.search.bm25_index import BM25Index
    from brain.store.vector_store import get_client
    bm25 = BM25Index.load(cfg.bm25_index_path)
    excluded_bm25 = sum(1 for fp, sr in zip(bm25.file_paths, bm25.source_repos)
                        if sr == "azure-ai" and cfg.is_excluded("azure-ai", fp))
    client, offset = get_client(cfg), None
    excluded_qdrant = legacy_tagged = legacy_untagged = crystallised = 0
    while True:
        recs, offset = client.scroll(cfg.qdrant_collection, limit=2000, offset=offset,
                                     with_payload=["source_repo", "file_path", "legacy"])
        for r in recs:
            sr, fp = r.payload.get("source_repo"), r.payload.get("file_path", "")
            if sr == "azure-ai" and cfg.is_excluded("azure-ai", fp):
                excluded_qdrant += 1
            if sr == "azure-ai" and cfg.is_legacy("azure-ai", fp):
                if r.payload.get("legacy") is True:
                    legacy_tagged += 1
                else:
                    legacy_untagged += 1
            crystallised += sr == "crystallised"
        if offset is None:
            break
    return {"excluded_under_azure_ai": {"bm25": excluded_bm25, "qdrant": excluded_qdrant},
            "legacy_foundry_classic": {"tagged": legacy_tagged, "untagged": legacy_untagged},
            "crystallised_vectors": crystallised}


def _samples(cfg: BrainConfig) -> dict[str, list[dict]]:
    from brain.search.bm25_index import BM25Index
    from brain.search.hybrid_search import hybrid_search
    bm25 = BM25Index.load(cfg.bm25_index_path)
    out: dict[str, list[dict]] = {}
    for q in SAMPLE_QUERIES:
        try:
            hits = hybrid_search(q, bm25, cfg, top_k=3, reinforce=False)
            out[q] = [{"title": h.chunk.title, "source": h.chunk.source_repo,
                       "file": os.path.relpath(h.chunk.file_path, cfg.wiki_root),
                       "score": round(h.fused_score, 5)} for h in hits]
        except Exception as exc:
            out[q] = [{"error": str(exc)[:160]}]
    return out


def run_rebuild(run: Run, dry_run: bool, force: bool) -> dict:
    ok, problems = rebuild_gate()
    if not ok and not force:
        err = "curated sources need review first: " + "; ".join(problems)
        run.note(err)
        return {"state": "failed", "error": err}

    serving = BrainConfig(wiki_root=CONFIG.wiki_root, qdrant_collection=CONFIG.qdrant_collection,
                          bm25_index_path=CONFIG.bm25_index_path)
    name = "azure_wiki_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M")
    target = BrainConfig(wiki_root=CONFIG.wiki_root, qdrant_collection=name, bm25_index_path=os.path.join(
        CONFIG.wiki_root, "brain", "store", f"bm25_{name}.pkl"))
    if target.qdrant_collection == serving.qdrant_collection:
        return {"state": "failed", "error": "target collection equals the serving collection"}
    run.status["target"] = {"collection": name, "bm25_path": target.bm25_index_path}
    run.note(f"rebuild target: collection {name}, BM25 {target.bm25_index_path}")
    run.note(f"serving index (untouched): {serving.qdrant_collection}, {serving.bm25_index_path}")

    if dry_run:
        return {"state": "succeeded", "error": "", "note": "dry run — gate passed, nothing built",
                "target": run.status["target"]}

    pulls, err = _pull_all(run)
    if err:
        return {"state": "failed", "error": err, "pulls": pulls}

    run.publish(phase="full ingest", progress=None)
    code = run.stream([sys.executable, "-u", "-m", "brain.ingest.pipeline", "--reset",
                       "--collection", name, "--bm25-path", target.bm25_index_path])
    if code != 0:
        return {"state": "failed", "error": f"full ingest exited with code {code}",
                "pulls": pulls, "target": run.status["target"]}

    run.publish(phase="verify + compare", progress=None)
    old_snap, new_snap = snapshot(serving), snapshot(target)
    missing_vectors = {src: v["chunks"] - (v["vectors"] or 0)
                       for src, v in new_snap["sources"].items() if v["chunks"] != v["vectors"]}
    src_checks = _source_checks(target)
    excl = src_checks["excluded_under_azure_ai"]
    report = {
        "serving": {"collection": serving.qdrant_collection, "bm25_path": serving.bm25_index_path,
                    "snapshot": old_snap},
        "candidate": {"collection": name, "bm25_path": target.bm25_index_path,
                      "manifest": target.manifest_path, "snapshot": new_snap},
        "checks": {
            "missing_vectors": missing_vectors,
            **src_checks,
            "passed": (not missing_vectors and excl["bm25"] == 0 and excl["qdrant"] == 0
                       and src_checks["legacy_foundry_classic"]["untagged"] == 0
                       and src_checks["legacy_foundry_classic"]["tagged"] > 0),
        },
        "samples": {"serving": _samples(serving), "candidate": _samples(target)},
        "pulls": pulls,
        "built_at": utcnow(),
        "run_id": run.run_id,
    }
    write_json(REBUILD_PATH, report)
    for line in (f"checks: {report['checks']}",
                 f"chunks {old_snap['total_chunks']} -> {new_snap['total_chunks']}, "
                 f"vectors {old_snap['total_vectors']} -> {new_snap['total_vectors']}"):
        run.note(line)
    return {"state": "succeeded", "error": "", "before": old_snap, "after": new_snap,
            "target": run.status["target"], "checks": report["checks"], "pulls": pulls,
            "note": "candidate ready — NOT switched; confirm to switch"}


def main() -> int:
    parser = argparse.ArgumentParser(description="TE-1 knowledge base updater")
    parser.add_argument("--rebuild", action="store_true",
                        help="pull all repos and build a new index (serving index untouched)")
    parser.add_argument("--force", action="store_true",
                        help="rebuild even if curated sources are stale (not used by the panel)")
    parser.add_argument("--dry-run", action="store_true",
                        help="skip pulls and ingest; exercise lock/status/history only")
    parser.add_argument("--hold", type=float, default=5.0,
                        help="incremental dry run only: seconds to hold the lock")
    args = parser.parse_args()

    lock = _acquire_lock()
    if lock is None:
        print("Knowledge base update already running — not starting a second one.", file=sys.stderr)
        return 3

    run = Run("rebuild" if args.rebuild else "incremental", args.dry_run)
    try:
        if args.rebuild:
            record = run_rebuild(run, args.dry_run, args.force)
        else:
            record = run_incremental_update(run, args.dry_run, args.hold)
    except Exception as exc:
        record = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
    run.finish(record)
    lock.close()  # releases the flock
    return 0 if record["state"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
