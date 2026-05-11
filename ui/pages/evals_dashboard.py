import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st
from brain.db.database import (
    init_db,
    get_eval_dashboard_stats,
    get_recent_evaluations,
)

st.set_page_config(page_title="TE-1 Evaluation Dashboard", layout="wide")

# ── Auth gate ──────────────────────────────────────────────────────────────

if st.session_state.get("mode") != "authenticated":
    st.warning("Sign in to access the Evaluation Dashboard.")
    st.stop()

init_db()

st.title("📊 TE-1 Evaluation Dashboard")
st.markdown("---")

# ── Row 1: metric cards ────────────────────────────────────────────────────

stats = get_eval_dashboard_stats()

total   = stats["total_evals"]
pos     = stats["positive_count"]
neg     = stats["negative_count"]
skipped = stats["skip_count"]
avg_sc  = stats["avg_auto_score"]

pos_rate = pos / total if total > 0 else 0.0
neg_rate = neg / total if total > 0 else 0.0
skip_rate = stats["skip_rate"]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Evaluations",   total)
c2.metric("👍 Positive Rate",    f"{pos_rate:.0%}",   f"{pos} evals")
c3.metric("👎 Negative Rate",    f"{neg_rate:.0%}",   f"{neg} evals")
c4.metric("⏭ Skip Rate",        f"{skip_rate:.0%}",  f"{skipped} evals")
c5.metric("Avg Auto-Score",      f"{avg_sc:.2f}")

st.markdown("---")

# ── Row 2: ratings over time + flagged chunks ──────────────────────────────

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Ratings Over Time")
    recent = get_recent_evaluations(limit=200)
    if recent:
        from collections import defaultdict
        date_rating: dict[str, dict[str, int]] = defaultdict(lambda: {"positive": 0, "negative": 0, "skipped": 0})
        for row in recent:
            date = (row.get("created_at") or "")[:10]
            rating = row.get("rating") or "skipped"
            if date and rating in date_rating[date]:
                date_rating[date][rating] += 1
        if date_rating:
            sorted_dates = sorted(date_rating.keys())
            chart_data = {
                "positive": [date_rating[d]["positive"] for d in sorted_dates],
                "negative": [date_rating[d]["negative"] for d in sorted_dates],
                "skipped":  [date_rating[d]["skipped"]  for d in sorted_dates],
            }
            import pandas as pd
            df_chart = pd.DataFrame(chart_data, index=sorted_dates)
            st.bar_chart(df_chart)
        else:
            st.caption("No evaluation data yet.")
    else:
        st.caption("No evaluation data yet.")

with col_right:
    st.subheader("Top 10 Flagged Chunks")
    flagged = stats["top_flagged_chunks"]
    if flagged:
        import pandas as pd
        df_flagged = pd.DataFrame(flagged)
        df_flagged["quarantined"] = df_flagged["quarantined"].apply(
            lambda v: "🔴 Yes" if v else "No"
        )
        df_flagged.columns = ["Chunk ID", "Flag Count", "Quarantined"]
        st.dataframe(df_flagged, use_container_width=True, hide_index=True)
    else:
        st.caption("No flagged chunks yet.")

st.markdown("---")

# ── Row 3: recent evaluations ──────────────────────────────────────────────

st.subheader("Recent Evaluations")
recent20 = get_recent_evaluations(limit=20)
if recent20:
    import pandas as pd
    rows = []
    for r in recent20:
        rows.append({
            "Date":       (r.get("created_at") or "")[:16].replace("T", " "),
            "User":       r.get("username") or "Guest",
            "Rating":     r.get("rating") or "",
            "Category":   r.get("category") or "",
            "Auto-Score": f"{r['auto_score_overall']:.2f}" if r.get("auto_score_overall") else "—",
            "Feedback":   (r.get("feedback_text") or "")[:100],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.caption("No evaluations recorded yet.")

st.markdown("---")

# ── Row 4: quarantine alert ────────────────────────────────────────────────

quarantined = [c for c in stats["top_flagged_chunks"] if c.get("quarantined")]
if quarantined:
    chunk_ids = ", ".join(c["chunk_id"][:12] + "…" for c in quarantined)
    st.warning(
        f"⚠️ **{len(quarantined)} quarantined chunk(s)** require /wiki-lint review: "
        f"`{chunk_ids}`"
    )
