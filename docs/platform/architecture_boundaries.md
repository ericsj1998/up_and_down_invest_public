# 도메인 경계 — 확정 매트릭스와 강제 방법

| 항목 | 값 |
|------|-----|
| 근거 | [auto_invest_spec.md](../planning/auto_invest_spec.md) §1.3, §3.1, §3.2 · 원칙 P4 |
| 결정 기록 | [Phase00_Foundation_plan.md](../planning/history/Phase00_Foundation_plan.md) D-5, D-6, P-1, P-2, P-3 |
| 강제 수단 | [`.importlinter`](../../.importlinter) — CI 에서 `lint-imports` |
| 확정일 | 2026-08-02 (P0-2) |

> 이 문서의 역할: **규칙이 무엇인지**가 아니라 **그 규칙의 근거가 어느 스펙 조항인지**를 남긴다.
> 나중에 규칙을 완화하고 싶어질 때, 근거 없이 뚫리는 것을 막는 장치다.
> 규칙을 바꾸려면 이 문서에 근거를 먼저 추가한다.

---

## 1. 계층 순서

```
common → marketdata → portfolio → analysis → decision → execution → orchestration → apps
(왼쪽이 하위. 상위는 하위를 import 가능, 역방향 금지)
```

**이 순서는 스펙 §3.2 의 데이터 흐름 그 자체다.**

| 스펙 §3.2 흐름 | 소속 계층 |
|---|---|
| `TechnicalAnalysis.analyze()` | analysis |
| `SignalAggregator.combine()` → `TradeProposal` 생성 | analysis |
| `RiskManager.evaluate()` → `TradeProposal` 소비, `ApprovedOrder` 생성 | decision |
| `OrderService.submit()` → `ApprovedOrder` 소비 | execution |

데이터가 흐르는 방향과 import 가 허용되는 방향이 같다. 그래서 규칙을 외울 필요가 없다 —
**"내가 소비하는 타입을 만든 쪽은 나보다 아래"** 하나면 된다.

---

## 2. 경계 판정 매트릭스

| 경계 | 판정 | 근거 |
|------|------|------|
| `analysis → execution` | **금지** | tasks.md P0-2 명시. 계층 순서상 자동 차단되지만 `forbidden` 계약으로 별도 가시화 |
| `decision → analysis` | 허용 | §4.6 — RiskManager 입력이 `TradeProposal`(analysis 산출물) |
| `execution → decision` | 허용 | §4.10 — Execution 입력이 `ApprovedOrder`, "가격/수량 판단은 하지 않음" |
| `decision → portfolio` | 허용 | §4.6 — RiskManager 입력이 `PortfolioState` |
| `portfolio` 의 위치 | analysis 보다 **하위** | 위 항목 때문에 decision 보다 아래여야 하고, 사실 집계는 아무 판단에도 의존하지 않는다 |
| §4.7 Allocation | **`decision/` 소속** | §4.18 의 역할 분리 — Unified Portfolio=**사실** / Allocation=**판단** |
| 백테스트·추천·Instant Analysis | **`orchestration/`** | 세 도메인을 전부 호출하는 조립 계층 (plan P-1) |
| 비용 테이블(§12.7) | **`common/costs.py`** | 백테스트·PaperAdapter·RR 계산 셋이 공유 → 최하위여야 셋 다 import 가능 (plan P-3) |
| 브로커 어댑터 획득 | **`execution.gateway.OrderGateway` 독점** | §12.4 — 절대 규칙 #0 (plan D-12) |

### 채택하지 않은 대안

**방식 A — "계약 타입 전부 `common`, 도메인 간 직접 import 전면 금지"**:
규칙이 한 줄로 끝나지만 `common` 이 비대해지고 "이 타입은 common 인가 도메인인가"를
매번 판단해야 한다. 스펙이 이미 데이터 흐름을 순서로 규정하고 있으므로,
그것을 그대로 계층으로 쓰는 편이 근거가 명확하다 (plan D-6).

---

## 3. `orchestration/` 입주 조건

> **자체 판단 로직이 없고, 여러 도메인을 호출만 하는 모듈**만 들어온다.
> 계산·판단이 생기는 순간 그 코드는 해당 도메인 패키지 소속이다.

이 한 줄이 `orchestration` 이 "아무거나 넣는 서랍"이 되는 것을 막는 유일한 장치다.
공교롭게도 **스펙이 입주 대상 셋을 스스로 "오케스트레이터"라고 부른다**:

| 모듈 | 스펙 표현 |
|------|----------|
| Backtest Engine (§4.11) | "**실거래와 동일한 전략 코드** 실행" |
| Recommendation (§4.9) | "자체 분석 로직 없음. **순수 오케스트레이터**" |
| Instant Analysis (§4.20) | "새 분석 로직 없음, **기존 모듈 호출만**" |

---

## 4. 계약 구성 (`.importlinter`)

| # | 계약 | 타입 | 목적 |
|---|------|------|------|
| 1 | 도메인 계층 순서 | `layers` (`exhaustive = true`) | §1 의 8단 순서를 강제 |
| 2 | 분석 ⇏ 집행 | `forbidden` | tasks.md P0-2 의 명시 규칙을 **별도로 가시화** |

**계약 2가 중복인데도 남기는 이유**: 계약 1로 이미 차단되지만, tasks.md 가 명시한 유일한
규칙이다. 나중에 계층 순서를 손대더라도 이 규칙만은 독립적으로 살아남아야 한다.

**`exhaustive = true` 의 역할**: 새 최상위 패키지를 만들고 계약에 등록하지 않으면
그 패키지만 규칙 밖에 남는다. 등록을 강제해 "조용히 경계 밖에 사는 코드"를 막는다.

### 정상 상태

```
$ uv run lint-imports
Analyzed 11 files, 0 dependencies.
----------------------------------

도메인 계층 순서 (common → … → apps 단방향) KEPT
분석은 집행을 직접 import 하지 않는다 (P4 · tasks.md P0-2) KEPT

Contracts: 2 kept, 0 broken.
```

---

## 5. 위반 재현 절차 (P0-2-4 증빙)

경계가 **실제로 강제되는지**는 위반을 만들어 봐야만 알 수 있다. 규칙을 고칠 때마다
이 절차를 다시 돌린다.

### 재현

```bash
cat > src/updown/analysis/_boundary_violation_probe.py <<'EOF'
"""임시 위반 재현 파일 (P0-2-4). 검증 후 삭제한다."""

from updown import execution

__all__ = ["execution"]
EOF

uv run lint-imports; echo "exit=$?"
```

### 실제 출력 (2026-08-02 채록)

```
Analyzed 12 files, 1 dependencies.
----------------------------------

도메인 계층 순서 (common → … → apps 단방향) BROKEN
분석은 집행을 직접 import 하지 않는다 (P4 · tasks.md P0-2) BROKEN

Contracts: 0 kept, 2 broken.


----------------
Broken contracts
----------------

도메인 계층 순서 (common → … → apps 단방향)
---------------------------------

updown.analysis is not allowed to import updown.execution:

- updown.analysis._boundary_violation_probe -> updown.execution (l.3)


분석은 집행을 직접 import 하지 않는다 (P4 · tasks.md P0-2)
---------------------------------------------

updown.analysis is not allowed to import updown.execution:

-   updown.analysis._boundary_violation_probe -> updown.execution (l.3)
```

```
lint-imports exit code = 1
```

**종료코드 1** → CI 잡이 레드가 된다. 위반 경로가 파일·줄 번호까지 출력되므로
"어디서 뚫렸는지"를 찾는 데 시간이 들지 않는다.

### 정리

```bash
rm src/updown/analysis/_boundary_violation_probe.py
uv run lint-imports   # 2 kept, 0 broken 으로 복귀
```

---

## 5.1 CI 레드 증빙 (G0-2 · tasks.md P0-2 DoD 3·4) ✅

로컬 종료코드만으로는 "CI 가 실제로 막는다"를 증명할 수 없다. 프로브 브랜치를 PR 로
올려 확인했다.

| 항목 | 값 |
|------|-----|
| 실행일 | 2026-08-03 |
| PR | [#1](https://github.com/ericsj1998/up_and_down_invest/pull/1) — **머지하지 않고 닫음** |
| 워크플로 실행 | run `30802029775` (event: `pull_request`) |
| 결론 | **`failure`** |
| 실패 스텝 | `import-linter — 도메인 경계` |
| 정리 | 브랜치 삭제, `main` 은 `2 kept, 0 broken` 유지 |

**스텝별 결과** — 경계 검사에서 정확히 끊기고 `pytest` 는 실행되지 않았다:

```
✓ ruff — lint
✓ ruff — format
✓ pyright — 타입 검사
X import-linter — 도메인 경계      ← 여기서 레드
- pytest                          ← 실행되지 않음
```

**CI 로그 채록**:

```
Analyzed 37 files, 48 dependencies.
-----------------------------------
도메인 계층 순서 (common → … → apps 단방향) BROKEN
분석은 집행을 직접 import 하지 않는다 (P4 · tasks.md P0-2) BROKEN
Contracts: 0 kept, 2 broken.
----------------
Broken contracts
----------------
도메인 계층 순서 (common → … → apps 단방향)
updown.analysis is not allowed to import updown.execution:
- updown.analysis._boundary_violation_probe -> updown.execution (l.7)

분석은 집행을 직접 import 하지 않는다 (P4 · tasks.md P0-2)
updown.analysis is not allowed to import updown.execution:
-   updown.analysis._boundary_violation_probe -> updown.execution (l.7)
##[error]Process completed with exit code 1.
```

**얻은 것**: 위반이 파일·줄 번호까지 출력되고, 두 계약이 **각각** 독립적으로 보고된다.
계약 2를 중복이라 여겨 지웠다면 이 출력의 절반이 사라진다 — 별도 계약으로 남긴 이유의
실증이다 (§4).

---

## 6. 규칙을 바꾸고 싶어질 때

경험상 가장 먼저 흔들리는 것은 **`portfolio` 의 위치**다. §4.18 드릴다운이 룰별 손익
귀속(§4.14)까지 내려가는데 그건 `orchestration` 에 있어서, `portfolio → orchestration`
을 열고 싶어진다.

**열지 않는다.** 집계 조인은 `orchestration` 에서 수행하고 `portfolio` 는 사실만 제공한다.
계층을 한 번 뒤집으면 그 다음 예외 요청을 거절할 근거가 사라진다.

바꿔야 한다면 순서는 이렇다:

1. 이 문서 §2 매트릭스에 **어느 스펙 조항을 근거로** 바꾸는지 행을 추가한다
2. `.importlinter` 계약을 수정한다
3. §5 재현 절차를 다시 돌려 새 규칙이 실제로 강제되는지 확인한다

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-08-02 | 최초 작성 (P0-2-2). D-6 계층 순서 확정, 계약 2종 + 위반 재현 출력 채록 |
