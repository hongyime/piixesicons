#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
exec "${PYTHON:-python3}" -m pip install -r requirements.txt "$@"
