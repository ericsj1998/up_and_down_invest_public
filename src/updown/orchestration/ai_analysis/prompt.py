"""프롬프트 조립 (Phase 5 §5-6).

## 🔴 종목명을 넘기지 않는다

요청서가 명시한 규칙이다 — *"단, 종목은 넘기지 않는다."* 이유가 측정에 중요하다:
모델이 종목을 알면 **차트가 아니라 기억을 분석**할 수 있다. "2025년 BTC 였지" 를
떠올리는 것과 캔들을 읽는 것은 다른 능력이고, 우리가 재려는 것은 후자다.
펀더멘탈 기능이 붙으면 그때 별도 경로로 넘긴다.

## 프롬프트를 바꾸면 새 참가자다

⛔ 결과를 보고 프롬프트를 손보면 §5.6.2 자동조율의 LLM 판이다. 바꿀 거면 표본을
새로 쌓는다 (G-AI-5). 그래서 이 파일의 문자열은 **버전 상수**처럼 다룬다.
"""

from decimal import Decimal

from updown.common.domain.candle import Candle

PROMPT_VERSION = "1.0"
"""프롬프트 판 번호 — 바뀌면 **새 참가자**다 (G-AI-5). 결과에 함께 기록한다."""

SYSTEM_PROMPT = """당신은 기술적 분석만으로 판단하는 트레이딩 분석가다.
종목명·뉴스·펀더멘탈 정보는 주어지지 않으며, 오직 캔들 데이터만 보고 판단한다.

반드시 아래 JSON 스키마 **하나만** 출력한다. 설명 문장을 JSON 밖에 쓰지 않는다.

{
  "trend": "UP" | "DOWN" | "RANGE",
  "levels": [{"kind": "support"|"resistance", "price": "숫자문자열", "label": "짧은 설명"}],
  "zones": [{"kind": "demand"|"supply", "low": "숫자문자열", "high": "숫자문자열",
             "timeframe": "4h", "label": "@4h OB"}],
  "trendlines": [{"kind": "support"|"resistance",
                  "from": {"ts": "2026-08-01T00:00:00Z", "price": "숫자문자열"},
                  "to":   {"ts": "2026-08-09T00:00:00Z", "price": "숫자문자열"}}],
  "plan": {"stop_loss": "숫자문자열", "take_profit_half": "숫자문자열",
           "take_profit_full": "숫자문자열", "conviction_pct": 0~100},
  "reasoning": "왜 그렇게 판단했는지 한국어로"
}

제약:
- **롱(매수) 관점만** 낸다. stop_loss < 현재가 < take_profit_half <= take_profit_full.
- 모든 가격은 **문자열**로 쓴다 (소수점 정밀도 보존).
- 모든 시각은 **UTC ISO8601**(끝에 Z)로 쓴다.
- 선·면을 찾지 못하면 빈 배열로 둔다. 없는 것을 지어내지 않는다."""


def _render(candles: list[Candle], limit: int) -> str:
    """캔들을 프롬프트에 실을 표로 만든다.

    Args:
        candles: 캔들 (오름차순).
        limit: 뒤에서 몇 개만 실을지.

    Returns:
        한 줄에 한 봉인 CSV 유사 문자열.

    Note:
        JSON 이 아니라 CSV 로 싣는 이유는 **토큰**이다. 7개 타임프레임을 JSON 으로
        실으면 키 이름이 봉마다 반복돼 몇 배가 된다.
    """
    rows = ["ts,open,high,low,close,volume"]
    rows.extend(
        f"{candle.ts:%Y-%m-%dT%H:%M:%SZ},{candle.open},{candle.high},"
        f"{candle.low},{candle.close},{candle.volume}"
        for candle in candles[-limit:]
    )
    return "\n".join(rows)


def build_user_prompt(
    frames: dict[str, list[Candle]],
    entry: Decimal,
    hold_note: str,
    bars_per_frame: int = 120,
) -> str:
    """차트 데이터를 사용자 프롬프트로 조립한다.

    Args:
        frames: 타임프레임 이름 → 캔들. 순서가 그대로 프롬프트 순서다.
        entry: 현재가 — 계획 정합성의 기준이며 모델에게도 알려 준다.
        hold_note: 주문 유지 기간 설명 (사용자 설정).
        bars_per_frame: 타임프레임당 실을 봉 수.

    Returns:
        사용자 프롬프트.

    Note:
        `bars_per_frame` 기본 120 은 토큰과 정보량의 절충이다. 7개 프레임 x 120봉이면
        대략 840줄인데, 이보다 늘리면 소형 모델의 컨텍스트를 넘긴다 — 넘치면 그 모델은
        **무조건 실패**하고, 그것은 모델 능력이 아니라 우리 설계 탓이 된다.
    """
    blocks = [
        f"## 현재가\n{entry}",
        f"## 주문 유지 기간\n{hold_note}",
    ]
    for name, candles in frames.items():
        if not candles:
            continue
        shown = min(len(candles), bars_per_frame)
        blocks.append(f"## {name} 캔들 (최근 {shown}봉)\n{_render(candles, bars_per_frame)}")
    blocks.append("위 데이터만으로 분석하고, 지정된 JSON 스키마 하나만 출력하라.")
    return "\n\n".join(blocks)
