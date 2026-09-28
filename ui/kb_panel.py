"""📚 Knowledge Base admin panel — rendered on the evals dashboard for admins only."""
from __future__ import annotations

import datetime
import os

import pandas as pd
import streamlit as st

from brain.config import CONFIG
from brain.kb import index_switch
from brain.kb import status as kb


@st.cache_data(ttl=120, show_spinner="Reading knowledge base state…")
def _cached_snapshot(bm25_path: str, bm25_mtime: float) -> dict:
    # Keyed on the BM25 file + time, so a finished update or index switch shows at once
    return kb.snapshot()


def _bm25_mtime() -> float:
    try:
        return os.path.getmtime(CONFIG.bm25_index_path)
    except OSError:
        return 0.0


def _fmt_ts(ts: str) -> str:
    if not ts:
        return "—"
    try:
        d = datetime.datetime.fromisoformat(ts)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - d).days
        return f"{d.strftime('%Y-%m-%d %H:%M')} UTC ({age}d ago)"
    except ValueError:
        return ts


def _fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


def _n(v) -> str:
    return "—" if v is None else f"{v:,}"


def _delta(a, b) -> str:
    if a is None or b is None:
        return "—"
    d = b - a
    return f"{d:+,}" if d else "0"


def render_kb_panel() -> None:
    st.header("📚 Knowledge Base")
    st.warning(
        "Updates re-embed changed documents with Ollama on the same CPU that generates "
        "architectures — generation and Brain search will be noticeably slower while an "
        "update runs. Prefer running updates outside working hours."
    )

    running = kb.is_update_running()
    _render_status(running)

    # ── Current state ──────────────────────────────────────────────────────
    st.caption(f"Serving index: `{CONFIG.qdrant_collection}` + "
               f"`{os.path.relpath(CONFIG.bm25_index_path, CONFIG.wiki_root)}`")
    snap = _cached_snapshot(CONFIG.bm25_index_path, _bm25_mtime())
    last_ts, last_src = kb.last_ingest()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total chunks", _n(snap["total_chunks"]))
    c2.metric("Embedded vectors", _n(snap["total_vectors"]))
    c3.metric("Documents", _n(snap["total_docs"]))
    c4.metric("Last ingest", _fmt_ts(last_ts)[:16])
    st.caption(f"Last ingest source: {last_src}. Chunks = BM25 index; vectors = Qdrant "
               "(chunks whose embedding failed are keyword-searchable only).")

    rows = [{"Source": r, "Documents": v["docs"], "Chunks": v["chunks"],
             "Vectors": v["vectors"]} for r, v in snap["sources"].items()]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Microsoft source repos")
    check = kb.last_check()
    repo_rows = []
    for repo, info in kb.repo_info().items():
        chk = (check.get("repos") or {}).get(repo, {})
        repo_rows.append({
            "Repo": repo,
            "Local commit": info.get("commit", "—"),
            "Commit date": (info.get("date") or "")[:10] or "—",
            "Subject": info.get("subject") or info.get("error", ""),
            "Commits behind": ("⚠️" if chk.get("error") else
                               f"≈{chk['behind']:,}" if chk.get("behind_approx") else
                               f"{chk['behind']:,}" if "behind" in chk else "—"),
            ".md changed upstream": chk.get("md_changed", "—") if not chk.get("error") else chk["error"],
        })
    st.dataframe(pd.DataFrame(repo_rows), use_container_width=True, hide_index=True)
    if check:
        st.caption(f"Last checked {_fmt_ts(check.get('checked_at', ''))} (git fetch only — "
                   "nothing was merged).")

    b1, b2, _ = st.columns([1, 1, 2])
    with b1:
        if st.button("🔍 Check for updates", use_container_width=True, disabled=running,
                     help="git fetch each repo (no merge) and count what changed upstream"):
            with st.spinner("Fetching from GitHub — large repos can take a few minutes…"):
                kb.check_updates()
            st.rerun()
    with b2:
        if st.button("⬇️ Update now", type="primary", use_container_width=True,
                     disabled=running,
                     help="git pull + incremental ingest in a background process"):
            if kb.start_update():
                st.toast("Update started in the background")
            else:
                st.toast("An update is already running")
            st.rerun()

    # ── Curated sources ────────────────────────────────────────────────────
    st.subheader("Curated sources (manual review)")
    st.caption("These are hand-maintained, not git-pulled — incremental ingest never refreshes "
               "them. Add `last_verified: YYYY-MM-DD` to a file's frontmatter after reviewing "
               "it; otherwise its last commit date is used.")
    for name, info in kb.curated_status().items():
        if not info.get("exists"):
            st.error(f"**{name}** (`{info['path']}`): {info['message']}.")
        elif info.get("stale"):
            st.warning(
                f"**{name}**: last verified {info.get('last_verified', '—')} "
                f"({info.get('age_days', '?')} days ago, {info.get('files', 0)} files) — older than "
                f"{kb.CURATED_MAX_AGE_DAYS} days. Review against current Microsoft documentation "
                f"and re-ingest."
            )
        else:
            st.success(f"**{name}**: last verified {info['last_verified']} "
                       f"({info['age_days']} days ago, {info['files']} files).")

    _render_rebuild(running)

    # ── Last run + history ─────────────────────────────────────────────────
    history = kb.read_history()
    if history:
        _render_last_run(history[0])
        st.subheader("Update history (last 10)")
        st.dataframe(pd.DataFrame([{
            "Started": _fmt_ts(h.get("started_at", ""))[:16],
            "Result": ("🧪 dry run " if h.get("dry_run") else "")
                      + ("✅" if h.get("state") == "succeeded" else "❌ " + (h.get("error") or "")[:60]),
            "Duration": _fmt_dur(h.get("duration_s")),
            "Added": h.get("totals", {}).get("added", 0),
            "Updated": h.get("totals", {}).get("updated", 0),
            "Superseded": h.get("totals", {}).get("superseded", 0),
            "Chunks Δ": _delta(h.get("before", {}).get("total_chunks"),
                               h.get("after", {}).get("total_chunks")),
        } for h in history]), use_container_width=True, hide_index=True)


def _render_status(running: bool) -> None:
    status = kb.read_status()
    if running:
        @st.fragment(run_every=5)
        def _live() -> None:
            s = kb.read_status()
            if not kb.is_update_running():
                st.rerun()  # finished — redraw the whole panel with the results
            prog = s.get("progress")
            what = "Full rebuild" if s.get("kind") == "rebuild" else "Update"
            st.info(f"⏳ {what} running since {_fmt_ts(s.get('started_at', ''))[:16]} — "
                    f"phase: **{s.get('phase', '…')}**")
            if prog and prog.get("total"):
                st.progress(prog["done"] / prog["total"],
                            text=f"{prog['done']:,} / {prog['total']:,} files · {prog.get('current', '')}")
            if s.get("log_tail"):
                st.code("\n".join(s["log_tail"][-12:]), language=None)
        _live()
    elif status.get("state") == "running":
        # Lock is free but the runner never wrote a final state: it was killed
        st.error(f"The update started {_fmt_ts(status.get('started_at', ''))[:16]} was "
                 "interrupted (process ended without finishing). Its partial changes are "
                 "already in the index; run Update now again to complete it — see "
                 "`data/kb_update/last_run.log`.")
    elif status.get("state") == "failed":
        st.error(f"Last update failed: {status.get('error', 'unknown error')} — see "
                 f"`data/kb_update/last_run.log`.")


def _render_last_run(h: dict) -> None:
    title = "Last update" + (" (dry run)" if h.get("dry_run") else "")
    st.subheader(title)
    t = h.get("totals", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files added", t.get("added", 0))
    c2.metric("Files updated", t.get("updated", 0))
    c3.metric("Files superseded", t.get("superseded", 0),
              help="Deleted or renamed upstream — their chunks were removed")
    c4.metric("Duration", _fmt_dur(h.get("duration_s")))
    if t.get("embed_failed"):
        st.caption(f"⚠️ {t['embed_failed']} new chunks could not be embedded (keyword search only).")

    before = h.get("before", {}).get("sources", {})
    after = h.get("after", {}).get("sources", {})
    rows = []
    for src in sorted(set(before) | set(after)):
        b, a = before.get(src, {}), after.get(src, {})
        rows.append({
            "Source": src,
            "Docs before": b.get("docs"), "Docs after": a.get("docs"),
            "Docs Δ": _delta(b.get("docs"), a.get("docs")),
            "Chunks before": b.get("chunks"), "Chunks after": a.get("chunks"),
            "Chunks Δ": _delta(b.get("chunks"), a.get("chunks")),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    for repo, r in (h.get("repos") or {}).items():
        if r.get("pull", "").startswith("FAILED") or "diff failed" in r.get("pull", ""):
            st.warning(f"{repo}: {r['pull']}")


def _render_rebuild(running: bool) -> None:
    st.subheader("Full rebuild (new index, switch after review)")
    st.caption("Pulls all four Microsoft repos, then builds a fresh Qdrant collection and BM25 "
               "file next to the serving one. TE-1 keeps serving the current index throughout; "
               "nothing switches until you confirm below. ~64k chunks at ~12 chunks/s is about "
               "1.5 h with Ollama idle, longer while people generate.")
    gate_ok, problems = kb.rebuild_gate()
    if not gate_ok:
        st.error("Rebuild blocked until curated sources are reviewed: " + "; ".join(problems)
                 + ". Update the files and set `last_verified` in their frontmatter.")
    if st.button("🏗️ Start full rebuild", disabled=running or not gate_ok,
                 help="Runs in the background; progress appears above"):
        st.toast("Rebuild started" if kb.start_update(["--rebuild"]) else "An update is already running")
        st.rerun()

    report = kb.read_rebuild()
    if not report:
        return
    cand, serv = report["candidate"], report["serving"]
    is_active = CONFIG.qdrant_collection == cand["collection"]
    st.markdown(f"**Candidate** `{cand['collection']}` built {_fmt_ts(report.get('built_at', ''))[:16]}"
                f" — compared against `{serv['collection']}`"
                + (" · ✅ **now serving**" if is_active else ""))

    old_src, new_src = serv["snapshot"]["sources"], cand["snapshot"]["sources"]
    rows = []
    for src in sorted(set(old_src) | set(new_src)):
        o, n = old_src.get(src, {}), new_src.get(src, {})
        rows.append({"Source": src,
                     "Docs old": o.get("docs"), "Docs new": n.get("docs"), "Docs Δ": _delta(o.get("docs"), n.get("docs")),
                     "Chunks old": o.get("chunks"), "Chunks new": n.get("chunks"), "Chunks Δ": _delta(o.get("chunks"), n.get("chunks")),
                     "Vectors old": o.get("vectors"), "Vectors new": n.get("vectors")})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    chk = report["checks"]
    ex = chk.get("excluded_under_azure_ai") or chk.get("foundry_chunks_under_azure_ai", {})
    lg = chk.get("legacy_foundry_classic", {})
    (st.success if chk["passed"] else st.error)(
        f"Checks {'passed' if chk['passed'] else 'FAILED'} — chunks without vectors: "
        f"{sum(chk['missing_vectors'].values()) if chk['missing_vectors'] else 0}; "
        f"excluded Foundry/Foundry Local chunks under azure-ai: BM25 {ex.get('bm25')}, "
        f"Qdrant {ex.get('qdrant')}; foundry-classic tagged legacy: {lg.get('tagged', '—')} "
        f"(untagged {lg.get('untagged', '—')}); crystallised vectors: "
        f"{chk.get('crystallised_vectors', '—')}")

    with st.expander("Sample searches — serving vs candidate"):
        for q, new_hits in report["samples"]["candidate"].items():
            st.markdown(f"**{q}**")
            c_old, c_new = st.columns(2)
            for col, label, hits in ((c_old, serv["collection"], report["samples"]["serving"].get(q, [])),
                                     (c_new, cand["collection"], new_hits)):
                with col:
                    st.caption(label)
                    for h in hits:
                        st.markdown(f"- {h.get('title') or h.get('error', '')} · `{h.get('source', '')}`")

    if not is_active:
        confirm = st.checkbox(f"I reviewed the comparison — serve TE-1 from `{cand['collection']}`",
                              disabled=running or not chk["passed"])
        if st.button("🔀 Switch to new index", type="primary", disabled=not confirm or running):
            ok, msg = index_switch.switch_to(cand["collection"], cand["bm25_path"])
            (st.toast if ok else st.error)(msg)
            if ok:
                st.cache_data.clear()
                st.rerun()
    else:
        prev = index_switch.previous()
        if prev and st.button(f"↩️ Switch back to `{prev['collection']}`", disabled=running):
            ok, msg = index_switch.switch_to(prev["collection"], prev["bm25_path"])
            (st.toast if ok else st.error)(msg)
            if ok:
                st.cache_data.clear()
                st.rerun()
