#!/bin/zsh
# Compatibility entry point for the original desktop setup instructions.
set -euo pipefail
cd "${0:A:h:h}"
npm run desktop:build
npm run desktop:install
