#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$project_dir"
for script in run.sh scripts/*.sh; do
  bash -n "$script"
done
python3 - <<'PY'
import ast
from pathlib import Path
for root in [Path('.'), Path('scripts'), Path('tests')]:
    for path in root.glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
print('Python and Bash syntax: PASS')
PY
python3 -m unittest discover -s tests -p 'test_*.py' -v
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo 'Node.js is not available; run node --check static/app.js on the development machine or in the image.'
fi
