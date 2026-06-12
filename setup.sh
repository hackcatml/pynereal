#!/usr/bin/env bash

_find_python() {
  local candidate minor

  {
    command -v python3 2>/dev/null || true
    command -v python 2>/dev/null || true

    minor=11
    while [ "$minor" -le 99 ]; do
      command -v "python3.$minor" 2>/dev/null || true
      minor=$((minor + 1))
    done
  } | while IFS= read -r candidate; do
      [ -n "$candidate" ] || continue
      [ -x "$candidate" ] || continue
      "$candidate" - <<'PY' 2>/dev/null || continue
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      "$candidate" - <<'PY'
import sys
version = sys.version_info
print(f"{version.major:03d}.{version.minor:03d}.{version.micro:03d}\t{sys.executable}")
PY
    done | LC_ALL=C sort -u | tail -n 1 | cut -f2-
}

_setup_main() {
  local python_bin

  python_bin="$(_find_python)"
  if [ -z "$python_bin" ]; then
    echo "Need Python 3.11+" >&2
    return 1
  fi

  "$python_bin" -m venv ./venv || return 1
  . venv/bin/activate || return 1

  python -m ensurepip --upgrade || return 1
  python -m pip install --upgrade setuptools || return 1

  python -m pip install -e ".[all]" || return 1
  python -m pip install python-dateutil dotenv flask pandas numpy 'uvicorn[standard]' fastapi tomlkit || return 1
}

_setup_main "$@"
_setup_status=$?
unset -f _find_python _setup_main 2>/dev/null || true
return "$_setup_status" 2>/dev/null || exit "$_setup_status"
