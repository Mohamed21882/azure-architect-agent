#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting Qdrant..."
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -v "${SCRIPT_DIR}/brain/store/qdrant:/qdrant/storage" \
  qdrant/qdrant

sleep 3

echo "Starting TE-1 Streamlit app..."
cd "${SCRIPT_DIR}"
venv/bin/streamlit run ui/app.py \
  --server.address 0.0.0.0 \
  --server.port 8501
