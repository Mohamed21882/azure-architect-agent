"""Knowledge base state, update checks and update-runner control for the admin panel.

Everything here is read-only except start_update(), which spawns brain.kb.updater as a
detached subprocess — the Streamlit thread never runs git pull or ingest itself.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

from brain.config import CONFIG, BrainConfig

KB_DIR       = os.path.join(CONFIG.wiki_root, "data", "kb_update")
STATUS_PATH  = os.path.join(KB_DIR, "status.json")
HISTORY_PATH = os.path.join(KB_DIR, "history.json")
CHECK_PATH   = os.path.join(KB_DIR, "check.json")
LOCK_PATH    = os.path.join(KB_DIR, "update.lock")
LOG_PATH     = os.path.join(KB_DIR, "last_run.log")
MANIFEST_PATH = os.path.join(CONFIG.wiki_root, "data", "ingest_manifest.json")

MS_REPOS = ("architecture-center", "azure-ai", "azure-foundry", "cli")
CURATED = {
    "region-availability": os.path.join(CONFIG.wiki_root, "raw", "region-availability"),
    "microsoft-fabric":    os.path.join(CONFIG.wiki_root, "raw", "microsoft-fabric"),
}
CURATED_MAX_AGE_DAYS = 30
HISTORY_LIMIT = 10
FETCH_TIMEOUT = 900


# ── Small helpers ──────────────────────────────────────────────────────────

def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    """Atomic JSON write so the panel never reads a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


# ── Current state ──────────────────────────────────────────────────────────

def snapshot(config: BrainConfig = CONFIG, with_vectors: bool = True) -> dict:
    """Docs and chunks per source (BM25 index = every chunk) plus Qdrant vectors."""
    from brain.search.bm25_index import BM25Index

    docs: dict[str, set] = defaultdict(set)
    chunks: dict[str, int] = defaultdict(int)
    try:
        bm25 = BM25Index.load(config.bm25_index_path)
        for fp, repo in zip(bm25.file_paths, bm25.source_repos):
            docs[repo].add(fp)
            chunks[repo] += 1
    except Exception:
        pass

    sources = {r: {"docs": len(docs[r]), "chunks": chunks[r], "vectors": None} for r in docs}
    total_vectors = None
    if with_vectors:
        try:
            from brain.store.vector_store import count_by_source, get_client
            client = get_client(config)
            for repo, n in count_by_source(client, list(sources), config).items():
                sources[repo]["vectors"] = n
            total_vectors = client.count(config.qdrant_collection, exact=True).count
        except Exception:
            pass
    return {
        "taken_at": utcnow(),
        "sources": dict(sorted(sources.items())),
        "total_docs": sum(s["docs"] for s in sources.values()),
        "total_chunks": sum(s["chunks"] for s in sources.values()),
        "total_vectors": total_vectors,
    }


def last_ingest(config: BrainConfig = CONFIG) -> tuple[str, str]:
    """(timestamp, where it came from). Falls back to the BM25 file time when the
    manifest does not exist (it was never written before incremental ingest ran)."""
    manifest = _read_json(MANIFEST_PATH, {})
    if manifest.get("last_run"):
        return manifest["last_run"], "ingest manifest"
    try:
        ts = datetime.datetime.fromtimestamp(
            os.path.getmtime(config.bm25_index_path), datetime.timezone.utc
        )
        return ts.isoformat(timespec="seconds"), "BM25 index file time (no manifest yet)"
    except OSError:
        return "", "unknown"


def repo_info(config: BrainConfig = CONFIG) -> dict[str, dict]:
    """Local commit of each Microsoft source repo."""
    out: dict[str, dict] = {}
    for repo in MS_REPOS:
        path = config.source_dirs.get(repo, "")
        info = {"path": os.path.relpath(path, config.wiki_root) if path else "", "ok": False}
        try:
            r = _git(["log", "-1", "--format=%h%x1f%cI%x1f%s"], path)
            if r.returncode == 0 and r.stdout.strip():
                commit, date, subject = r.stdout.strip().split("\x1f", 2)
                info.update(ok=True, commit=commit, date=date, subject=subject[:90])
            else:
                info["error"] = (r.stderr.strip() or "not a git repo")[:120]
        except Exception as exc:
            info["error"] = str(exc)[:120]
        out[repo] = info
    return out


_FRONTMATTER_VERIFIED = re.compile(r"^last_verified:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def curated_status(config: BrainConfig = CONFIG) -> dict[str, dict]:
    """Last-verified date for curated (non-git-pulled) sources.

    Per file: a `last_verified: YYYY-MM-DD` frontmatter line wins; otherwise the file's
    last commit date in the TE-1 repo. The source's date is its OLDEST file."""
    now = datetime.datetime.now(datetime.timezone.utc)
    out: dict[str, dict] = {}
    for name, path in CURATED.items():
        info: dict = {"path": os.path.relpath(path, config.wiki_root), "exists": os.path.isdir(path)}
        if not info["exists"]:
            info.update(stale=True, message="Directory missing — this source is not in the knowledge base")
            out[name] = info
            continue
        dates: list[datetime.datetime] = []
        files = sorted(f for f in os.listdir(path) if f.endswith(".md"))
        for f in files:
            fp = os.path.join(path, f)
            d = None
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    m = _FRONTMATTER_VERIFIED.search(fh.read(2000))
                if m:
                    d = datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=datetime.timezone.utc)
            except OSError:
                pass
            if d is None:
                r = _git(["log", "-1", "--format=%cI", "--", f], path)
                if r.stdout.strip():
                    d = datetime.datetime.fromisoformat(r.stdout.strip())
            if d is None:
                d = datetime.datetime.fromtimestamp(os.path.getmtime(fp), datetime.timezone.utc)
            dates.append(d)
        info["files"] = len(files)
        if dates:
            oldest = min(dates)
            age = (now - oldest).days
            info.update(last_verified=oldest.date().isoformat(), age_days=age,
                        stale=age > CURATED_MAX_AGE_DAYS)
        else:
            info.update(stale=True, message="No .md files found")
        out[name] = info
    return out


# ── Check for updates (git fetch only — never merges) ─────────────────────

def _check_repo(repo: str, path: str) -> dict:
    res: dict = {"repo": repo}
    try:
        f = _git(["fetch", "--quiet"], path, timeout=FETCH_TIMEOUT)
        if f.returncode != 0:
            res["error"] = f"fetch failed: {f.stderr.strip()[:160]}"
            return res
        upstream = _git(["rev-parse", "--abbrev-ref", "@{upstream}"], path).stdout.strip()
        if not upstream:
            res["error"] = "no upstream branch configured"
            return res
        res["upstream"] = upstream
        count_args = ["rev-list", "--count", "HEAD..@{upstream}"]
        shallow = _git(["rev-parse", "--is-shallow-repository"], path).stdout.strip() == "true"
        if shallow:
            # In a shallow clone, fetch pulls in old side-branch history that HEAD cannot
            # reach, so a plain count is wildly inflated (azure-ai: 168k vs ~7k). Count
            # only commits newer than HEAD instead.
            since = _git(["log", "-1", "--format=%cI", "HEAD"], path).stdout.strip()
            count_args.insert(2, f"--since={since}")
        behind = _git(count_args, path, timeout=120)
        res["behind"] = int(behind.stdout.strip() or 0)
        res["behind_approx"] = shallow
        # --relative scopes to the ingested folder (azure-foundry → articles/foundry)
        d = _git(["diff", "--name-only", "--relative", "HEAD", "@{upstream}"], path, timeout=120)
        res["md_changed"] = sum(1 for line in d.stdout.splitlines() if line.endswith(".md"))
        res["upstream_commit"] = _git(["log", "-1", "--format=%h %cI", "@{upstream}"], path).stdout.strip()
    except subprocess.TimeoutExpired:
        res["error"] = f"timed out after {FETCH_TIMEOUT}s"
    except Exception as exc:
        res["error"] = str(exc)[:160]
    return res


def check_updates(config: BrainConfig = CONFIG) -> dict:
    """git fetch every Microsoft repo in parallel and compare HEAD with upstream."""
    started = utcnow()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(MS_REPOS)) as ex:
        futs = {r: ex.submit(_check_repo, r, config.source_dirs[r]) for r in MS_REPOS}
        repos = {r: f.result() for r, f in futs.items()}
    result = {"started_at": started, "checked_at": utcnow(), "repos": repos}
    write_json(CHECK_PATH, result)
    return result


def last_check() -> dict:
    return _read_json(CHECK_PATH, {})


# ── Update runner control ──────────────────────────────────────────────────

def is_update_running() -> bool:
    """True while brain.kb.updater holds the flock. The kernel drops the lock if the
    runner dies, so a crash can never leave a stale lock behind."""
    os.makedirs(KB_DIR, exist_ok=True)
    with open(LOCK_PATH, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False


def start_update(extra_args: list[str] | None = None) -> bool:
    """Spawn the updater detached from Streamlit. False if one is already running.
    (The runner re-checks the lock itself, so a race here cannot start two runs.)"""
    if is_update_running():
        return False
    env = dict(os.environ, PYTHONPATH=CONFIG.wiki_root, PYTHONUNBUFFERED="1")
    subprocess.Popen(
        [sys.executable, "-m", "brain.kb.updater", *(extra_args or [])],
        cwd=CONFIG.wiki_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # survives Streamlit reruns and restarts
    )
    return True


def read_status() -> dict:
    return _read_json(STATUS_PATH, {})


def read_history() -> list[dict]:
    return _read_json(HISTORY_PATH, [])
