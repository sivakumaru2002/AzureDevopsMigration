#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env"
SHOW_HELP="false"
CHECK_ONLY="false"
MIGRATION_ARGS=()

while (($# > 0)); do
    case "$1" in
        --help|-h)
            SHOW_HELP="true"
            MIGRATION_ARGS+=("$1")
            shift
            ;;
        --check)
            CHECK_ONLY="true"
            shift
            ;;
        --env-file)
            if (($# < 2)); then
                echo "Error: --env-file requires a value." >&2
                exit 1
            fi
            ENV_FILE="$2"
            MIGRATION_ARGS+=("$1" "$2")
            shift 2
            ;;
        --env-file=*)
            ENV_FILE="${1#*=}"
            MIGRATION_ARGS+=("$1")
            shift
            ;;
        *)
            MIGRATION_ARGS+=("$1")
            shift
            ;;
    esac
done

usage() {
    cat <<'EOF'
Usage:
  ./run_all_migrations.sh
  ./run_all_migrations.sh --check

Runs repository migration first, then DevOpsMigration execute.
Use --check to validate both entrypoints without starting a migration.
EOF
}

if [[ "$SHOW_HELP" == "true" ]]; then
    usage
    exit 0
fi

if ! command -v python >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python was not found in PATH." >&2
    exit 1
fi

if command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

if ! command -v devopsmigration >/dev/null 2>&1; then
    echo "Error: devopsmigration was not found in PATH." >&2
    exit 1
fi

TEMP_CONFIG="$(mktemp "${TMPDIR:-/tmp}/devopsmigration-config.XXXXXX.json")"
cleanup() {
    rm -f "$TEMP_CONFIG"
}
trap cleanup EXIT

if [[ "$CHECK_ONLY" == "true" ]]; then
    echo "Validating repository migration entrypoint..."
    "$PYTHON_CMD" ./migrate_azure_devops_repos.py --help

    echo
    echo "Rendering DevOpsMigration configuration from .env..."
    "$PYTHON_CMD" ./render_devopsmigration_config.py --env-file "$ENV_FILE" --output "$TEMP_CONFIG"

    echo
    echo "Validating DevOpsMigration entrypoint..."
    devopsmigration execute --config "$TEMP_CONFIG" --help
    exit $?
fi

echo "Running Azure DevOps repository migration..."
"$PYTHON_CMD" ./migrate_azure_devops_repos.py "${MIGRATION_ARGS[@]}"

echo
echo "Rendering DevOpsMigration configuration from .env..."
"$PYTHON_CMD" ./render_devopsmigration_config.py --env-file "$ENV_FILE" --output "$TEMP_CONFIG"

echo
echo "Running DevOpsMigration execute..."
devopsmigration execute --config "$TEMP_CONFIG"

echo
echo "Both migrations completed successfully."