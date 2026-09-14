#!/usr/bin/env sh
# Lanceur de développement (macOS / Linux) : démarre l'application depuis les sources.
# Utilise le venv du dépôt s'il existe, sinon python3.   Usage :  sh start.sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" app.py
