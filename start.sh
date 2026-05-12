#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# --- Qdrant ---
if docker ps --filter "name=te1-qdrant" --filter "status=running" --format '{{.Names}}' | grep -q '^te1-qdrant$'; then
    echo "Qdrant already running"
else
    docker run -d --name te1-qdrant \
        -p 6333:6333 \
        -v ~/Azure-Architect-Wiki/data/qdrant:/qdrant/storage \
        qdrant/qdrant
fi

# Wait for Qdrant health
echo "Waiting for Qdrant..."
for i in $(seq 1 15); do
    if curl -sf http://localhost:6333/healthz > /dev/null 2>&1; then
        echo "Qdrant ready"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "ERROR: Qdrant did not become ready within 15 seconds" >&2
        exit 1
    fi
    sleep 1
done

# --- Streamlit ---
LAN_IP=$(hostname -I | awk '{print $1}')
echo "TE-1 starting... Access from your network at: http://${LAN_IP}:8501"

PYTHONPATH=. venv/bin/streamlit run ui/app.py \
    --server.address 0.0.0.0 \
    --server.port 8501
