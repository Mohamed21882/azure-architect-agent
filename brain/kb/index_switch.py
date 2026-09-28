"""Which index TE-1 serves from, and switching between indexes.

    python -m brain.kb.index_switch --show
    python -m brain.kb.index_switch --to azure_wiki_20261001_0200
    python -m brain.kb.index_switch --to azure_wiki          # the original index

The choice lives in data/active_index.json and is read by brain.config at import.
Switching also updates the in-process CONFIG so a running Streamlit app follows
immediately (load_bm25 is keyed on the BM25 path). Old indexes are never deleted.
"""
from __future__ import annotations

import argparse
import os
import sys

from brain.config import CONFIG
from brain.kb.status import _read_json, utcnow, write_json

ORIGINAL = {
    "collection": "azure_wiki",
    "bm25_path": os.path.join(CONFIG.wiki_root, "brain", "store", "bm25.pkl"),
}


def active() -> dict:
    return {"collection": CONFIG.qdrant_collection, "bm25_path": CONFIG.bm25_index_path}


def index_exists(collection: str, bm25_path: str) -> tuple[bool, str]:
    if not os.path.isfile(bm25_path):
        return False, f"BM25 file not found: {bm25_path}"
    try:
        from brain.store.vector_store import get_client
        if not get_client(CONFIG).collection_exists(collection):
            return False, f"Qdrant collection not found: {collection}"
    except Exception as exc:
        return False, f"Qdrant unreachable: {exc}"
    return True, ""


def switch_to(collection: str, bm25_path: str) -> tuple[bool, str]:
    """Make (collection, bm25_path) the serving index. Refused while an update runs."""
    from brain.kb.status import is_update_running
    if is_update_running():
        return False, "A knowledge base update or rebuild is running — switch after it finishes."
    ok, why = index_exists(collection, bm25_path)
    if not ok:
        return False, why
    current = active()
    if current["collection"] == collection and current["bm25_path"] == bm25_path:
        return False, f"'{collection}' is already the active index."
    write_json(CONFIG.active_index_path, {
        "collection": collection,
        "bm25_path": bm25_path,
        "switched_at": utcnow(),
        "previous": current,
    })
    CONFIG.qdrant_collection = collection
    CONFIG.bm25_index_path = bm25_path
    return True, f"TE-1 now serves from '{collection}' (previous: '{current['collection']}')."


def previous() -> dict | None:
    return _read_json(CONFIG.active_index_path, {}).get("previous")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show or switch TE-1's serving index")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--to", metavar="COLLECTION", help="collection name to serve from")
    parser.add_argument("--bm25-path", help="BM25 file (default: derived from the collection)")
    args = parser.parse_args()

    if args.show:
        print(f"active:   {active()}")
        print(f"previous: {previous()}")
        return 0
    path = args.bm25_path or (ORIGINAL["bm25_path"] if args.to == "azure_wiki" else
                              os.path.join(CONFIG.wiki_root, "brain", "store", f"bm25_{args.to}.pkl"))
    ok, msg = switch_to(args.to, path)
    print(msg, file=sys.stdout if ok else sys.stderr)
    if ok:
        print("Restart Streamlit (./stop.sh && ./start.sh) if it was started before this switch "
              "and you switched from the command line.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
