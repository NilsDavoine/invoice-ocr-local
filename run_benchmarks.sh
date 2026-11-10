#!/usr/bin/env bash
# Every measured number in results.md comes from this script.
#   ./run_benchmarks.sh            light engines only (CPU, a few minutes), tuning + held-out sets
#   ./run_benchmarks.sh --vlm      + Xiaomi-OCR-0 on GPU (40 + 30 pages) and on CPU (3 pages, 40 min at most)
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
LOCK=${MODEL_LOCK:-/tmp/invoice-ocr-model.lock}   # one heavy model at a time on a shared machine
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}
[ -f data/truth.json ] || $PY -m invoice_ocr generate
[ -f data_holdout/truth.json ] || $PY -m invoice_ocr generate --holdout
for set in data data_holdout; do
  $PY -m invoice_ocr evaluate --engine textlayer --data $set
  flock "$LOCK" $PY -m invoice_ocr evaluate --engine rapidocr --data $set
done
if [ "${1:-}" = "--vlm" ]; then
  for set in data data_holdout; do
    flock "$LOCK" $PY -m invoice_ocr evaluate --engine xiaomi --device cuda --data $set
  done
  # CPU: results are written after each invoice, so a run stopped at 40 minutes keeps its pages
  flock "$LOCK" timeout 2400 $PY -m invoice_ocr evaluate --engine xiaomi --device cpu --limit 3 \
    || [ $? -eq 124 ]
fi
$PY -m invoice_ocr stress
$PY -m tools.mutate
$PY -m invoice_ocr report
