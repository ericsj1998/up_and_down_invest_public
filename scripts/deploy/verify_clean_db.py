"""배포 DB 검사기 — 테스트 데이터가 없는지, 참조 표는 채워졌는지 (T216 · 2026-09-04).

> 사용자: *"배포에 맞춰 DB 정리 (현재 테스트 데이터는 배포본에 안올라가게)."*

원칙: **개발 볼륨을 배포로 복사하지 않는다.** 배포 DB 는 빈 볼륨에서 시작한다 —
마이그레이션 → 시드 → 백필. 이 스크립트는 그 각 단계 뒤에 돌려 "지금 상태가 배포 시작
상태인가" 를 판정한다. 어기면 exit 1 이고, 배포 절차(`init_db.sh`)는 거기서 멈춘다.

```
--stage migrated   비어야 할 표가 전부 0 행  (마이그레이션 직후)
--stage seeded     + instruments > 0        (시드 직후)
--stage ready      + candles > 0            (백필 직후 · 기본)
```

파일 쪽도 본다: `logs/funds` `logs/walkforward` `logs/reconcile` `logs/labels` 에 파일이 있으면
그건 개발 산출물이 볼륨에 딸려 온 것이다 (`--logs-root` · 기본 `UPDOWN_LOGS_DIR` 또는 `logs`).

실행 (컨테이너 안 · 이미지에 들어 있다):
    docker compose ... run --rm --no-deps api \
        python scripts/deploy/verify_clean_db.py --stage migrated
호스트:
    set -a; . ./.env.live; set +a; uv run python scripts/deploy/verify_clean_db.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa

from updown.common.config import load_settings
from updown.common.db.session import create_engine

MUST_BE_EMPTY: tuple[str, ...] = (
    # 운영 상태 — 판·매매·주문·보정
    "wf_runs",
    "wf_trades",
    "wf_orders",
    "wf_calibration",
    "backtest_runs",
    # 라이브 원장
    "orders",
    "positions",
    "accounts",
    "account_balances",
    "portfolio_snapshots",
    "allocation_ledger",
    "approved_orders",
    "trade_proposals",
    "risk_plan_revisions",
    # 감사 · 알림 · 구조물
    "event_logs",
    "notifications",
    "structures",
    "transitions",
)
"""배포 시작 시 **0 행**이어야 하는 표. 하나라도 있으면 개발 데이터가 딸려 온 것이다."""

PEOPLE: tuple[str, ...] = ("users", "broker_credentials", "app_settings", "risk_policies")
"""사람이 넣는 표 — 세기만 한다 (첫 로그인·관리자 입력으로 채워진다)."""

ARTIFACT_DIRS: tuple[str, ...] = ("funds", "walkforward", "reconcile", "labels")

STAGES = ("migrated", "seeded", "ready")


async def _counts(url: str, names: tuple[str, ...]) -> dict[str, int | None]:
    """표별 행 수. 없는 표는 None — 이름을 잘못 적었는지 스키마가 바뀌었는지 사람이 본다."""
    engine = create_engine(url)
    out: dict[str, int | None] = {}
    try:
        async with engine.connect() as conn:
            existing = set(
                (
                    await conn.execute(
                        sa.text("select tablename from pg_tables where schemaname = 'public'")
                    )
                )
                .scalars()
                .all()
            )
            for name in names:
                if name not in existing:
                    out[name] = None
                    continue
                out[name] = int(
                    (await conn.execute(sa.text(f'select count(*) from "{name}"'))).scalar_one()
                )
    finally:
        await engine.dispose()
    return out


def _artifacts(root: Path) -> dict[str, int]:
    return {
        d: sum(1 for p in (root / d).rglob("*") if p.is_file()) if (root / d).exists() else 0
        for d in ARTIFACT_DIRS
    }


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stage", choices=STAGES, default="ready")
    ap.add_argument("--logs-root", default=os.environ.get("UPDOWN_LOGS_DIR", "logs"))
    args = ap.parse_args()
    url = load_settings().database_url

    counts = await _counts(url, (*MUST_BE_EMPTY, *PEOPLE, "instruments", "candles"))
    bad: list[str] = []
    print(f"=== 배포 DB 검사 · stage={args.stage} ===")
    for name in MUST_BE_EMPTY:
        n = counts[name]
        mark = "  표 없음" if n is None else ("  ✅" if n == 0 else "  🔴")
        print(f"{mark} {name:22s} {'-' if n is None else n}")
        if n:
            bad.append(f"{name} 에 {n} 행 — 개발 데이터가 딸려 왔다 (볼륨 복사 금지)")
    print("--- 사람이 넣는 표 (세기만) ---")
    for name in PEOPLE:
        print(f"     {name:22s} {'-' if counts[name] is None else counts[name]}")
    print("--- 참조 표 ---")
    inst, cand = counts["instruments"], counts["candles"]
    print(f"     {'instruments':22s} {inst}")
    print(f"     {'candles':22s} {cand}")
    if args.stage in ("seeded", "ready") and not inst:
        bad.append("instruments 가 비었다 — `scripts/runtime/seed_instruments.py` 먼저")
    if args.stage == "ready" and not cand:
        bad.append("candles 가 비었다 — 백필(`scripts/runtime/backfill_cli.py`) 먼저")

    arts = _artifacts(Path(args.logs_root))
    print(f"--- 산출물 디렉터리 ({args.logs_root}) ---")
    for d, n in arts.items():
        print(f"{'  🔴' if n else '  ✅'} {d:22s} {n} 파일")
        if n:
            bad.append(f"logs/{d} 에 파일 {n}개 — 개발 산출물이 볼륨에 딸려 왔다")

    if bad:
        print("\n🔴 배포 시작 상태가 아니다:")
        for b in bad:
            print(f"   - {b}")
        return 1
    print("\n✅ 배포 시작 상태다")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
