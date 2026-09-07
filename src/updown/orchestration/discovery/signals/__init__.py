"""A~F 신호 — **한 파일에 한 계열** (T153 §7).

계열을 import 하는 것만으로 레지스트리가 찬다. 격자의 분모(`registry()`)가 여기서
나오므로, **좋은 것만 골라 담으면 다중검정 보정이 거짓말이 된다.**

    oscillators.py   A-1  OSC-01~08 (RSI · 스토캐스틱 · MACD 셋)
    trend.py         A-3  MA-01/04/05 · OSC-09 (DMI)
    volatility.py    A-4  BND-02/03/04 · OSC-10 (ATR)
    divergence.py    A-2  DIV-01/02/03/05 (DIV-04 는 체결 데이터가 없어 보류)
    levels.py        A-5  LVL-03/09/10/11/12 (4순위 Wave 1 · 자유도 0인 절대 레벨)
    structure.py     A-6  STR-01~07 (4순위 Wave 2 · 확정 이벤트 스트림만 읽는다)
    extras.py        2순위 — MA-06/07 · BND-05/06 · OSC-05/11a/11b
    control.py       ⚠️ 신호가 아니다 — CTRL-01 무작위 진입(음성 대조군)

⚠️ **아직 없는 계열**: A-5 레벨 일부(LVL-01/02·04~08 — 개수를 우리가 정하는 것들.
귀무모형이 있어야 붙인다) · A-7 유동성 ·
A-8 오더플로우 · A-9 캔들 · A-10~12 차트패턴. 지금 격자는 그 사실과 함께 읽어야 한다 —
*"이 신호들 중 최고"* 이지 *"모든 신호 중 최고"* 가 아니다.
"""

from updown.orchestration.discovery.signals import (
    control,
    divergence,
    extras,
    levels,
    oscillators,
    structure,
    trend,
    volatility,
)
from updown.orchestration.discovery.signals.base import Board, Signal, Trigger, registry

__all__ = [
    "Board",
    "Signal",
    "Trigger",
    "control",
    "divergence",
    "extras",
    "levels",
    "oscillators",
    "registry",
    "structure",
    "trend",
    "volatility",
]
