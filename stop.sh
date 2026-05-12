#!/usr/bin/env bash
set -euo pipefail

pkill -f "streamlit run ui/app.py" || true

docker stop te1-qdrant && docker rm te1-qdrant

echo "TE-1 stopped."
