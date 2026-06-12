#!/usr/bin/env bash
set -euo pipefail

find_python() {
  local dir path

  IFS=':' read -ra path_dirs <<< "$PATH"
  for dir in "${path_dirs[@]}"; do
    [[ -d "$dir" ]] || continue

    for path in "$dir"/python3 "$dir"/python3.[0-9]*; do
      [[ -x "$path" && ! -d "$path" ]] || continue

      "$path" - <<'PY' 2>/dev/null || continue
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

      "$path" - <<'PY'
import sys
version = sys.version_info
print(f"{version.major:03d}.{version.minor:03d}.{version.micro:03d}\t{sys.executable}")
PY
    done
  done | LC_ALL=C sort -u | LC_ALL=C sort | tail -n 1 | cut -f2-
}

PYTHON_BIN="$(find_python)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Need Python 3.11+" >&2
  exit 1
fi

"$PYTHON_BIN" -m venv ./venv
source venv/bin/activate

python -m ensurepip --upgrade
python -m pip install --upgrade setuptools

python -m pip install -e ".[all]"
python -m pip install python-dateutil dotenv flask pandas numpy 'uvicorn[standard]' fastapi tomlkit
