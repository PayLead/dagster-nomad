#!/usr/bin/env bash

cd /app
mapfile -t < $1 ; PYTHONPATH=/app exec "${MAPFILE[@]}"
