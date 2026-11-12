#!/bin/sh
# Installs each lock file into a fresh virtual environment the way the workflows do (hashes
# required, wheels only) and runs that workflow's tests, inside the same Linux / Python 3.12 image
# the lock files are made for. The repo is mounted read-only.
set -eu

IMAGE="python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"

# The container script comes from the heredoc on stdin, so quotes in it need no escaping.
docker run --rm -i --platform linux/amd64 -v "$PWD":/src:ro -w /src \
  -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" sh -s <<'SCRIPT'
set -eu
# Proves where the tests ran: this must say Linux x86_64 and Python 3.12.
echo "check environment: $(uname -sm), $(python --version)"
python -m venv /tmp/weather
/tmp/weather/bin/pip install --quiet --require-hashes --only-binary=:all: -r requirements.txt
/tmp/weather/bin/python -m pytest -q -p no:cacheprovider
# osmium links against the system expat library: GitHub's Ubuntu runners have it, this slim
# image does not.
apt-get update -qq
apt-get install -y -qq --no-install-recommends libexpat1 > /dev/null
python -m venv /tmp/search
/tmp/search/bin/pip install --quiet --require-hashes --only-binary=:all: -r requirements-search.txt
/tmp/search/bin/python -m pytest -q -p no:cacheprovider tests/search
SCRIPT
