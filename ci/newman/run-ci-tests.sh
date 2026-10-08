#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
state_root="$(cd "${script_dir}/../.." && pwd -P)"
module_root="$(pwd -P)"
case "$module_root/" in
  "$state_root/"*) ;;
  *) echo "Newman must run from a module inside $state_root" >&2; exit 2 ;;
esac
test -f "$module_root/postman/collection.json" || {
  echo "Newman collection missing in current module: $module_root" >&2; exit 2;
}

export PYTHONDONTWRITEBYTECODE=1
python3 -B "$(dirname "${script_dir}")/verify.py"
cd "$module_root"
exec python3 -B "${script_dir}/ws-ci" --project-root "$module_root" \
  --state-root "$state_root" "$@"
