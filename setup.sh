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

# The dashboard AI runs commands through Codex's bubblewrap sandbox. Ubuntu
# 24.04+ (and 23.10 with the option enabled) blocks unprivileged user
# namespaces via AppArmor, which fails every AI command with
# "bwrap: setting up uid map: Permission denied". Best effort: never fails
# the install, only prints what is left to do by hand.
_setup_ai_sandbox() {
  [ "$(uname -s)" = "Linux" ] || return 0
  command -v apt-get >/dev/null 2>&1 || return 0

  local sudo_cmd=""
  if [ "$(id -u)" != "0" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo_cmd="sudo"
    else
      echo "[setup] no root access; skipping AI sandbox (bwrap) setup" >&2
      return 0
    fi
  fi

  if ! command -v bwrap >/dev/null 2>&1; then
    echo "[setup] installing bubblewrap for the AI command sandbox"
    $sudo_cmd apt-get install -y bubblewrap \
      || { $sudo_cmd apt-get update && $sudo_cmd apt-get install -y bubblewrap; } \
      || {
        echo "[setup] bubblewrap install failed; AI chat commands will not run" >&2
        return 0
      }
  fi

  # already able to create a user namespace: nothing to do
  bwrap --ro-bind / / --unshare-user true 2>/dev/null && return 0

  local restricted bwrap_path
  restricted="$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)"
  if [ "$restricted" = "1" ]; then
    bwrap_path="$(command -v bwrap)"
    echo "[setup] allowing user namespaces for $bwrap_path via AppArmor profile"
    $sudo_cmd tee /etc/apparmor.d/bwrap >/dev/null <<EOF
abi <abi/4.0>,
include <tunables/global>

profile bwrap $bwrap_path flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
    if ! $sudo_cmd apparmor_parser -r /etc/apparmor.d/bwrap; then
      # e.g. AppArmor too old for abi/4.0 — fall back to the global switch
      echo "[setup] AppArmor profile failed to load; disabling the userns restriction globally" >&2
      $sudo_cmd rm -f /etc/apparmor.d/bwrap
      echo 'kernel.apparmor_restrict_unprivileged_userns = 0' \
        | $sudo_cmd tee /etc/sysctl.d/60-pynereal-userns.conf >/dev/null
      $sudo_cmd sysctl --system >/dev/null
    fi
  fi

  if bwrap --ro-bind / / --unshare-user true 2>/dev/null; then
    echo "[setup] AI sandbox (bwrap) is working"
  else
    echo "[setup] bwrap still cannot create user namespaces; AI chat commands may fail." >&2
    echo "[setup] If this host is a Docker/LXC container, allow user namespaces in the runtime" >&2
    echo "[setup] (Docker: --security-opt seccomp=unconfined --security-opt apparmor=unconfined," >&2
    echo "[setup]  LXD: security.nesting=true)." >&2
  fi
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

  _setup_ai_sandbox
}

_setup_main "$@"
_setup_status=$?
unset -f _find_python _setup_main _setup_ai_sandbox 2>/dev/null || true
return "$_setup_status" 2>/dev/null || exit "$_setup_status"
