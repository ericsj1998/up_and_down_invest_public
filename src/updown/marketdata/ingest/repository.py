"""캔들·무결성 이슈 영속화 (P0-8-2·5 · spec §9, plan D-4, D-14).

## upsert 가 필요한 이유

두 가지다. ① 백필을 중간에 죽였다 재실행하면 겹치는 구간이 다시 들어온다 —
재실행 안전성(P0-8-3)이 없으면 매번 처음부터 지우고 다시 받아야 한다.
② 주기 수집이 **최근 N봉을 재조회해 갭을 메운다**(P0-8-6). 그 재조회분은 이미 있는 봉과
겹친다.

`DO NOTHING` 이 아니라 **`DO UPDATE`** 를 쓴다 — 마감된 봉은 값이 바뀌지 않지만, 거래소가
집계를 정정하는 경우가 있고 그때 옛 값을 붙잡고 있을 이유가 없다.

## 파티션이 없으면 INSERT 가 실패한다

`candles` 는 월 RANGE 파티션이고 DEFAULT 파티션을 **일부러 두지 않았다** (plan D-4) —
범위 밖 데이터를 조용히 흡수하면 파티션 누락을 눈치채지 못한다. 그래서 적재 전에 필요한
파티션을 보장한다. 실패를 미리 막는 것이 아니라, **실패가 나기 전에 만들어 두는** 것이다.
"""

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.db.base import JsonDict
from updown.common.db.models.enums import QualityIssueStatus, QualityIssueType
from updown.common.db.partitions import ensure_partitions_ddl, month_floor
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.ingest.integrity import IntegrityIssue

_logger = get_logger("marketdata.ingest.repository")

#: 한 번의 executemany 에 넣을 행 수.
#:
#: 5m 1년치는 종목당 10.5만 봉이다. 한 문장에 전부 넣으면 파라미터 수가 psycopg 한도를
#: 넘고 실패 시 되돌릴 양도 커진다. 청크로 끊으면 진행 상황도 로그로 보인다.
UPSERT_CHUNK_SIZE = 1_000

_UPSERT_CANDLES = sa.text("""
    INSERT INTO candles (instrument_id, timeframe, ts, open, high, low, close, volume)
    VALUES (:instrument_id, :timeframe, :ts, :open, :high, :low, :close, :volume)
    ON CONFLICT (instrument_id, timeframe, ts) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume
""")

_INSERT_ISSUE = sa.text("""
    INSERT INTO candle_quality_issues
        (id, instrument_id, timeframe, ts_start, ts_end, issue_type, detail_json, status)
    VALUES
        (gen_random_uuid(), :instrument_id, :timeframe, :ts_start, :ts_end,
         :issue_type, CAST(:detail AS JSONB), 'open')
""")


class RepositoryError(RuntimeError):
    """영속화 실패."""


class InstrumentNotFoundError(RepositoryError):
    """`instruments` 에 없는 종목이다.

    Note:
        별도 타입인 이유: "시드를 안 돌렸다"와 "DB 가 죽었다"는 대응이 다르다.
        전자는 `scripts/runtime/seed_instruments.py` 를 실행하면 되고 후자는 장애다.
    """


@dataclass(frozen=True, slots=True)
class OpenIssue:
    """열린 무결성 이슈 1건 (판정 CLI 용).

    Attributes:
        issue_id: 이슈 id.
        instrument_id: 종목 id.
        instrument: 도메인 종목.
        timeframe: 시간축.
        ts_start: 구간 시작.
        ts_end: 구간 끝.
        issue_type: 위반 종류.
        detail: 최초 적재 시의 근거.
        detected_at: 탐지 시각.
    """

    issue_id: uuid.UUID
    instrument_id: int
    instrument: Instrument
    timeframe: Timeframe
    ts_start: datetime
    ts_end: datetime
    issue_type: QualityIssueType
    detail: JsonDict
    detected_at: datetime

    @property
    def span_label(self) -> str:
        """사람이 읽는 구간 표기."""
        if self.ts_start == self.ts_end:
            return self.ts_start.isoformat()
        return f"{self.ts_start.isoformat()} ~ {self.ts_end.isoformat()}"


@dataclass(frozen=True, slots=True)
class Coverage:
    """종목 x TF 의 적재 커버리지 (P0-8-7 DoD 1).

    Attributes:
        instrument_id: 종목 id.
        symbol: 종목 코드.
        timeframe: 시간축.
        bars: 적재된 봉 수.
        first_ts: 가장 오래된 봉 시각. 비었으면 None.
        last_ts: 가장 최근 봉 시각. 비었으면 None.
    """

    instrument_id: int
    symbol: str
    timeframe: Timeframe
    bars: int
    first_ts: datetime | None
    last_ts: datetime | None


class CandleRepository:
    """캔들·무결성 이슈 저장소.

    Note:
        raw SQL 을 쓴다. `candles` 는 파티션 부모라 ORM 의 이점(관계 순회·식별자 맵)이
        없고, 10만 행 단위 적재에서는 ORM 인스턴스 생성이 그대로 비용이다.
        psycopg3 라 파라미터 문법이 psql 과 같아 쿼리를 그대로 옮겨 실행해 볼 수 있다
        (plan D-2).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """저장소를 만든다.

        Args:
            session_factory: 세션 팩토리.
        """
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # instruments
    # ------------------------------------------------------------------

    async def upsert_instrument(self, instrument: Instrument) -> int:
        """종목을 등록하고 id 를 돌려준다 (P0-8-1).

        Args:
            instrument: 등록할 종목.

        Returns:
            `instruments.id`.

        Note:
            자연키 `(market, symbol)` 충돌 시 표시 정보만 갱신한다 — id 는 `candles` 가
            수천만 번 참조하므로 절대 바뀌면 안 된다.
        """
        statement = sa.text("""
            INSERT INTO instruments (market, symbol, name, asset_type, currency)
            VALUES (:market, :symbol, :name, :asset_type, :currency)
            ON CONFLICT (market, symbol) DO UPDATE SET
                name = EXCLUDED.name,
                asset_type = EXCLUDED.asset_type,
                currency = EXCLUDED.currency
            RETURNING id
        """)
        async with self._session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "market": instrument.market.value,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "asset_type": instrument.asset_type.value,
                    "currency": instrument.currency.value,
                },
            )
            instrument_id = result.scalar_one()
            await session.commit()
        return int(instrument_id)

    async def resolve_instrument(self, market: Market, symbol: str) -> tuple[int, Instrument]:
        """자연키로 종목을 찾는다.

        Args:
            market: 시장.
            symbol: 종목 코드.

        Returns:
            `(id, 도메인 Instrument)`.

        Raises:
            InstrumentNotFoundError: 등록되지 않은 종목.
        """
        statement = sa.text("""
            SELECT id, market, symbol, name, asset_type, currency
            FROM instruments WHERE market = :market AND symbol = :symbol
        """)
        async with self._session_factory() as session:
            row = (
                await session.execute(statement, {"market": market.value, "symbol": symbol})
            ).one_or_none()

        if row is None:
            raise InstrumentNotFoundError(
                f"instruments 에 {market.value}:{symbol} 이 없다 — "
                "`python scripts/runtime/seed_instruments.py` 를 먼저 실행하라"
            )
        return int(row.id), Instrument(
            market=Market(row.market),
            symbol=row.symbol,
            name=row.name,
            asset_type=AssetType(row.asset_type),
            currency=Currency(row.currency),
        )

    # ------------------------------------------------------------------
    # candles
    # ------------------------------------------------------------------

    async def ensure_partitions_for(self, timestamps: Iterable[datetime]) -> list[str]:
        """주어진 시각들이 속한 월 파티션을 보장한다 (plan D-4).

        Args:
            timestamps: 적재할 봉들의 시각.

        Returns:
            실제로 실행한 DDL 목록 (이미 있으면 `IF NOT EXISTS` 로 무해하다).

        Note:
            **DEFAULT 파티션이 없으므로 이 단계를 건너뛰면 INSERT 가 실패한다.**
            파티션 부재는 캔들 적재 전면 중단이라 심각도가 높다 (spec §7).

            월 단위로 중복을 제거해 호출한다 — 10만 봉이면 DDL 이 10만 번 나갈 수 있다.
        """
        months = sorted({month_floor(ts) for ts in timestamps})
        if not months:
            return []

        statements = [
            ddl
            for month in months
            for ddl in ensure_partitions_ddl("candles", month, months_back=0, months_forward=0)
        ]
        async with self._session_factory() as session:
            for ddl in statements:
                await session.execute(sa.text(ddl))
            await session.commit()
        return statements

    async def upsert_candles(self, instrument_id: int, candles: Sequence[Candle]) -> int:
        """캔들을 upsert 한다.

        Args:
            instrument_id: 종목 id.
            candles: 적재할 봉.

        Returns:
            적재를 시도한 행 수.

        Raises:
            RepositoryError: 적재 실패.

        Note:
            반환값은 "시도한 수"이고 신규/갱신을 구분하지 않는다. `DO UPDATE` 는
            `rowcount` 로 둘을 구분해 주지 않으며, 구분이 필요한 곳도 없다 — 중복 없음은
            PK 가 보장하고, 실제 적재량은 커버리지 쿼리로 확인한다 (DoD 1·3).
        """
        if not candles:
            return 0

        await self.ensure_partitions_for(candle.ts for candle in candles)

        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": candle.timeframe.value,
                "ts": candle.ts,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ]

        try:
            async with self._session_factory() as session:
                for start in range(0, len(rows), UPSERT_CHUNK_SIZE):
                    await session.execute(_UPSERT_CANDLES, rows[start : start + UPSERT_CHUNK_SIZE])
                await session.commit()
        except Exception as exc:
            raise RepositoryError(f"캔들 적재 실패(instrument_id={instrument_id}): {exc}") from exc

        return len(rows)

    async def fetch_candles(
        self,
        instrument: Instrument,
        instrument_id: int,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """적재된 캔들을 오름차순으로 읽는다 (무결성 검사 입력).

        Args:
            instrument: 도메인 종목 (반환 객체 구성용).
            instrument_id: 종목 id.
            timeframe: 시간축.
            start: 시작 (포함).
            end: 끝 (포함).

        Returns:
            `ts` 오름차순 캔들.
        """
        statement = sa.text("""
            SELECT ts, open, high, low, close, volume
            FROM candles
            WHERE instrument_id = :instrument_id AND timeframe = :timeframe
              AND ts >= :start AND ts <= :end
            ORDER BY ts
        """)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    statement,
                    {
                        "instrument_id": instrument_id,
                        "timeframe": timeframe.value,
                        "start": start,
                        "end": end,
                    },
                )
            ).all()

        return [
            Candle(
                instrument=instrument,
                timeframe=timeframe,
                ts=row.ts,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        ]

    async def coverage(self) -> list[Coverage]:
        """종목 x TF 별 커버리지를 돌려준다 (P0-8-7 DoD 1).

        Returns:
            적재가 있는 조합만, `symbol`·`timeframe` 순.

        Note:
            `count(*)` 가 파티션 전체를 훑으므로 1년치에서는 몇 초 걸린다. DoD 확인용
            수동 조회이므로 감수한다 — 상시 조회가 필요해지면 집계 테이블을 둔다.
        """
        statement = sa.text("""
            SELECT c.instrument_id, i.symbol, c.timeframe,
                   count(*) AS bars, min(c.ts) AS first_ts, max(c.ts) AS last_ts
            FROM candles c JOIN instruments i ON i.id = c.instrument_id
            GROUP BY c.instrument_id, i.symbol, c.timeframe
            ORDER BY i.symbol, c.timeframe
        """)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()

        return [
            Coverage(
                instrument_id=int(row.instrument_id),
                symbol=row.symbol,
                timeframe=Timeframe(row.timeframe),
                bars=int(row.bars),
                first_ts=row.first_ts,
                last_ts=row.last_ts,
            )
            for row in rows
        ]

    async def last_ts(self, instrument_id: int, timeframe: Timeframe) -> datetime | None:
        """가장 최근 적재 봉의 시각 (주기 수집·재개 기준점).

        Args:
            instrument_id: 종목 id.
            timeframe: 시간축.

        Returns:
            최근 봉 시각. 없으면 None.
        """
        statement = sa.text("""
            SELECT max(ts) FROM candles
            WHERE instrument_id = :instrument_id AND timeframe = :timeframe
        """)
        async with self._session_factory() as session:
            result = await session.execute(
                statement, {"instrument_id": instrument_id, "timeframe": timeframe.value}
            )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 무결성 이슈 (P0-8-5)
    # ------------------------------------------------------------------

    async def list_open_issues(
        self, *, timeframe: Timeframe | None = None, issue_type: QualityIssueType | None = None
    ) -> list[OpenIssue]:
        """열린 무결성 이슈를 읽는다 (판정 CLI 용).

        Args:
            timeframe: 시간축 필터. None 이면 전체.
            issue_type: 종류 필터. None 이면 전체.

        Returns:
            `(종목, 시간축, 구간)` 순으로 정렬된 목록.
        """
        # 조건을 파이썬에서 조립한다. `(:p IS NULL OR col = :p)` 로 쓰면 PostgreSQL 이
        # 파라미터 타입을 추론하지 못해 `could not determine data type` 으로 죽는다 —
        # 캐스팅을 얹어 우회할 수도 있지만, 조건이 실제로 없을 때 그것을 SQL 에 남기지
        # 않는 편이 실행 계획도 읽기도 낫다.
        conditions = ["q.status = 'open'"]
        params: dict[str, str] = {}
        if timeframe is not None:
            conditions.append("q.timeframe = :timeframe")
            params["timeframe"] = timeframe.value
        if issue_type is not None:
            conditions.append("q.issue_type = :issue_type")
            params["issue_type"] = issue_type.value

        statement = sa.text(f"""
            SELECT q.id, q.instrument_id, i.market, i.symbol, i.name,
                   i.asset_type, i.currency, q.timeframe, q.ts_start, q.ts_end,
                   q.issue_type, q.detail_json, q.detected_at
            FROM candle_quality_issues q JOIN instruments i ON i.id = q.instrument_id
            WHERE {" AND ".join(conditions)}
            ORDER BY i.symbol, q.timeframe, q.ts_start
        """)
        async with self._session_factory() as session:
            rows = (await session.execute(statement, params)).all()

        return [
            OpenIssue(
                issue_id=row.id,
                instrument_id=int(row.instrument_id),
                instrument=Instrument(
                    market=Market(row.market),
                    symbol=row.symbol,
                    name=row.name,
                    asset_type=AssetType(row.asset_type),
                    currency=Currency(row.currency),
                ),
                timeframe=Timeframe(row.timeframe),
                ts_start=row.ts_start,
                ts_end=row.ts_end,
                issue_type=QualityIssueType(row.issue_type),
                detail=row.detail_json or {},
                detected_at=row.detected_at,
            )
            for row in rows
        ]

    async def close_issue(
        self, issue_id: uuid.UUID, status: QualityIssueStatus, evidence: JsonDict
    ) -> bool:
        """이슈를 `resolved` 또는 `ignored` 로 내린다.

        Args:
            issue_id: 이슈 id.
            status: 새 상태. `OPEN` 은 허용하지 않는다.
            evidence: 판정 근거. **기존 `detail_json` 에 병합**된다.

        Returns:
            갱신했으면 True. 이미 닫힌 이슈면 False.

        Raises:
            ValueError: `status` 가 `OPEN` 인 경우.
            RepositoryError: 갱신 실패.

        Note:
            **근거를 덮어쓰지 않고 병합한다.** 원래 왜 잡혔는지(`missing_bars` 수 등)와
            왜 내렸는지(재조회 결과)가 **둘 다** 남아야 감사가 성립한다 — 덮어쓰면
            "이 구간이 원래 몇 봉 비었나"를 잃는다 (spec §7).

            `candle_quality_issues` 는 append-only 가 아니다 — 그것은 `event_logs` 와
            `risk_plan_revisions` 뿐이다 (절대 규칙 #8-2). 여기서 UPDATE 는 정상이며,
            상태 전이가 이 테이블의 설계 의도다 (D-14).
        """
        if status is QualityIssueStatus.OPEN:
            raise ValueError("close_issue 는 OPEN 으로 되돌릴 수 없다 — 닫는 전이만 한다")

        # `RETURNING` 을 쓰는 이유: `rowcount` 는 SQLAlchemy `Result` 타입에 노출되지 않고
        # (커서 구현 세부다), 갱신 여부를 값으로 받는 편이 의도가 분명하다.
        statement = sa.text("""
            UPDATE candle_quality_issues
            SET status = :status,
                resolved_at = now(),
                detail_json = detail_json || CAST(:evidence AS JSONB)
            WHERE id = :id AND status = 'open'
            RETURNING id
        """)
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    statement,
                    {
                        "id": issue_id,
                        "status": status.value,
                        "evidence": json.dumps(evidence, ensure_ascii=False, default=repr),
                    },
                )
                updated = result.scalar_one_or_none()
                await session.commit()
        except Exception as exc:
            raise RepositoryError(f"이슈 상태 전이 실패({issue_id}): {exc}") from exc

        changed = updated is not None
        if changed:
            _logger.warning(
                "candle_quality_issue_closed",
                payload={"issue_id": str(issue_id), "status": status.value, "evidence": evidence},
            )
        return changed

    async def open_issue_keys(
        self, instrument_id: int, timeframe: Timeframe
    ) -> set[tuple[datetime, datetime, str]]:
        """열린 이슈의 식별 키 집합.

        Args:
            instrument_id: 종목 id.
            timeframe: 시간축.

        Returns:
            `(ts_start, ts_end, issue_type)` 집합.

        Note:
            중복 억제 판정에 쓴다. `detail_json` 은 키에 넣지 않는다 — 임계값을 바꿔
            재검사하면 근거 수치가 달라지지만 **같은 구간의 같은 위반**이므로 새 이슈가
            아니다.
        """
        statement = sa.text("""
            SELECT ts_start, ts_end, issue_type FROM candle_quality_issues
            WHERE instrument_id = :instrument_id AND timeframe = :timeframe
              AND status = 'open'
        """)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    statement,
                    {"instrument_id": instrument_id, "timeframe": timeframe.value},
                )
            ).all()
        return {(row.ts_start, row.ts_end, str(row.issue_type)) for row in rows}

    async def record_issues(
        self,
        instrument_id: int,
        issues: Sequence[IntegrityIssue],
        *,
        dedupe_open: bool = False,
    ) -> int:
        """무결성 위반 구간을 적재한다 (D-14).

        Args:
            instrument_id: 종목 id.
            issues: 위반 구간.
            dedupe_open: True 면 **이미 열려 있는 같은 구간·같은 종류를 건너뛴다.**
                주기 수집(P0-8-6)이 켠다.

        Returns:
            실제로 적재한 행 수.

        Raises:
            RepositoryError: 적재 실패.

        Note:
            **기본값은 억제 없음**이다. 수동 검사(CLI)는 "언제 어떤 기준으로 잡혔는지"가
            여러 번 남는 편이 임계값 조정에 유용하다 (D-9).

            **주기 수집은 반대다.** 5분마다 같은 구간을 검사하므로, 메울 수 없는 구멍
            하나가 하루 288행을 만든다 — 거래소 자체 부재(§7.1 의 ETH 1h 3봉)가 실제로
            그런 구멍이다. 그래서 `dedupe_open=True` 로 켠다.

            억제 키에 `detail_json` 을 넣지 않는다 — 임계값이 달라 근거 수치가 바뀌어도
            **같은 구간의 같은 위반**은 같은 이슈다.
        """
        if not issues:
            return 0

        pending = list(issues)
        if dedupe_open:
            existing: set[tuple[datetime, datetime, str]] = set()
            for timeframe in {issue.timeframe for issue in pending}:
                existing |= await self.open_issue_keys(instrument_id, timeframe)
            pending = [
                issue
                for issue in pending
                if (issue.ts_start, issue.ts_end, issue.issue_type.value) not in existing
            ]
            if not pending:
                return 0

        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": issue.timeframe.value,
                "ts_start": issue.ts_start,
                "ts_end": issue.ts_end,
                "issue_type": issue.issue_type.value,
                "detail": json.dumps(issue.detail, ensure_ascii=False, default=repr),
            }
            for issue in pending
        ]
        try:
            async with self._session_factory() as session:
                await session.execute(_INSERT_ISSUE, rows)
                await session.commit()
        except Exception as exc:
            raise RepositoryError(f"무결성 이슈 적재 실패: {exc}") from exc

        _logger.warning(
            "candle_quality_issues_recorded",
            payload={
                "instrument_id": instrument_id,
                "issues": len(rows),
                "suppressed": len(issues) - len(rows),
            },
        )
        return len(rows)
