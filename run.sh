#!/bin/bash
# Run the Studio Assistant locally on KPrime (the deployed app runs on App Service; the azure tool starts it against the real Azure
# services with `run_local`). This local run uses KPrime's local model server and a pretend sign-in for browser checks.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r app/requirements.txt
AI_BASE_URL="${AI_BASE_URL:-http://127.0.0.1:8081/v1}" AI_MODEL="${AI_MODEL:-swift-1.5}" \
AI_MAX_TOKENS="${AI_MAX_TOKENS:-6000}" AI_TIMEOUT_SECONDS="${AI_TIMEOUT_SECONDS:-300}" \
LOCAL_SIGN_IN=1 HOST="${HOST:-127.0.0.1}" PORT="${PORT:-4000}" .venv/bin/python app/app.py
