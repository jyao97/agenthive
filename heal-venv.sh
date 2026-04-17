#!/usr/bin/env bash
# Repair .venv when the project directory has been moved/renamed.
# - Rewrites .venv/bin/* shebangs that point to the wrong python
# - Rewrites VIRTUAL_ENV in activate scripts
# - Updates pyvenv.cfg's `command =` line
# Portable: works with GNU sed (Linux) and BSD sed (macOS).
# Idempotent and fast — safe to run on every startup.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
[ -d "$VENV" ] || { echo "heal-venv: no $VENV — skipping"; exit 0; }

PY="$VENV/bin/python3"
[ -x "$PY" ] || { echo "heal-venv: $PY missing or not executable" >&2; exit 1; }

# sed -i portability: GNU accepts `-i`, BSD/macOS requires `-i ''`
if sed --version >/dev/null 2>&1; then
  sedi() { sed -i "$@"; }
else
  sedi() { sed -i '' "$@"; }
fi

fixed=0
for f in "$VENV"/bin/*; do
  [ -f "$f" ] || continue
  IFS= read -r first <"$f" 2>/dev/null || continue
  case "$first" in '#!'*) ;; *) continue ;; esac
  cur=${first#\#!}
  cur=${cur%% *}
  case "$cur" in /*python*) ;; *) continue ;; esac
  case "$cur" in "$PY"|"$VENV/bin/python"|"$VENV/bin/python3") continue ;; esac
  sedi "1s|^#!.*|#!$PY|" "$f"
  fixed=$((fixed + 1))
done

for f in "$VENV/bin/activate" "$VENV/bin/activate.csh" "$VENV/bin/activate.fish"; do
  [ -f "$f" ] || continue
  sedi \
    -e "s|VIRTUAL_ENV=\"[^\"]*\"|VIRTUAL_ENV=\"$VENV\"|g" \
    -e "s|VIRTUAL_ENV='[^']*'|VIRTUAL_ENV='$VENV'|g" \
    -e "s|setenv VIRTUAL_ENV \"[^\"]*\"|setenv VIRTUAL_ENV \"$VENV\"|g" \
    -e "s|set -gx VIRTUAL_ENV \"[^\"]*\"|set -gx VIRTUAL_ENV \"$VENV\"|g" \
    "$f"
done

CFG="$VENV/pyvenv.cfg"
if [ -f "$CFG" ] && grep -q "^command = " "$CFG"; then
  sedi "s|^\\(command = .*\\) -m venv .*|\\1 -m venv $VENV|" "$CFG"
fi

echo "heal-venv: $fixed shebang(s) rewritten in $VENV/bin"
