#!/usr/bin/env bash
set -euo pipefail

mkdir -p .git/hooks
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

python3 scripts/secret_scan.py --mode staged
HOOK
chmod +x .git/hooks/pre-commit

echo "Installed .git/hooks/pre-commit"
