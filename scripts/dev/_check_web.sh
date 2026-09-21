#!/usr/bin/env bash
# 프런트 점검 한 벌 — tsc · vitest.   wsl.exe -e bash scripts/dev/_check_web.sh
cd /home/ericsj1998/projects/up_and_down_invest/web || exit 1
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node" 2>/dev/null | tail -1)/bin:$PATH"
echo "── tsc"
npx tsc --noEmit -p . 2>&1 | tail -20
echo "── vitest"
npx vitest run 2>&1 | tail -12
