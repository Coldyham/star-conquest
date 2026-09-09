#!/bin/sh
# Build severance. No dependencies, so this works on a fresh machine with no
# registry and no network -- which is the point: the manifest names a compiled
# binary, and `docs/bot-api.md` leaves "who builds the bot" open.
set -e
cd "$(dirname "$0")"
if command -v cargo >/dev/null 2>&1; then
    exec cargo build --release --offline
fi
# No cargo: rustc alone is enough, since nothing outside src/ is needed.
mkdir -p target/release
exec rustc -O --edition 2021 -o target/release/severance src/main.rs
