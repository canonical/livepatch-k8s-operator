#!/bin/sh -e
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# shellcheck disable=SC1091
dir_root=$(dirname "$(readlink -f "$0")")

if [ -z "$VIRTUAL_ENV" ] && [ -d venv/ ]; then
    . "$dir_root/venv/bin/activate"
fi

if [ -z "$PYTHONPATH" ]; then
    export PYTHONPATH="lib:src"
else
    export PYTHONPATH="lib:src:$PYTHONPATH"
fi

flake8
coverage run --branch --source=src -m unittest -v "$@"
coverage report -m
