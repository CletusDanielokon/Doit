#!/usr/bin/env bash
# Exit on error
set -o errexit

pip install -r requirements.txt

export FLASK_APP=server:app

# Apply committed migrations (never auto-generate migrations during a deploy).
flask db upgrade
