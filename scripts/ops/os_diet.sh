#!/usr/bin/env bash
# 서버 OS 다이어트 — 안 쓰는 서비스 끄기 · journald 상한 (2026-09-26 · T310 R2 · 사용자 승인). 여러 번 돌려도 같다.
#
#   bash scripts/ops/remote.sh scripts/ops/os_diet.sh
#
# 무엇을
#   multipathd   다중 경로 스토리지 데몬 — Lightsail 단일 디스크 VM 에는 할 일이 없다(약 26 MB).
#                `multipath -ll` 에 장치가 **없을 때만** 끈다.
#   journald     상한 50 MB(디스크) · 20 MB(런타임) — 지금 약 85 MB · 앱 로그는 따로 회전한다.
#   snapd        설치된 snap 이 기본(core* · snapd · bare)뿐일 때만 끈다 — 다른 snap(예: amazon-ssm-agent)이 있으면 그대로 둔다.
# 안 하는 것: 커널 · 도커 · 스왑 설정 · 재부팅.
set -uo pipefail
mem() { free -m | awk '/Mem:/{printf "사용 %s · 가용 %s MB", $3, $7} /Swap:/{printf " · 스왑 %s MB", $3}'; }
echo "=== 전: $(mem)"

echo "=== multipathd"
if systemctl is-active --quiet multipathd; then
  devs="$(sudo -n multipath -ll 2>/dev/null | grep -c . || true)"
  if [ "${devs:-0}" = "0" ]; then
    sudo -n systemctl disable --now multipathd.service multipathd.socket >/dev/null 2>&1
    echo "끔(다중 경로 장치 0)"
  else
    echo "그대로 — 다중 경로 장치 $devs 줄"
  fi
else
  echo "이미 꺼져 있음"
fi

echo "=== journald 상한"
sudo -n mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=20M\n' | sudo -n tee /etc/systemd/journald.conf.d/updown-size.conf >/dev/null
sudo -n systemctl restart systemd-journald
sudo -n journalctl --vacuum-size=50M >/dev/null 2>&1
journalctl --disk-usage 2>/dev/null

echo "=== snapd"
if command -v snap >/dev/null 2>&1 && systemctl is-active --quiet snapd; then
  others="$(snap list 2>/dev/null | awk 'NR>1 && $1 !~ /^(core[0-9]*|snapd|bare)$/ {print $1}' | tr '\n' ' ')"
  if [ -z "$others" ]; then
    sudo -n systemctl disable --now snapd.service snapd.socket >/dev/null 2>&1
    echo "끔(기본 snap 만 있음)"
  else
    echo "그대로 — 쓰는 snap: $others"
  fi
else
  echo "없음 또는 이미 꺼져 있음"
fi

sleep 3
echo "=== 뒤: $(mem)"
