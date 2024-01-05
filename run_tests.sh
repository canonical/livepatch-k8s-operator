#!/bin/sh -e
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

if [ -z "$VIRTUAL_ENV" ] && [ -d venv/ ]; then
    . venv/bin/activate
fi

if [ -z "$PYTHONPATH" ]; then
    export PYTHONPATH="lib:src"
else
    export PYTHONPATH="lib:src:$PYTHONPATH"
fi

flake8
coverage run --branch --source=src -m unittest -v "$@"
coverage report -m
