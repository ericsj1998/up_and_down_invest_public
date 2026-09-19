#!/usr/bin/env bash
# 서버가 **실제로 읽는** 플레이북 파일 전부와, 각 파일의 키가 선언 스키마 안에 있는지 (T286 배포 전).
# `load_playbooks()` 는 엔트리포인트 패키지 + UPDOWN_PLAYBOOK_FILES + config/playbooks.yml 을 병합한다.
# 값은 안 찍는다 — 파일 경로와 키 이름뿐이다.
set -uo pipefail
for API in $(docker ps --filter "name=api" --filter "status=running" --format "{{.Names}}"); do
echo "=== $API ==="
docker exec -i "$API" python - <<'PY'
from dataclasses import fields
import yaml
from updown.analysis import plugins
from updown.analysis.playbook.types import Playbook

known = {f.name for f in fields(Playbook)} - {"playbook_id"}
paths = list(plugins.playbook_files())
print("읽는 파일 수:", len(paths))
bad = 0
for p in paths:
    try:
        parsed = yaml.safe_load(open(p, encoding="utf-8").read()) or {}
    except Exception as exc:  # noqa: BLE001
        print(" !!", p, "읽기 실패:", type(exc).__name__)
        bad += 1
        continue
    books = (parsed or {}).get("playbooks") or {}
    unknown = sorted({k for b in books.values() if isinstance(b, dict) for k in b} - known)
    print(f"  {p} · 선언 {len(books)}개 · 모르는 키 {unknown if unknown else '없음'}")
    bad += len(unknown)
print("UNKNOWN_TOTAL=", bad)
PY
done
