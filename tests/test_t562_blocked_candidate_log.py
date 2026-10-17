"""T562 — 막힌 후보를 개수가 아니라 **기록**으로 남긴다 (`session_candidate_blocked`).

깔때기(`blocked:<코드>`)는 개수만 올려서, 사후 분석이 *"재현은 들어가고 라이브는 못 들어간 자리"* 를
못 찾았다(생존자 편향 · T561 §9). 여기서 못 박는 것:

    ① 펀드를 기다리는 판에서 후보 하나가 막히면 기록 한 건 · 진입은 없다
    ② 같은 봉에서 같은 후보를 다시 막아도 기록은 한 건이다
    ③ 문(펀드 규칙)이 막은 기록에는 쓰려던 크기까지 실린다 — 값은 읽기만 한다
    ④ 저널에 `blocked_recent` 가 실린다(순환 · 최근 `BLOCKED_RECENT_LIMIT`)
    ⑤ 라이브 러너는 새것만 `event_logs` 에 **추가**하고, 실패하면 경고를 남기고 다음에 다시 적는다
    ⑥ live-check 한 줄 — `awaiting_fund` 는 러너 기동 뒤 4H 안에서만 정상
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from test_sample_end_to_end import (  # pyright: ignore[reportPrivateUsage]
    START,
    _candle,  # pyright: ignore[reportPrivateUsage]
    _session,  # pyright: ignore[reportPrivateUsage]
    _ShutGate,  # pyright: ignore[reportPrivateUsage]
)
from updown.common.db.models.enums import LogLevel
from updown.common.domain.instrument import Timeframe
from updown.orchestration.live_review.check import Health, blocked_row
from updown.orchestration.live_review.snapshot import Snapshot, load_snapshot
from updown.orchestration.playbook_run import Proposal
from updown.orchestration.walkforward import Session
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import BLOCKED_EVENT, LiveRunner
from updown.orchestration.walkforward.session import BLOCKED_RECENT_LIMIT
from updown.orchestration.walkforward.store import RunStoreError


def _eligible(proposals: tuple[Proposal, ...]) -> list[Proposal]:
    """진입에 나설 후보 — 룰이 막은 것(`Proposal.blocked`)은 뺀다."""
    return [item for item in proposals if not item.blocked]


def _walk_until_blocked(session: Session) -> tuple[Proposal, ...]:
    """처음 막힌 후보가 생긴 걸음까지 걷고 그 걸음의 후보를 돌려준다."""
    for _ in range(20_000):
        if session.finished:
            break
        shot = session.step()
        if session.blocked_seq and shot is not None:
            return shot.proposals
    raise AssertionError("전제: 예시 매매법이 후보를 한 번은 낸다")


class TestTheSessionRecords:
    def test_a_waiting_member_records_the_candidate_and_does_not_enter(self) -> None:
        """① 후보 하나 = 기록 하나 · 진입 없음."""
        session = _session()
        session.fund_ready = False
        proposals = _walk_until_blocked(session)
        eligible = _eligible(proposals)
        assert not session.ledger.records, "펀드를 기다리는 동안은 한 건도 안 산다"
        assert session.blocked_seq == len(eligible) >= 1, "막힌 후보 하나에 기록 하나"
        rec = session.blocked_recent[-1]
        setup = eligible[-1].setup
        assert rec.code == "awaiting_fund"
        assert rec.at == session.cursor, "봉 시각은 세션 커서(UTC)다"
        assert rec.symbol == session.instrument.symbol
        assert rec.leg == eligible[-1].playbook.attribution
        assert rec.playbook_id == eligible[-1].playbook.playbook_id
        assert rec.direction is Direction.LONG
        assert (rec.entry, rec.stop, rec.size_mult) == (
            setup.avg_entry,
            setup.stop_loss,
            setup.size_mult,
        )
        assert rec.exposure is None, "크기를 재기 전에 막혔다 — 지어내지 않는다"

    def test_the_whole_walk_records_one_per_candidate_per_bar(self) -> None:
        session = _session()
        session.fund_ready = False
        expected = 0
        for _ in range(20_000):
            if session.finished:
                break
            shot = session.step()
            if shot is not None:
                expected += len(_eligible(shot.proposals))
        assert not session.ledger.records
        assert session.blocked_seq == expected
        assert session.funnel.get("blocked:awaiting_fund", 0) > 0, "깔때기는 예전 그대로 센다"

    def test_the_same_bar_is_not_recorded_twice(self) -> None:
        """② 같은 봉 재호출(마감 진입 뒤 조기 진입이 같은 후보로 또 묻는 경우 등)은 한 건."""
        session = _session()
        session.fund_ready = False
        proposals = _walk_until_blocked(session)
        before = session.blocked_seq
        for item in _eligible(proposals):
            session._note_proposal_blocked("awaiting_fund", item)  # pyright: ignore[reportPrivateUsage]
        assert session.blocked_seq == before, "같은 봉 · 같은 후보 · 같은 코드는 다시 안 남긴다"
        session._note_proposal_blocked("unreconciled", _eligible(proposals)[0])  # pyright: ignore[reportPrivateUsage]
        assert session.blocked_seq == before + 1, "코드가 다르면 다른 기록이다"

    def test_the_flip_path_records_once_per_bar(self) -> None:
        """뒤집기 길(`_open_reversal`)은 `_may_enter` 를 안 거친다 — 따로 막고 따로 남긴다."""
        session = _session()
        session.fund_ready = False
        closed = TradeRecord(
            trade_id="t1",
            playbook="sample@0.1",
            actor=Actor.SYSTEM,
            direction=Direction.LONG,
            placed_at=START,
            entry=Decimal(100),
            planned_stop=Decimal(90),
            planned_first=Decimal(110),
            planned_target=Decimal(120),
            outcome=Outcome.OPEN,
            cost_pct=Decimal("0.001"),
            leverage=Decimal(1),
        )
        bar = _candle(Timeframe.H4, START, Decimal(105), Decimal(104))
        assert session._open_reversal(closed, bar) is None  # pyright: ignore[reportPrivateUsage]
        assert session._open_reversal(closed, bar) is None  # pyright: ignore[reportPrivateUsage]
        assert session.blocked_seq == 1
        rec = session.blocked_recent[-1]
        assert (rec.code, rec.direction, rec.entry) == ("awaiting_fund", Direction.SHORT, bar.close)
        assert session.funnel["blocked:awaiting_fund"] == 2, (
            "깔때기는 부를 때마다 센다(예전 그대로)"
        )

    def test_a_gate_block_carries_the_size_it_wanted(self) -> None:
        """③ 문이 막은 자리 — 진입 · 손절 · 쓰려던 크기를 읽어 둔다. 사지는 않는다."""
        session = _session()
        session.entry_gate = _ShutGate()
        for _ in range(20_000):
            if session.finished:
                break
            session.step()
        assert not session.ledger.records
        recs = [item for item in session.blocked_recent if item.code == "gate:test"]
        assert recs, "문이 막은 후보가 기록에 없다"
        assert all(item.exposure is not None and item.exposure > 0 for item in recs)
        assert all(item.entry is not None and item.stop is not None for item in recs)
        assert session.blocked_seq <= session.gate_held, "기록은 같은 봉 중복을 거른 수다"

    def test_the_ring_keeps_the_latest(self) -> None:
        session = _session()
        for i in range(BLOCKED_RECENT_LIMIT + 5):
            session._note_blocked(f"code{i}", "sample@0.1", Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        assert len(session.blocked_recent) == BLOCKED_RECENT_LIMIT
        assert session.blocked_since(0)[0].seq == 6, (
            "밀려난 것은 없다 — 러너가 첫 seq 로 잃은 수를 안다"
        )
        assert session.blocked_since(session.blocked_seq) == ()

    def test_the_journal_carries_blocked_recent(self, tmp_path: Path) -> None:
        """④ 저널 — 막힌 후보가 생긴 걸음에도 다시 쓴다."""
        session = _session()
        session.fund_ready = False
        session.journal_path = tmp_path / "run.json"
        _walk_until_blocked(session)
        doc = json.loads(session.journal_path.read_text(encoding="utf-8"))
        last = doc["blocked_recent"][-1]
        assert last["code"] == "awaiting_fund"
        assert last["seq"] == session.blocked_seq
        assert last["at"] == session.cursor.isoformat()
        assert last["playbook_id"] and last["direction"] == Direction.LONG.value
        assert last["exposure"] is None


class _Store:
    """`RunStore.note_event` 대역 — 추가만 받는다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[tuple[str, dict[str, Any], LogLevel]] = []
        self.fail = fail

    async def note_event(
        self, event_type: str, payload: dict[str, Any], *, level: LogLevel = LogLevel.ERROR
    ) -> None:
        if self.fail:
            raise RunStoreError("DB 에 닿지 않는다")
        self.rows.append((event_type, payload, level))


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, event: str, **_: Any) -> None:
        self.warnings.append(event)


BORN = datetime(2026, 10, 17, tzinfo=UTC)


def _runner(session: Session, store: _Store) -> tuple[LiveRunner, _Log]:
    """급전 · 스트림 없이 기록 경로만 물린 러너(`test_live_incident_20260820.runner_for` 꼴)."""
    made = LiveRunner.__new__(LiveRunner)
    log = _Log()
    made._session = session  # pyright: ignore[reportPrivateUsage]
    made._store = store  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._log = log  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made.observe_only = False
    made._blocked_noted = 0  # pyright: ignore[reportPrivateUsage]
    made._born_at = BORN  # pyright: ignore[reportPrivateUsage]
    made._run_key = "k1"  # pyright: ignore[reportPrivateUsage]
    return made, log


class TestTheRunnerAppends:
    async def test_new_records_go_to_event_logs_once(self) -> None:
        """⑤ 새것만 한 줄씩 · INFO · 다시 불러도 안 겹친다."""
        session = _session()
        session._note_blocked("awaiting_fund", "sample@0.1", Direction.LONG, entry=Decimal(1))  # pyright: ignore[reportPrivateUsage]
        session._note_blocked("gate:slots", "sample@0.1", Direction.SHORT, exposure=Decimal(2))  # pyright: ignore[reportPrivateUsage]
        store = _Store()
        runner, log = _runner(session, store)
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]
        assert [row[0] for row in store.rows] == [BLOCKED_EVENT, BLOCKED_EVENT]
        assert all(row[2] is LogLevel.INFO for row in store.rows)
        first = store.rows[0][1]
        assert first["code"] == "awaiting_fund" and first["entry"] == "1"
        assert first["symbol"] == session.instrument.symbol and first["run"] == "k1"
        assert first["runner_started"] == BORN.isoformat()
        assert store.rows[1][1]["exposure"] == "2"
        assert not log.warnings

    async def test_a_failure_warns_and_retries_next_step(self) -> None:
        session = _session()
        session._note_blocked("awaiting_fund", "sample@0.1", Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        store = _Store(fail=True)
        runner, log = _runner(session, store)
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]
        assert log.warnings == ["live_blocked_note_failed"], "조용히 삼키지 않는다 (규칙 #8)"
        store.fail = False
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]
        assert len(store.rows) == 1, "다음 걸음에 남은 것부터 다시 적는다"

    async def test_records_lost_from_the_ring_are_counted(self) -> None:
        session = _session()
        for i in range(BLOCKED_RECENT_LIMIT + 3):
            session._note_blocked(f"code{i}", "sample@0.1", Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        store = _Store()
        runner, log = _runner(session, store)
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]
        assert "live_blocked_note_lost" in log.warnings
        assert len(store.rows) == BLOCKED_RECENT_LIMIT

    async def test_no_store_means_no_work(self) -> None:
        session = _session()
        session._note_blocked("awaiting_fund", "sample@0.1", Direction.LONG)  # pyright: ignore[reportPrivateUsage]
        runner, _ = _runner(session, _Store())
        runner._store = None  # pyright: ignore[reportPrivateUsage]
        await runner._note_blocked_candidates()  # pyright: ignore[reportPrivateUsage]


NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def _snap(blocked: list[dict[str, Any]] | None) -> Snapshot:
    return Snapshot(
        path=Path(),
        taken_at=NOW,
        runs=[],
        trades=[],
        orders=[],
        fund={},
        events=[],
        exchange={},
        blocked=blocked,
    )


def _row(code: str, ts: datetime, started: datetime | None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "symbol": "BTC_USDT", "ts": ts.isoformat()}
    if started is not None:
        out["runner_started"] = started.isoformat()
    return out


class TestLiveCheckRow:
    def test_awaiting_fund_right_after_boot_is_normal(self) -> None:
        boot = NOW - timedelta(hours=5)
        snap = _snap(
            [
                _row("awaiting_fund", boot + timedelta(minutes=2), boot),
                _row("gate:slots", NOW - timedelta(hours=1), boot),
            ]
        )
        row = blocked_row(snap, Health(), NOW, 7)
        assert row.status == "ok"
        assert row.value.startswith("2건") and "awaiting_fund 1" in row.value
        assert "gate:slots 1" in row.value

    def test_awaiting_fund_outside_the_window_warns(self) -> None:
        boot = NOW - timedelta(hours=10)
        snap = _snap([_row("awaiting_fund", boot + timedelta(hours=5), boot)])
        row = blocked_row(snap, Health(), NOW, 7)
        assert row.status == "warn"
        assert "밖 1건" in row.value

    def test_missing_boot_falls_back_to_api_start(self) -> None:
        boot = NOW - timedelta(hours=1)
        snap = _snap([_row("awaiting_fund", boot + timedelta(minutes=1), None)])
        assert blocked_row(snap, Health(api_started=boot), NOW, 7).status == "ok"
        assert blocked_row(snap, Health(), NOW, 7).status == "warn", "모르면 확인 쪽으로 센다"

    def test_old_dump_says_so_and_none_is_ok(self) -> None:
        assert blocked_row(_snap(None), Health(), NOW, 7).status == "info"
        empty = blocked_row(_snap([]), Health(), NOW, 7)
        assert (empty.status, empty.value) == ("ok", "0건")

    def test_old_rows_fall_out_of_the_period(self) -> None:
        boot = NOW - timedelta(days=9)
        snap = _snap([_row("awaiting_fund", boot + timedelta(hours=6), boot)])
        assert blocked_row(snap, Health(), NOW, 7).value == "0건"

    def test_the_snapshot_reads_blocked_tsv(self, tmp_path: Path) -> None:
        payload = {"code": "awaiting_fund", "symbol": "BTC_USDT", "runner_started": NOW.isoformat()}
        (tmp_path / "blocked.tsv").write_text(
            f"2026-10-17T12:01:00Z\t{json.dumps(payload)}\n", encoding="utf-8"
        )
        snap = load_snapshot(tmp_path)
        assert snap.blocked == [{**payload, "ts": "2026-10-17T12:01:00Z"}]
        assert load_snapshot(tmp_path / "없음").blocked is None, "파일이 없으면 옛 덤프다"
