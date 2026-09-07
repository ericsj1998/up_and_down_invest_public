"""백테스트 조립 (spec §4.11, §5.6.7 · P1-8).

**왜 `orchestration/` 인가**: 백테스트는 `analysis`(탐지) · `decision`(손절·수량 확정) ·
`common.costs`(비용)를 **조립**한다. `analysis/` 에 두면 "분석 → 집행 import 금지"(원칙 P4)를
위반하고, `decision/` 에 두면 리스크 정책이 측정 코드에 묶인다.

⚠️ 2026-08-23 폐기: matrix · report · depth · equity · plan_shape · regime · regime_check · window.
실전 백테스트는 `scripts/research/backtest_lab.py` + `scripts/research/gate_backtest.py` 다.
남은 것은 `chart.py`
(inspection 스냅샷이 쓴다) 뿐이다. 결정 기록: docs/planning/history/T30_retirement_2026-08-23.md
"""
