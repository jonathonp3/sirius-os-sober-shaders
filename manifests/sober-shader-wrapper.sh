#!/bin/sh
export PYTHONPATH=/app/lib/sober-shader
export SOBER_SHADER_HOST_FLATPAK=1
exec python3 -m cli "$@"
