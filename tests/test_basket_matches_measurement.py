"""선택창에 올린 매매법은 **측정한 우주**로 펀드가 떠야 한다 (2026-09-19).

## 무엇이 하마터면 일어날 뻔했나

`private_strategy`(룰 0.3 + A · 1.9.0)를 골라 실계좌 펀드를 만들려던 순간, `config/baskets.yml`
`by_playbook` 에 그 매매법의 블록이 **없다는 것**을 발견했다. 블록이 없으면 묶음 기본(`default`)으로
떨어지는데 그것은 **SOL 이 없고 NEAR 가 있는** 다른 우주다:

    측정한 것   BTC · ETH · XRP · SOL · DOGE · ADA   → 4.47년 1,000 → 46,726 USDT
    떨어질 곳   BTC · ETH · XRP · DOGE · ADA · NEAR  → 아무도 잰 적 없는 조합

아무 에러도 안 난다. 펀드는 그냥 **다른 종목으로 돌고**, 화면은 여전히 A 의 성적을 보여 준다.
152·153차가 보인 대로 종목 구성은 결과를 크게 가른다 — 3종목 조합 20개의 4.47년 잔고가
5,080 ~ 60,790 USDT 로 흩어졌다. 이것은 "조금 다른 판" 이 아니라 **다른 매매법**이다.

## 그래서 무엇을 못 박나

선택창에 올라간(`listed`) **자리 배분 매매법**은 `by_playbook` 에 자기 블록이 있어야 한다.
자리 배분은 "종목마다 자기 자리" 가 전제라, 우주가 달라지면 자리 수도 뜻을 잃는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis.playbook.select import load_playbooks

BASKETS = Path("config/baskets.yml")


def _by_playbook() -> dict[str, Any]:
    raw = cast("dict[str, Any]", yaml.safe_load(BASKETS.read_text(encoding="utf-8")) or {})
    return cast("dict[str, Any]", raw.get("by_playbook") or {})


class TestListedSlotPlaybooksDeclareTheirUniverse:
    """자리 배분 + 선택창 = 바스켓이 있어야 한다."""

    def test_every_listed_slots_playbook_has_a_basket(self) -> None:
        blocks = _by_playbook()
        missing = [
            book.playbook_id
            for book in load_playbooks()
            if book.listed and book.weight_mode == "slots" and book.playbook_id not in blocks
        ]
        assert not missing, (
            f"{missing} 는 선택창에 있고 자리 배분인데 `baskets.yml by_playbook` 에 블록이 없다 — "
            f"펀드가 묶음 기본 우주(코인 기본 = SOL 없음·NEAR 있음)로 조용히 떨어진다. "
            f"측정한 종목과 다른 우주로 라이브가 돈다."
        )

    def test_the_two_rule_03_playbooks_share_the_core_six(self) -> None:
        """룰 0.3 과 A 는 **진입·청산이 같고 계좌 층만 다르다** — 우주가 달라질 이유가 없다."""
        blocks = _by_playbook()
        core = {"BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT"}
        for book_id in ("private_strategy", "private_strategy"):
            members = cast("list[dict[str, str]]", blocks[book_id]["members"])
            assert {m["symbol"] for m in members} == core, (
                f"{book_id} 의 바스켓이 핵심 6종이 아니다 — 4.47년 46,726 USDT 는 이 여섯으로 쟀다"
            )

    def test_slot_count_matches_the_universe_size(self) -> None:
        """자리 수 = 종목 수여야 "종목마다 자기 자리" 가 성립한다 (자리 6 · 6종)."""
        blocks = _by_playbook()
        for book in load_playbooks():
            if not (book.listed and book.weight_mode == "slots"):
                continue
            members = cast("list[dict[str, str]]", blocks[book.playbook_id]["members"])
            if book.playbook_id == "private_strategy":
                continue  # P3·V2 는 자리 3 · 6종이 의도다(자리 경합이 설계의 일부 · 93차)
            assert book.slots == len(members), (
                f"{book.playbook_id}: 자리 {book.slots} 인데 바스켓은 {len(members)}종 — "
                f"A 구성은 '종목마다 자기 자리' 가 전제다(130차)"
            )
