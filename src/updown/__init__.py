"""업 앤 다운 자동투자 플랫폼 루트 패키지.

하위 패키지는 스펙 §3.1 레이어를 그대로 따르며, 의존 방향은 단방향이다
(`common → marketdata → portfolio → analysis → decision → execution → orchestration → apps`).
경계는 `.importlinter` 계약으로 CI에서 강제한다 (spec §1.3, P4).
"""

__all__: list[str] = []
