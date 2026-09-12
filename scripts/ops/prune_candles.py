"""봉 보관 정책 실행 — 기본은 **찍어 보기만** 한다 (T278 · 2026-09-12).

    uv run python scripts/ops/prune_candles.py            # 무엇을 지울지 세기만 (기본)
    uv run python scripts/ops/prune_candles.py --apply    # 실제로 지운다

정책은 `config/retention.yml`, 계획은 `common/db/retention.py` 가 만든다. 이 파일은 **실행만** 한다.

🔴 **일봉은 어떤 설정으로도 안 지워진다** — `never_delete` 자물쇠가 계획 단계에서 뺀다.
🔴 **기본이 찍어 보기**다. 지우는 것은 `--apply` 를 손으로 붙여야 한다. 되돌릴 수 없는 일에
   기본값을 주지 않는다.

⚠️ 파티션을 떼는 것이 아니라 축을 지정해 지운다 — 한 달 파티션에 모든 축이 섞여 있기 때문이다
   (`retention.py` 머리말). 공간은 OS 로 안 돌아가고 그 표가 다시 쓴다. 목적은 **표가 더 자라지
   않게** 하는 것이다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from updown.common.db.retention import count_sql, delete_sql, load_policy, plan  # noqa: E402
from updown.common.db.session import create_engine, create_session_factory  # noqa: E402


async def run(*, apply: bool) -> int:
    """계획을 세고 세거나 지운다.

    Args:
        apply: 참이면 실제로 지운다. 거짓이면 셈만 한다.

    Returns:
        종료 코드 (0 정상).
    """
    # 🔴 **연구 PC 에서는 지우지 않는다** (사용자 2026-09-12).
    #    로컬은 백테스트 말뭉치라 같은 정책을 걸면 1,030만 행이 대상이 된다 — 실제로 찍어 보고
    #    그 숫자를 본 것이 이 장치를 넣은 이유다. 찍어 보기는 어디서나 되고 지우기만 막는다.
    env = os.environ.get("APP_ENV", "")
    if apply and env != "live":
        print(f"🔴 APP_ENV={env or '(없음)'} — 지우기는 실계좌 서버(live)에서만 한다.")
        print("   여기서는 찍어 보기만 된다 (--apply 없이 다시).")
        return 1
    policy = load_policy()
    sweeps = plan(policy)
    if not sweeps:
        print("지울 축이 없다 — 정책이 비어 있다")
        return 0
    print(
        f"정책: {len(sweeps)}축 · 잠긴 축 {', '.join(sorted(f.value for f in policy.never_delete))}"
    )
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = create_session_factory(engine)
    total = 0
    async with factory() as session:
        for sweep in sweeps:
            args = {"frame": sweep.timeframe.value, "cutoff": sweep.cutoff}
            found = await session.scalar(sa.text(count_sql()), args)
            count = int(found or 0)
            total += count
            head = (
                f"  {sweep.timeframe.value:>4} · {sweep.keep_days}일 남김 · "
                f"{sweep.cutoff:%Y-%m-%d} 이전"
            )
            if not apply:
                print(f"{head} → {count:,}행 (찍어 보기)")
                continue
            if count == 0:
                print(f"{head} → 없음")
                continue
            await session.execute(sa.text(delete_sql()), args)
            await session.commit()
            print(f"{head} → {count:,}행 지움")
    await engine.dispose()
    print(f"합계 {total:,}행" + ("" if apply else " — 지우려면 --apply"))
    return 0


def main() -> int:
    """인자를 읽고 돌린다.

    Returns:
        종료 코드.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="실제로 지운다 (없으면 세기만)")
    args = parser.parse_args()
    return asyncio.run(run(apply=bool(args.apply)))


if __name__ == "__main__":
    sys.exit(main())
