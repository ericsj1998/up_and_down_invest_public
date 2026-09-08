"""모의 라이브 **판**의 영속화 — `run > trade > order` (T16 ②).

## 왜 파일로는 안 되나

저널은 **세션 id 별 파일**이다 (`logs/walkforward/{session_id}.json`). `src/` 를 고치면
uvicorn 이 리로드되고 판이 새 id 로 다시 뜨는데, 그러면 **새 파일이 생기고 옛 파일은
고아가 된다.** 이어붙일 닻이 파일 이름 안에 없다.

```
livec797465c.json   ← 22:16 에 뜬 판
liveb1432d17.json   ← 22:56 에 뜬 판 (같은 종목·같은 매매법인데 남남이다)
```

⇒ 그 순간부터 아무도 그 포지션을 관리하지 않는다. 반익도, 본절 상향도, 손절 재장착도
멈춘다 — 사용자 지적: *"이런식으로 연결이 끊겨버리면 1차 익절이나 그런 대응 자체가
불가능하잖아."*

## 닻은 A 안이다 — 그리고 그 위험을 스키마로 막는다

```
닻     종목 + 매매법 + live      (`GATE:BTC_USDT:sample_ma_cross@0.1.0:live`)
열기   같은 닻의 **열린** 판이 있으면 이어받는다
닫기   사람이 "RUN 삭제" 를 누른다
⛔     시간으로 자동 종료하지 않는다 — N 을 정할 근거가 없다
```

A 의 위험은 *"며칠 전 판까지 같은 RUN 으로 이어붙는 것"* 이다. 그것을 **부분 유니크
인덱스**로 막는다 — `anchor` 는 `closed_at IS NULL` 인 행에서만 유일하다. 즉 **열린 판은
닻마다 하나뿐**이고, 닫힌 판은 몇 개든 남아 과거 성적이 그대로 보존된다.

⛔ 열린 판을 시간으로 닫지 않는다. 닫는 순간 거래소 포지션이 고아가 되는데, 그 시점을
정할 근거가 어디에도 없다.

## 열거형을 CHECK 로 못 박지 않는 이유

`Actor`·`Direction`·`Outcome`·`HalfBy` 는 `orchestration/walkforward/ledger.py` 에 있고
`common/` 은 상위 계층을 import 할 수 없다 (CLAUDE.md 의존 방향). 값을 여기 복제하면
**두 벌이 되고**, 한쪽만 늘렸을 때 조용히 어긋난다 — 실제로 `SIGNAL_EXIT` 를 추가했을 때
집계에서 통째로 빠진 적이 있다.

⇒ 문자열로 둔다. 값의 권위는 원장에 있고, DB 는 그것을 **적을 뿐**이다.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import RATIO, Base, JsonDict, JsonList


class WalkforwardRun(Base):
    """한 판 — 종목·매매법·시드·증거금·레버리지·시작/종료 (T16 ②).

    Note:
        🔴 **`steps` 와 봉인 구간은 여기 없다.** 그 둘은 *이 프로세스가* 몇 번 판정했나와
        *이번에 뜬 판이* 어느 창을 봤나이며, 이어붙이면 화면의 "로직이 도는가" 표시가
        거짓말한다. 이어붙이는 것은 **매매·손익·시드·증거금·레버리지**뿐이다.

        ⚠️ `key` 는 화면이 쓰는 짧은 id 다 (`live8961b738`). 판을 이어받으면 **같은 key
        로 다시 뜬다** — 다르면 목록에 같은 판이 두 번 뜨고, 사람은 어느 쪽이 진짜인지
        모른다.
    """

    __tablename__ = "wf_runs"
    __table_args__ = (
        # 🔴 **A 안의 안전장치.** 열린 판은 닻마다 하나뿐이다 — 두 판이 같은 종목을
        #    돌면 Gate 는 종목당 포지션이 하나라 서로의 포지션을 자기 것으로 여긴다.
        sa.Index(
            "uq_wf_runs_open_anchor",
            "anchor",
            unique=True,
            postgresql_where=sa.text("closed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(unique=True, index=True)
    """화면·저널이 쓰는 짧은 id. 이어받으면 **같은 값으로 다시 뜬다**."""

    anchor: Mapped[str] = mapped_column(index=True)
    """`시장:종목:매매법:live|paper` — 같은 판인지 가르는 유일한 기준."""

    live: Mapped[bool]
    """실계좌 경로인가. 🔴 라이브와 백테스트는 목록을 공유하지 않는다."""

    market: Mapped[str]
    symbol: Mapped[str]
    playbook: Mapped[str]
    """성과 귀속 키 (`sample_ma_cross@0.1.0`). 버전이 바뀌면 **다른 판**이다."""

    playbook_id: Mapped[str]
    seed_cash: Mapped[Decimal]
    margin_budget: Mapped[Decimal | None]
    """굴리는 돈. `None` 이면 지갑 전액 (백테스트 기존 동작)."""

    leverage: Mapped[Decimal] = mapped_column(RATIO)
    profit_line: Mapped[Decimal | None] = mapped_column(sa.Numeric(38, 18))
    """**이 선을 넘은 부분에서만** 이익을 실현한다 (T21 ⑤). 없으면 모든 이익에서 뗀다."""

    budget_cap: Mapped[Decimal | None] = mapped_column(sa.Numeric(38, 18))
    """가용자금의 천장 (T21 ⑥). 없으면 상한 없이 커진다 — 손실에도 상한이 없다는 뜻이다."""

    skim_pct: Mapped[Decimal] = mapped_column(RATIO)
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    """`None` 이면 **열려 있다** — 위 부분 유니크 인덱스가 이 값을 본다."""

    closed_reason: Mapped[str | None]
    meta_json: Mapped[JsonDict] = mapped_column(default=dict)
    """저널 머리(플래그·봉인 등)를 그대로. ⚠️ 판단에 쓰지 않는다 — 되짚기용이다."""

    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class WalkforwardTrade(Base):
    """그 판의 매매 한 건 — **진입 근거를 포함해서** (T16 ①·②).

    Note:
        🔴 `evidence_json` 이 이 표의 존재 이유 절반이다. 진입가·손절·익절은 거래소에서
        되읽을 수 있지만(`LiveRunner.adopt`) *"어떤 박스의 어떤 방아쇠였나"* 는 원장에만
        있었다 — 여기 안 적으면 판이 죽는 순간 영영 복구할 수 없다.

        ⚠️ **계획값은 진입 시점의 값이다.** 나중에 레벨이 바뀌어도 안 움직인다. 움직이면
        달성률이 언제나 100% 가 되어 아무것도 못 잰다.
    """

    __tablename__ = "wf_trades"
    __table_args__ = (sa.UniqueConstraint("run_id", "trade_id", name="uq_wf_trades_run_trade"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("wf_runs.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str] = mapped_column(index=True)
    playbook: Mapped[str]
    actor: Mapped[str]
    """시스템 · 사람 · 이어받음. ⛔ 이어받음은 성과 표본에 섞지 않는다."""

    direction: Mapped[str]
    outcome: Mapped[str]
    placed_at: Mapped[datetime]
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    half_at: Mapped[datetime | None]
    half_by: Mapped[str | None]
    entry_fills_json: Mapped[JsonDict] = mapped_column(default=dict)
    """진입 다리 — `{"legs": [[가격, 비중], ...]}` (T19 ①).

    ⚠️ **`entry` 칸은 남겨 둔다.** SQL 로 훑을 때 평단 하나가 있어야 편하고, 그것은
    이 값에서 나온 **파생값**이다 — 읽을 때는 여기서 다시 만든다.

    ⚠️ 빈 객체면 다리 하나짜리다. 지금까지의 모든 행이 그 모양이다.
    """

    half_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(38, 18))
    """반익을 **얼마에** 덜었나 (2026-08-19 사고 ③).

    ⚠️ 옛 행은 NULL 이다 — 그때는 계획가로 계산했다는 뜻이고, 원장이 그 사실을 안다.
    """
    funding_paid: Mapped[Decimal | None] = mapped_column(sa.Numeric(38, 18))
    """거래소가 뗀 펀딩 누적 USDT (T226). 옛 행은 NULL = 0."""
    funding_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(38, 18))
    """펀딩 누적 · 명목 대비 비율 (T226). `gain_pct` 가 `cost_pct` 처럼 뺀다. 옛 행은 NULL = 0."""
    funding_keys_json: Mapped[JsonDict | None] = mapped_column(default=None)
    """이미 붙인 정산 열쇠 `{"keys": ["<epoch>:<change>", ...]}` (T226 · 0114). 옛 행은 NULL.

    ⚠️ NULL 인데 `funding_paid` 가 0 이 아니면 옛(v1.3.0 이전) 부푼 값 — 러너가 첫 동기화에서 맞춘다.
    """
    entry: Mapped[Decimal]
    exit_price: Mapped[Decimal | None]
    planned_stop: Mapped[Decimal]
    planned_first: Mapped[Decimal]
    planned_target: Mapped[Decimal]
    cost_pct: Mapped[Decimal] = mapped_column(RATIO)
    leverage: Mapped[Decimal] = mapped_column(RATIO)
    note: Mapped[str] = mapped_column(default="")
    evidence_json: Mapped[JsonList] = mapped_column(default=list)
    """진입 근거 (`source · family · grade · detail · price`). T16 ① 참고."""

    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class WalkforwardOrder(Base):
    """그 매매가 거래소로 보낸 주문 하나 (T16 ②).

    Note:
        🔴 **원장이 보유중인데 여기 행이 없으면 유령 포지션이다.** 원장은 판정만으로
        보유를 적고(`Session.step`) 전송은 러너가 따로 한다 — 전송이 실패해도 보유는
        남는다. 실제로 겪었다: 거래소 포지션 0 · 증거금 0 인데 화면은 `보유중 롱 64,182`.

        ⛔ **이 표로 원장을 고치지 않는다.** 고치는 것은 `reconcile()` 의 일이고, 여기서
        되돌리면 결정론 코어가 거래소 응답에 따라 달라진다 (절대 규칙 #5).

        ⚠️ `role` 은 우리가 붙인 이름이다. Gate 가 만든 `ao-` 주문(조건부 발동)은
        `text` 가 우리 것이 아니라 **이름으로는 못 엮는다** — 그것은 T18 ⑤ 가 조건부
        주문 id 로 잇는다.
    """

    __tablename__ = "wf_orders"
    # 🔴 **역할 하나에 행 하나다.** 조건부 손절은 24시간에 만료돼 매 걸음 다시 걸린다 —
    #    걸 때마다 행을 쌓으면 하루에 수천 행이 되고, 정작 알고 싶은 *"지금 손절이
    #    걸려 있나"* 는 가장 최근 행을 골라내야 답이 나온다.
    #
    # ⚠️ 그래서 **재장착 이력은 여기 없다.** 손절 상향의 증거는 `risk_plan_revisions`
    #    (append-only)의 몫이고, 이 표는 *지금 상태*를 든다.
    # 🔴 **매매 행이 아니라 판에 매단다** (2026-08-19 사고 ④).
    #
    #    예전에는 `wf_trades.id`(대리키)를 가리키는 외래키였다. 그러면 *"매매를 먼저
    #    저장해야 주문을 적을 수 있다"* 는 **쓰기 순서 제약**이 생기는데, 러너의 걸음은
    #    `_place()`(주문) → … → `_persist()`(저장) 순서다 — 즉 **진입 주문은 항상
    #    부모가 없는 시점에 기록됐고, `record_order` 가 조용히 버렸다.**
    #
    #      거래소 실제 체결 77건  vs  이 표 29건 (전부 손절)
    #
    #    ⇒ 순서를 바꾸거나(주문 경로에 DB 지연을 얹는다) 동시에 하는 것(경합이 **간헐적**
    #      이 된다 — 늘 실패하는 버그는 발견되고 가끔 실패하는 버그는 안 된다)이 아니라,
    #      **의존 자체를 없앤다.**
    #
    # ⭐ `trade_id` 는 판 안에서 이미 유일하다 (`uq_wf_trades_run_trade`). 조인은 그
    #   업무 키로 하고, 삭제 전파는 `run_id` 가 맡는다.
    #
    # ⚠️ 대가: *"주문은 실재하는 매매를 가리킨다"* 는 DB 차원 보장을 잃는다. 그런데 그
    #   보장은 **원래 없었다** — 위반을 막은 것이 아니라 조용히 버리고 있었다.
    __table_args__ = (
        sa.UniqueConstraint("run_id", "trade_id", "role", name="uq_wf_orders_run_trade_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("wf_runs.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str] = mapped_column(index=True)
    """매매의 짧은 id — `wf_trades.trade_id` 와 같은 값이지만 **외래키가 아니다.**

    ⚠️ 주문이 매매보다 **먼저** 적힐 수 있다. 그것이 이 칸이 문자열인 이유다.
    """
    role: Mapped[str]
    """진입 · 1차 익절 · 목표 익절 · 손절 · 청산 · 이어받음."""

    status: Mapped[str]
    """`sending` 이 남아 있으면 **보내려 했는데 응답을 못 받았다**는 뜻이다."""

    exchange_order_id: Mapped[str] = mapped_column(default="")
    contracts: Mapped[str] = mapped_column(default="")
    price: Mapped[Decimal | None]
    error: Mapped[str] = mapped_column(default="")
    raw_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class WalkforwardCalibration(Base):
    """**모의 라이브 교정 원장** — 백테스트 가정을 실측으로 바꾸기 위한 표 (T185).

    Note:
        🔴 **`wf_orders` 로는 못 한다.** 저 표는 `(run_id, trade_id, role)` 유일키로
        **역할당 한 행을 덮어쓴다** — 지금 상태를 드는 표다. 교정은 *"몇 번 시도했고
        그때마다 얼마나 어긋났나"* 를 세는 일이라 **모든 시도**가 남아야 한다.

        ⇒ 이 표는 **추가 전용**이다. 유일키를 걸지 않는다.

        ⚠️ **왜 이 표가 필요한가** (T185 §1): 같은 6종·같은 기간인데 BN 과 Gate 의
        축 판정이 부호까지 갈렸다. ±20%p 수준 효과가 체결 가정 잡음에 묻힌다는 뜻이고,
        그 잡음 크기를 모르면 어떤 개선도 판정할 수 없다.

        🔴 **없는 값은 사후에 못 만든다.** 판정 봉 종가는 데이터로 되찾아도 *그 순간
        러너가 무엇을 의도했는지* 는 못 되찾는다. 실제로 겪었다 — 2026-08-29
        post-only 거절 18건의 의도 가격을 사후에 구하지 못했다.

        ⛔ 이 표는 **읽기 전용 관측**이다. 여기 값으로 원장이나 주문을 고치지 않는다.
    """

    __tablename__ = "wf_calibration"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("wf_runs.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str] = mapped_column(default="", index=True)
    """매매의 짧은 id. 펀딩처럼 매매에 안 매달리는 사건은 빈 문자열이다."""

    kind: Mapped[str] = mapped_column(index=True)
    """`entry` · `exit` · `funding`. 무엇을 재는 행인가."""

    role: Mapped[str] = mapped_column(default="")
    """`wf_orders.role` 과 같은 이름 (진입 · 손절 · 청산 …)."""

    intended_price: Mapped[Decimal | None]
    """**백테스트라면 얼마였나** — 러너가 걸려고 한 지정가.

    체결가와의 차이가 곧 *"봉이 관통하면 체결"* 가정의 오차다.
    """

    judge_close: Mapped[Decimal | None]
    """판정 봉 종가. `intended_price` 의 분모 — 물러서기 0.30% 의 기준점이다."""

    judge_ts: Mapped[datetime | None]
    """판정 봉 시각 (UTC). 신호 -> 체결 지연의 시작점이다."""

    rvol: Mapped[Decimal | None]
    """진입 시점 실현변동성 (%/봉). 슬리피지를 변동성 구간별로 가르는 축이다."""

    wanted_contracts: Mapped[Decimal | None]
    """증거금 한도가 없었다면 걸었을 계약 수."""

    sent_contracts: Mapped[Decimal | None]
    """실제로 보낸 계약 수. `wanted` 보다 작으면 **증거금 한도가 발동**한 것이다."""

    amount: Mapped[Decimal | None]
    """펀딩 정산액 (음수면 받은 것). `kind=funding` 에서만 쓴다."""

    extra_json: Mapped[JsonDict] = mapped_column(default=dict)
    """자본·배율·승수 등 되짚기용 나머지."""

    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
