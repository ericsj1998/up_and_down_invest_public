"""펀드 장부 재앵커 — T285 누수로 오염된 펀드 파일의 총자본을 실잔고 기준으로 되돌린다.

왜: 2026-09-17 까지 펀드 총자본은 세션 평가금액의 합이었고, 멤버 몫이 틱마다 새 예산으로
갈리는 구조라 누적 손익률이 틱·재기동마다 다시 곱해져 장부가 샜다(로컬 데모 2,000 → 114 ·
실계좌 300 → 132 · 계좌 실잔고 298 그대로). 코드는 고쳤지만(증분 합산) 이미 샌 장부는
스스로 못 돌아온다 — 사람이 실잔고를 보고 한 번 앵커한다.

무엇을: `logs/funds/<id>.json` 의 `twr.equity` 를 준 값으로, `twr`(누적 지수)는
`equity ÷ contributed` 로(입금이 있었으면 근사 · 표시용), 고점·낙폭은 그 지수에서 다시,
`marks` 는 지운다(다음 틱이 지금 값을 mark 로 잡아 과거를 다시 안 센다).
`seeds`·판 매핑·바스켓은 그대로.

    docker exec <api 컨테이너> python scripts/deploy/fund_reanchor.py <fund_id> <equity_usdt>
    docker restart <api 컨테이너>        # 메모리의 옛 장부가 파일을 덮어쓰기 전에

⚠️ api 가 떠 있는 채로 파일만 고치면 다음 저장(틱·입출금·편집)이 메모리 값으로 덮어쓴다 —
   바로 재기동한다.
⚠️ 실계좌: 실잔고는 거래소 계정 총액에서 **다른 펀드·단독 판 몫을 뺀 것**이다(펀드 하나면
   총액).
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

FUNDS = Path("logs/funds")


def reanchor(path: Path, equity: Decimal) -> dict[str, str]:
    """파일 하나를 재앵커한다.

    Args:
        path: 펀드 정의 파일.
        equity: 지금 이 펀드가 실제로 가진 돈(USDT).

    Returns:
        바뀐 값 요약 (전/후).

    Raises:
        ValueError: 음수 자본 · `twr` 블록 없음.
    """
    if equity < 0:
        raise ValueError(f"자본이 음수다: {equity}")
    data = json.loads(path.read_text(encoding="utf-8"))
    twr = data.get("twr")
    if not isinstance(twr, dict):
        raise ValueError(f"{path.name}: twr 블록이 없다 — 펀드 파일이 아니다")
    before = str(twr.get("equity"))
    contributed = Decimal(str(twr.get("contributed") or "0"))
    index = equity / contributed if contributed > 0 else Decimal(1)
    twr["equity"] = str(equity)
    twr["twr"] = str(index)
    twr["twr_peak"] = str(max(Decimal(1), index))
    peak = Decimal(str(twr["twr_peak"]))
    twr["deepest"] = str((peak - index) / peak if peak > 0 else Decimal(0))
    data["marks"] = {}
    data["reanchored"] = {"from": before, "to": str(equity)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fund": str(data.get("fund_id")), "equity_before": before, "equity_after": str(equity)}


def main(argv: list[str]) -> int:
    """CLI 진입점.

    Args:
        argv: `[fund_id, equity]`.

    Returns:
        종료 코드.
    """
    if len(argv) != 2:
        print("사용법: fund_reanchor.py <fund_id> <equity_usdt>", file=sys.stderr)
        return 2
    path = FUNDS / f"{argv[0]}.json"
    if not path.exists():
        print(f"{path} 가 없다", file=sys.stderr)
        return 1
    print(json.dumps(reanchor(path, Decimal(argv[1])), ensure_ascii=False))
    print("⚠️ api 컨테이너를 바로 재기동한다 — 메모리의 옛 장부가 이 파일을 덮어쓰기 전에")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
