"""도메인 값 객체 — 전 계층이 공유하는 계약 (interfaces v1).

모든 타입은 **frozen dataclass**다. 분석이 만든 값을 결정이 바꾸고, 결정이 확정한 값을
집행이 바꾸는 사고를 타입 레벨에서 막는다 (원칙 P4, spec §5.1).

컬렉션 필드에 `list` 가 아니라 `tuple` 을 쓰는 이유도 같다 — frozen dataclass 라도
list 필드는 내용이 바뀐다.

계약 문서: `docs/platform/interfaces_v1.md`
"""
