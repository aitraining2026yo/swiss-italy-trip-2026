#!/bin/bash
# One-shot check: all official Last Supper ticket types for late Aug 2026
# Products: admission, EN guided, IT guided, workshop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
python3 "$ROOT/last_supper_watch.py" --once
