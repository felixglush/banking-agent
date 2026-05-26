#!/usr/bin/env bash
set -euo pipefail

# compass/ doesn't exist yet in Stage 1 — nothing to check.
if [ ! -d "compass" ]; then
    echo "compass/ not found — dependency-direction check skipped."
    exit 0
fi

if grep -rnE "^[[:space:]]*(from|import)[[:space:]]+(workflows|mcp_bank|synthetic_account_1)(\.|[[:space:]]|$)" compass/; then
    echo "compass/ must not import from project code"; exit 1
fi

echo "Dependency-direction check passed."
