#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/release.sh <patch|minor|major|X.Y.Z> [--no-push] [--dry-run]

Bumps the version across pyproject.toml, src/genealogy/__init__.py,
plugin/plugin.json, and uv.lock, runs the verification suite, commits
the release, and creates an annotated git tag vX.Y.Z.
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

BUMP=""
NO_PUSH=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --no-push)
      NO_PUSH=true
      ;;
    --dry-run)
      DRY_RUN=true
      ;;
    patch|minor|major)
      if [[ -n "$BUMP" ]]; then
        echo "Error: multiple version arguments provided." >&2
        exit 1
      fi
      BUMP="$arg"
      ;;
    *)
      if [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ -z "$BUMP" ]]; then
        BUMP="$arg"
      else
        echo "Error: unknown argument '$arg'" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$BUMP" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$DRY_RUN" == false ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree is not clean. Commit or stash changes first." >&2
    exit 1
  fi
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$BRANCH" != "main" ]]; then
    echo "Error: releases must be cut from 'main' (currently on '$BRANCH')." >&2
    exit 1
  fi
fi

CURRENT_VERSION="$(python3 - <<'PY'
import tomllib
from pathlib import Path
data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
)"

NEW_VERSION="$(python3 - "$CURRENT_VERSION" "$BUMP" <<'PY'
import re
import sys

cur, bump = sys.argv[1], sys.argv[2]
m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", cur)
if not m:
    raise SystemExit(f"Invalid current version: {cur}")
major, minor, patch = map(int, m.groups())

if bump == "patch":
    print(f"{major}.{minor}.{patch + 1}")
elif bump == "minor":
    print(f"{major}.{minor + 1}.0")
elif bump == "major":
    print(f"{major + 1}.0.0")
else:
    print(bump)
PY
)"

TAG="v${NEW_VERSION}"
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "Error: tag '${TAG}' already exists." >&2
  exit 1
fi

echo "Release plan: ${CURRENT_VERSION} -> ${NEW_VERSION} (tag ${TAG})"

run_checks() {
  if command -v uv >/dev/null 2>&1; then
    uv run ruff check src tests plugin/hooks
    uv run pytest -v
    uv run genealogy validate --root examples/west-family --strict
  else
    .venv/bin/ruff check src tests plugin/hooks
    .venv/bin/pytest -v
    .venv/bin/genealogy validate --root examples/west-family --strict
  fi
  if command -v agy >/dev/null 2>&1; then
    agy plugin validate plugin --strict
  fi
}

echo "Running pre-release verification..."
run_checks

if [[ "$DRY_RUN" == true ]]; then
  echo "[dry-run] Checks passed. Would bump ${CURRENT_VERSION} -> ${NEW_VERSION}, commit, and tag ${TAG}."
  exit 0
fi

python3 - "$CURRENT_VERSION" "$NEW_VERSION" <<'PY'
import json
import re
import sys
from pathlib import Path

old_ver, new_ver = sys.argv[1], sys.argv[2]

pyproject = Path("pyproject.toml")
pyproject_text = pyproject.read_text(encoding="utf-8")
updated_pyproject, count = re.subn(
    r'^(version\s*=\s*)"' + re.escape(old_ver) + r'"',
    rf'\1"{new_ver}"',
    pyproject_text,
    count=1,
    flags=re.MULTILINE,
)
assert count == 1, "Failed to update version in pyproject.toml"
pyproject.write_text(updated_pyproject, encoding="utf-8")

init_py = Path("src/genealogy/__init__.py")
init_text = init_py.read_text(encoding="utf-8")
updated_init, count = re.subn(
    r'^(__version__\s*=\s*)"' + re.escape(old_ver) + r'"',
    rf'\1"{new_ver}"',
    init_text,
    count=1,
    flags=re.MULTILINE,
)
assert count == 1, "Failed to update __version__ in src/genealogy/__init__.py"
init_py.write_text(updated_init, encoding="utf-8")

plugin_json = Path("plugin/plugin.json")
plugin_data = json.loads(plugin_json.read_text(encoding="utf-8"))
plugin_data["version"] = new_ver
plugin_json.write_text(json.dumps(plugin_data, indent=2) + "\n", encoding="utf-8")
PY

if command -v uv >/dev/null 2>&1; then
  uv lock
else
  python3 - "$CURRENT_VERSION" "$NEW_VERSION" <<'PY'
import re
import sys
from pathlib import Path

old_ver, new_ver = sys.argv[1], sys.argv[2]
lock_path = Path("uv.lock")
if lock_path.exists():
    text = lock_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(\[\[package\]\]\nname = "genealogy-workspace"\nversion = )"' + re.escape(old_ver) + r'"',
        rf'\1"{new_ver}"',
        text,
        count=1,
    )
    assert count == 1, "Failed to update genealogy-workspace version in uv.lock"
    lock_path.write_text(updated, encoding="utf-8")
PY
fi

# Re-run tests after bumping so the version-sync test verifies the new version
if command -v uv >/dev/null 2>&1; then
  uv run pytest -q
else
  .venv/bin/pytest -q
fi

git add pyproject.toml src/genealogy/__init__.py plugin/plugin.json uv.lock
git commit -m "chore: release ${TAG}"
git tag -a "${TAG}" -m "Release ${TAG}"

if [[ "$NO_PUSH" == true ]]; then
  echo "Created commit and tag ${TAG} locally (--no-push)."
  echo "Push with: git push origin main --follow-tags"
else
  git push origin main --follow-tags
  echo "Released ${TAG} and pushed to origin."
fi
