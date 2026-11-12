#!/bin/sh
# Regenerates the lock files from their .in files: every package, including indirect ones, pinned
# with the hashes of its files. Resolved for the Linux / Python 3.12 runners the workflows use, from
# wheels only, choosing only files uploaded at least 14 days ago so a malicious release has time to
# be caught and pulled before it is locked in. The tools doing this come from scripts/lock-tools.txt,
# installed the same strict way. Run from the repo root with Docker running; extra arguments go to
# pip-compile (for example --upgrade, or --upgrade-package numpy).
set -eu

IMAGE="python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"

# The container script comes from the heredoc on stdin, so quotes in it need no escaping.
# CUSTOM_COMPILE_COMMAND sets the "to regenerate, run" line at the top of each lock file.
docker run --rm -i --platform linux/amd64 -v "$PWD":/src -w /src \
  -e CUSTOM_COMPILE_COMMAND="scripts/lock-requirements.sh" "$IMAGE" sh -s -- "$@" <<'SCRIPT'
set -eu
pip install --quiet --root-user-action=ignore --require-hashes --only-binary=:all: \
  -r scripts/lock-tools.txt
for name in scripts/lock-tools requirements requirements-search; do
  pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
    --pip-args "--uploaded-prior-to=P14D --only-binary=:all:" \
    --output-file "$name.txt" "$@" "$name.in"
done
SCRIPT
