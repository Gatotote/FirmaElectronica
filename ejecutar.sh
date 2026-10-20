#!/bin/sh
# Lanza Firma Electrónica con el entorno del proyecto (lo usa el menú de clic derecho de Dolphin).
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec uv run --quiet python -m firmaelectronica "$@"
