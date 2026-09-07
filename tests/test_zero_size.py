"""거래소가 준 **0 을 0 으로 읽는다** (2026-08-30 요율 사고).

## 무슨 일이 있었나

배포 뒤에도 Binance 요율이 한도(2,400)에 붙어 있었다. 러너가 범인인 줄 알았는데
요청을 세어 보니 아니었다:

    3분간   klines 150 (limit=2 · 창 좁히기가 실제로 돌고 있었다)
            **exchangeInfo 56**  ← 1시간 기억통이 있는데도

기억통은 멀쩡했다. 문제는 **묻는 심볼**이었다:

    exchangeInfo?symbol=ETHUSDT260327   → "testnet exchangeInfo 에 없다"
    exchangeInfo?symbol=BTCUSDT251226   → "testnet exchangeInfo 에 없다"

분기물이다. 우리는 무기한만 돌리는데, 콘솔의 "거래소에 열린 포지션" 축이 그것들을
**열려 있다**고 판단하고 있었다. 실측 (734행):

    "0"       481      ← 걸렸다
    "0.0"     146      ← 걸렸다
    "0.00"     64      ⛔
    "0.000"    38      ⛔
    "0.0000"    3      ⛔
    "0E-7"      1      ⛔

`str(size) in {"0", "0.0"}` 으로 걸렀기 때문이다. **107행이 샜고**, 콘솔은 그 107개의
명세를 매번 물었다. 실패는 캐시하지 않으므로(그것은 옳다) 영원히 반복됐다.

## 🔴 교훈은 "0.00 을 빠뜨렸다" 가 아니다

**같은 판단을 여섯 벌로 쓰고 있었다.** 그래서 한 번에 여섯 곳이 틀렸고, 어느 하나를
고쳐도 나머지 다섯이 남았다. 이 파일은 그 판단이 **한 곳에 있고 숫자로 판단한다**는
것을 지킨다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from updown.common.numeric import zero


class TestItReadsEveryShapeOfZero:
    """실측에서 실제로 온 모양들 — 지어낸 것이 아니다."""

    @pytest.mark.parametrize(
        "text",
        ["0", "0.0", "0.00", "0.000", "0.0000", "0E-7", "-0", "0e-9", " 0.00 ", "+0.0"],
    )
    def test_zero_is_zero(self, text: str) -> None:
        assert zero(text) is True, f"{text!r} 를 0 으로 못 읽었다"

    @pytest.mark.parametrize("text", ["0.001", "-1", "1E-7", "116", "0.00000001"])
    def test_a_real_position_is_not_zero(self, text: str) -> None:
        """⛔ 있는 포지션을 "없다" 로 읽으면 화면에서 사라지고, 아무도 안 닫는다."""
        assert zero(text) is False, f"{text!r} 는 0 이 아니다"

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_missing_is_zero(self, text: str | None) -> None:
        """필드가 없으면 포지션이 없는 것이다 — 그 해석은 여섯 곳이 이미 쓰고 있었다."""
        assert zero(text) is True

    @pytest.mark.parametrize("text", ["", "nan", "abc", "--1"])
    def test_garbage_is_not_treated_as_absent(self, text: str) -> None:
        """🔴 **모르는 값을 "없다" 로 읽지 않는다.**

        빈 값은 없는 것이지만, 읽을 수 없는 값은 **모르는 것**이다. 모르는 것을
        "포지션 없음" 으로 처리하면 그 포지션은 화면에서 사라지고, 사라진 포지션은
        아무도 안 닫는다 (절대 규칙 #8 과 같은 방향).
        """
        if text.strip():
            assert zero(text) is False
        else:
            assert zero(text) is True


class TestTheJudgementLivesInOnePlace:
    """🔴 여섯 벌이었기 때문에 한 번에 여섯 곳이 틀렸다."""

    ROOT = Path(__file__).resolve().parents[1] / "src" / "updown"

    HOME = "common/numeric.py"
    """`zero` 가 사는 곳 — 거기 있는 옛 비교는 **사고 기록**이지 코드가 아니다."""

    def test_no_file_compares_zero_as_a_string(self) -> None:
        """⛔ `in {"0", "0.0"}` 이 다시 생기면 같은 사고가 다시 난다."""
        guilty = [
            name
            for path in self.ROOT.rglob("*.py")
            if (name := path.relative_to(self.ROOT).as_posix()) != self.HOME
            and '{"0", "0.0"}' in path.read_text(encoding="utf-8")
        ]
        assert guilty == [], f"문자열로 0 을 재는 곳이 남아 있다: {guilty}"

    def test_the_position_listers_use_it(self) -> None:
        """두 거래소가 **같은 규칙**으로 판단해야 콘솔이 한 가지 말을 한다."""
        for name in ("execution/gate_paper.py", "execution/binance_paper.py"):
            source = (self.ROOT / name).read_text(encoding="utf-8")
            assert "from updown.common.numeric import zero" in source, name
            assert 'zero(raw.get("size"))' in source, name
