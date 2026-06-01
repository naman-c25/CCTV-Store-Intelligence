#!/usr/bin/env bash
# Process every store's clips into a single events file, then (optionally) feed
# them into the running API.
#
#   bash pipeline/run.sh <DATA_DIR> <OUT_EVENTS> [API_URL]
#
# Expected layout (dataset is NOT in the repo — place it yourself):
#   DATA_DIR/
#     store_layout.json
#     clips/STORE_BLR_002/CAM_ENTRY_01.mp4  (etc.)
#
# Each clip filename (without extension) is taken as the camera_id; each
# subfolder of clips/ is taken as the store_id.
set -euo pipefail

DATA_DIR="${1:-./data}"
OUT="${2:-./out/events.jsonl}"
API="${3:-}"
LAYOUT="${DATA_DIR}/store_layout.json"
CLIPS_ROOT="${DATA_DIR}/clips"
START="${CLIP_START:-2026-03-03T14:00:00+00:00}"

mkdir -p "$(dirname "$OUT")"
: > "$OUT"   # truncate

if [[ ! -d "$CLIPS_ROOT" ]]; then
  echo "No clips dir at $CLIPS_ROOT. Place the dataset there (see README)." >&2
  exit 1
fi

for store_dir in "$CLIPS_ROOT"/*/; do
  store_id="$(basename "$store_dir")"
  tmp="$(mktemp)"
  echo ">> detecting $store_id"
  python -m pipeline.detect \
    --clips "$store_dir" --store "$store_id" --layout "$LAYOUT" \
    --out "$tmp" --start "$START" ${API:+--api "$API"}
  cat "$tmp" >> "$OUT"
  rm -f "$tmp"
done

echo ">> all stores done -> $OUT"
echo ">> calibrate against ground truth (if present):"
if [[ -f "${DATA_DIR}/sample_events.jsonl" ]]; then
  python -m pipeline.calibrate --events "$OUT" --ground-truth "${DATA_DIR}/sample_events.jsonl"
else
  python -m pipeline.calibrate --events "$OUT"
fi
