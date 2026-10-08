#!/bin/sh
set -eu

runtime_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
python3 -B "$(dirname "$runtime_dir")/verify.py"
exec python3 -B "$runtime_dir/deploy.py" "$@"
