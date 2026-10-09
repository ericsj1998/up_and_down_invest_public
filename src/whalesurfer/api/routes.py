"""WhaleSurfer API — 유명 13F 보고자의 보유 지도 (T442 · 2026-10-08 · 2026-10-09 본체에서 분리).

    GET /whalesurfer/managers            추적 목록(설정 표) · 사진(위키백과 · 저작자 표시)
    GET /whalesurfer/managers/{cik}?n=4  최근 n 분기 — 보유 줄 · 비중 · 직전 분기 대비 변화
    GET /whalesurfer/consensus           추적 보고자 전부 — CUSIP 마다 보유 비율 · 늘림 · 줄임
    GET /whalesurfer/stocks/{cusip}      한 종목 — 보유자 · 변화 · 바로가기 · 그때 샀다면(T443)

라우터는 I/O · 모양 바꾸기 · 캐시만 한다. 13F 읽기는 `whalesurfer.edgar.thirteen_f`,
EDGAR 클라이언트는 본체의 획득 지점 `updown.marketdata.provider.edgar_client`(절대 규칙 #0).
🔴 이 화면은 **분석 · 안내**다 — 주문 경로가 없다. 13F 는 롱 보유만 담고 분기 끝 45일 뒤에
나오므로 "지금 들고 있다" 가 아니라 "그때 들고 있었다" 다.
값은 6시간 기억한다(13F 는 분기마다 한 번 바뀐다). 못 받은 보고자는 `failures` 에 이유와 함께
남는다 — 조용히 빼지 않는다(규칙 #8).

2026-10-09 분리(T442 §1-1 ②): 본체 API 에 붙어 `WHALESURFER_ENABLED` 로 켜고 끄던 것을 걷어냈다 —
이제 이 라우터는 `whalesurfer.api.app` 단독 앱에만 붙는다. 본체(실계좌 서버)는 이 패키지를 모른다
(import-linter 계약 · 배포 이미지 제외).
"""

from __future__ import annotations

import asyncio
import json
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import APIRouter, HTTPException, Query

from updown.common.cache import TtlCache
from updown.common.config import ConfigurationError, Settings
from updown.common.http.outbound import Outbound, OutboundError
from updown.common.logging.setup import get_logger
from updown.marketdata.fundamentals.client import EdgarClient, contact_of
from updown.marketdata.provider import edgar_client
from whalesurfer.edgar.figi import FigiClient
from whalesurfer.edgar.thirteen_f import Holding13F, Report13F, recent_reports
from whalesurfer.portraits import PortraitSource

router = APIRouter(prefix="/whalesurfer", tags=["whalesurfer"])
_logger = get_logger("whalesurfer.api")

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "whalesurfer" / "managers.yml"
CACHE_DIR = ROOT / "cache" / "whalesurfer"
"""파일 캐시(gitignore) — CUSIP → 티커(OpenFIGI) · 인물 사진(위키백과) · T443 실현값."""
REALIZED_PATH = CACHE_DIR / "buyq_realized.json"
"""T443 BUY-Q 사건별 실현값 `{cik: {filed: {cusip: {ticker, entry, ret_q, ret_now, …}}}}`.

`whalesurfer/scripts/t443_backtest.py` 가 쓴다 — 접수일 다음 거래일 시가 진입 · 비용 0.10% 뒤 ·
ret_q = 다음 접수일까지 · ret_now = 백테스트를 돌린 날의 마지막 종가까지."""
REPORT_TTL_S = 6 * 3600.0
PERPS_TTL_S = 6 * 3600.0
BINANCE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_FUTURES_URL = "https://www.binance.com/en/futures/{symbol}"
STOCK_PERP = "TRADIFI_PERPETUAL"
MAX_QUARTERS = 8
CONSENSUS_QUARTERS = 2
PARALLEL = 4
DISCLAIMER = (
    "13F 는 롱 보유만 · 분기 끝 45일 뒤 공개 — 공매도는 보고 항목이 아니다. "
    "공시 뒤에 따라 산 종목은 평균적으로 시장(SPY)과 같았다 — 산 종목과 판 종목의 다음 분기 수익이 "
    "같았다(T443 · 2013 ~ 2026 · 104 보고자 · 사건 154만). 투자 권유가 아니다."
)
ESTIMATE_NOTE = (
    "공시 다음 거래일 시가에 샀다면 — 다음 공시일까지(분기) · 비용 왕복 0.10% 뒤. "
    "상장 폐지 종목은 빠져 있다(실제보다 좋게 보이는 쪽)."
)

_settings: Settings | None = None
_client: EdgarClient | None = None
_binance: Outbound | None = None
_figi: FigiClient | None = None
_portraits: PortraitSource | None = None
_realized: dict[str, Any] | None = None
_REPORTS = TtlCache[dict[str, Any]]("whalesurfer.reports", REPORT_TTL_S)
_CONSENSUS = TtlCache[dict[str, Any]]("whalesurfer.consensus", REPORT_TTL_S)
_PERPS = TtlCache[dict[str, Any]]("whalesurfer.perps", PERPS_TTL_S)
_PORTRAITS = TtlCache[dict[str, Any]]("whalesurfer.portraits", REPORT_TTL_S)


def attach_whalesurfer(settings: Settings | None) -> None:
    """설정을 붙인다 — 단독 앱(`whalesurfer.api.app`)의 기동 훅이 부른다. None 이면 뗀다."""
    global _settings, _client, _figi, _portraits, _realized
    _settings = settings
    _client = None
    _figi = None
    _portraits = None
    _realized = None
    _REPORTS.forget()
    _CONSENSUS.forget()
    _PORTRAITS.forget()


def _realized_table() -> dict[str, Any]:
    """T443 실현값 표(파일) — 없으면 빈 표(화면은 "아직 없음" 이라고 말한다)."""
    global _realized
    if _realized is None:
        try:
            _realized = cast(
                "dict[str, Any]", json.loads(REALIZED_PATH.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            _logger.info("whalesurfer_realized_unavailable", payload={"detail": str(exc)[:80]})
            _realized = {}
    return _realized


def estimate_for(cusip: str, realized: Mapping[str, Any]) -> dict[str, Any] | None:
    """이 종목을 **누군가 사거나 늘린 공시** 뒤에 따라 샀다면 — 사건 전부의 분기 수익 요약(순수).

    Returns:
        ``{events, with_q, mean_q, median_q, win_q}`` 또는 사건이 없으면 None.
        mean · median 은 %(비용 뒤) · win_q 는 0 ~ 1.
    """
    events: list[Mapping[str, Any]] = []
    for by_filed in realized.values():
        for by_cusip in cast("Mapping[str, Any]", by_filed).values():
            ev = cast("Mapping[str, Any]", by_cusip).get(cusip)
            if ev is not None:
                events.append(cast("Mapping[str, Any]", ev))
    if not events:
        return None
    qs = [float(e["ret_q"]) for e in events if e.get("ret_q") is not None]
    if not qs:
        return {"events": len(events), "with_q": 0, "mean_q": None, "median_q": None, "win_q": None}
    return {
        "events": len(events),
        "with_q": len(qs),
        "mean_q": round(statistics.fmean(qs), 2),
        "median_q": round(statistics.median(qs), 2),
        "win_q": round(sum(1 for q in qs if q > 0) / len(qs), 3),
    }


def _client_or_503() -> EdgarClient:
    global _client
    if _client is None:
        if _settings is None:
            raise HTTPException(503, "설정이 안 붙었다 — API 기동 뒤에 다시")
        try:
            _client = edgar_client(_settings)
        except ConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
    return _client


def _figi_client() -> FigiClient:
    global _figi
    if _figi is None:
        _figi = FigiClient(CACHE_DIR / "cusip_figi.json")
    return _figi


def _portrait_source() -> PortraitSource | None:
    """위키백과 초상 출처 — 연락처(EDGAR 선언의 이메일)가 없으면 None(사진 없이 간다)."""
    global _portraits
    if _portraits is None and _settings is not None and _settings.edgar_user_agent:
        _portraits = PortraitSource(
            CACHE_DIR / "portraits.json", contact_of(_settings.edgar_user_agent)
        )
    return _portraits


def load_managers(path: Path = CONFIG_PATH) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """설정 표 → (보고자 목록, CUSIP → 티커).

    Raises:
        HTTPException: 503 표가 없거나 깨짐 — 빈 화면으로 넘어가지 않는다(규칙 #8).
    """
    try:
        raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError) as exc:
        raise HTTPException(503, f"config/whalesurfer/managers.yml 을 못 읽었다: {exc}") from exc
    managers: list[dict[str, Any]] = []
    rows_raw = cast("list[dict[str, Any]]", raw.get("managers") or [])
    for row in rows_raw:
        cik = str(row.get("cik", "")).strip()
        if not cik.isdigit():
            raise HTTPException(503, f"managers.yml 의 CIK 가 숫자가 아니다: {row!r}")
        person = str(row.get("person", ""))
        managers.append(
            {
                "cik": cik.zfill(10),
                "label": str(row.get("label", "")),
                "person": person,
                "image": row.get("image"),
                "image_credit": "수동 적재" if row.get("image") else None,
                "image_page": None,
                # wiki: 위키백과 제목. 없으면 인물 이름 · "" 이면 사진을 찾지 않는다(기관만 있는 줄)
                "wiki": row.get("wiki", person) if "wiki" in row else person,
                "note": row.get("note"),
            }
        )
    ticker_raw = cast("dict[str, Any]", raw.get("cusip_tickers") or {})
    tickers = {str(k).upper(): str(v).upper() for k, v in ticker_raw.items()}
    return managers, tickers


def merge_rows(holdings: Sequence[Holding13F]) -> list[dict[str, Any]]:
    """같은 (CUSIP, Put/Call) 줄을 하나로 합친다 — 가치 · 주 수는 더하고 이름은 첫 줄 것.

    13F 는 한 종목을 운용 주체(otherManager) · 의결권 갈래마다 **여러 줄**에 나눠 적는다
    (버크셔 2026 Q2 애플이 7.8% + 6.0% 두 줄). 합치지 않으면 비중 · 변화 셈이 한 줄만 보고 틀린다.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for h in holdings:
        key = (h.cusip, h.put_call or "")
        slot = merged.get(key)
        if slot is None:
            merged[key] = {
                "cusip": h.cusip,
                "issuer": h.issuer,
                "title": h.title,
                "value_usd": h.value_usd,
                "shares": h.shares,
                "sh_prn": h.sh_prn,
                "put_call": h.put_call,
                "rows": 1,
            }
        else:
            slot["value_usd"] += h.value_usd
            slot["shares"] += h.shares
            slot["rows"] += 1
    return sorted(merged.values(), key=lambda r: r["value_usd"], reverse=True)


def _report_json(report: Report13F) -> dict[str, Any]:
    total = report.total_value_usd or 1
    holdings = merge_rows(report.holdings)
    for h in holdings:
        h["weight"] = round(h["value_usd"] / total, 6)
    return {
        "accession": report.accession,
        "form": report.form,
        "filed": report.filed.isoformat(),
        "period": report.period.isoformat() if report.period else None,
        "entity": report.entity,
        "total_value_usd": report.total_value_usd,
        "n": len(holdings),
        "rows": len(report.holdings),
        "holdings": holdings,
    }


def diff_holdings(now: Mapping[str, Any], prev: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """직전 분기 대비 변화 — 같은 CUSIP · 같은 Put/Call 줄끼리 주 수를 비교한다(순수).

    Returns:
        ``[{cusip, issuer, kind, shares, prev_shares, value_usd, weight}]`` 가치 순 ·
        kind = new · added · reduced · exited · held. prev 가 None 이면 전부 ``held``(비교 불가).
    """

    def key(h: Mapping[str, Any]) -> tuple[str, str]:
        return (str(h["cusip"]), str(h.get("put_call") or ""))

    now_rows = cast("Sequence[Mapping[str, Any]]", now["holdings"])
    prev_rows = cast("Sequence[Mapping[str, Any]]", prev["holdings"] if prev else ())
    cur: dict[tuple[str, str], Mapping[str, Any]] = {key(h): h for h in now_rows}
    old: dict[tuple[str, str], Mapping[str, Any]] = {key(h): h for h in prev_rows}
    out: list[dict[str, Any]] = []
    for k, h in cur.items():
        p = old.get(k)
        if prev is None:
            kind = "held"
        elif p is None:
            kind = "new"
        elif h["shares"] > p["shares"]:
            kind = "added"
        elif h["shares"] < p["shares"]:
            kind = "reduced"
        else:
            kind = "held"
        out.append(
            {
                "cusip": h["cusip"],
                "issuer": h["issuer"],
                "put_call": h.get("put_call"),
                "kind": kind,
                "shares": h["shares"],
                "prev_shares": p["shares"] if p else 0,
                "value_usd": h["value_usd"],
                "weight": h["weight"],
            }
        )
    for k, p in old.items():
        if k not in cur:
            out.append(
                {
                    "cusip": p["cusip"],
                    "issuer": p["issuer"],
                    "put_call": p.get("put_call"),
                    "kind": "exited",
                    "shares": 0,
                    "prev_shares": p["shares"],
                    "value_usd": 0,
                    "weight": 0.0,
                }
            )
    return sorted(out, key=lambda r: (r["value_usd"], r["prev_shares"]), reverse=True)


async def _reports_of(cik: str, n: int) -> dict[str, Any]:
    client = _client_or_503()

    async def _build() -> dict[str, Any]:
        reports = await recent_reports(client, cik, n)
        return {"cik": cik, "reports": [_report_json(r) for r in reports]}

    return await _REPORTS.get_or_fetch(f"{cik}:{n}", _build)


async def _perps() -> dict[str, Any]:
    """바이낸스 주식형 무기한(TRADIFI_PERPETUAL) 심볼 — 바로가기 단추용. 못 받으면 빈 목록 + 이유."""  # noqa: E501

    async def _build() -> dict[str, Any]:
        global _binance
        if _binance is None:
            _binance = Outbound(
                "BINANCE_PUBLIC", timeout=20.0, headers={"Accept": "application/json"}
            )
        try:
            body = cast("dict[str, Any]", await _binance.get_json(BINANCE_INFO_URL))
        except (OutboundError, ValueError) as exc:
            _logger.info("whalesurfer_perps_unavailable", payload={"detail": str(exc)[:120]})
            return {"symbols": [], "failure": f"바이낸스 exchangeInfo 실패: {str(exc)[:80]}"}
        rows = cast("list[dict[str, Any]]", body.get("symbols") or [])
        symbols = sorted(
            str(s.get("symbol"))
            for s in rows
            if s.get("contractType") == STOCK_PERP and s.get("quoteAsset") == "USDT"
        )
        return {"symbols": symbols, "failure": None}

    return await _PERPS.get_or_fetch("*", _build)


def links_for(ticker: str | None, perp_symbols: Sequence[str]) -> dict[str, str | None]:
    """바로가기 — 있으면 URL, 없으면 None(화면은 회색 비활성).

    토스 종목 딥링크 · 게이트 주식 무기한은 공개 형식을 확인하지 못했다(T442 §5 [결정 필요]) → None.
    """
    binance = None
    if ticker and f"{ticker}USDT" in set(perp_symbols):
        binance = BINANCE_FUTURES_URL.format(symbol=f"{ticker}USDT")
    return {"toss": None, "binance": binance, "gate": None}


async def _with_portraits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`image` 가 없고 `wiki` 가 비어 있지 않은 보고자에 위키백과 초상을 붙인다.

    파일 캐시 + 6시간 메모리 캐시.
    """
    source = _portrait_source()
    if source is None:
        return rows

    async def _build() -> dict[str, Any]:
        sem = asyncio.Semaphore(PARALLEL)

        async def one(m: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            if m.get("image") or not m.get("wiki"):
                return m["cik"], None
            async with sem:
                return m["cik"], await source.portrait(str(m["wiki"]))

        got = await asyncio.gather(*(one(m) for m in rows))
        return {cik: p for cik, p in got if p}

    found = await _PORTRAITS.get_or_fetch("*", _build)
    out: list[dict[str, Any]] = []
    for m in rows:
        p = found.get(m["cik"])
        if p and p.get("image"):
            m = {
                **m,
                "image": p["image"],
                "image_credit": p.get("credit"),
                "image_page": p.get("page"),
            }
        out.append(m)
    return out


@router.get("/managers")
async def managers() -> dict[str, Any]:
    """추적 목록 — 설정 표 + 위키백과 초상(저작자 표시)."""
    rows, tickers = load_managers()
    rows = await _with_portraits(rows)
    return {
        "managers": rows,
        "count": len(rows),
        "cusip_tickers": len(tickers),
        "disclaimer": DISCLAIMER,
        "config": str(CONFIG_PATH.relative_to(CONFIG_PATH.parents[2])),
    }


@router.get("/managers/{cik}")
async def manager(cik: str, n: int = Query(4, ge=1, le=MAX_QUARTERS)) -> dict[str, Any]:
    """한 보고자의 최근 n 분기 — 보고마다 보유 줄 · 직전 분기 대비 변화."""
    rows, _tickers = load_managers()
    meta = next((m for m in rows if m["cik"] == cik.zfill(10)), None)
    if meta is None:
        raise HTTPException(404, f"추적 목록에 없는 CIK: {cik}")
    got = await _reports_of(meta["cik"], n)
    reports = cast("list[dict[str, Any]]", got["reports"])
    out: list[dict[str, Any]] = []
    for i, rep in enumerate(reports):
        prev = reports[i + 1] if i + 1 < len(reports) else None
        out.append({**rep, "changes": diff_holdings(rep, prev)})
    return {"manager": meta, "reports": out, "disclaimer": DISCLAIMER}


async def _consensus_build() -> dict[str, Any]:
    rows, _tickers = load_managers()
    sem = asyncio.Semaphore(PARALLEL)
    failures: list[dict[str, str]] = []

    async def one(m: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
        async with sem:
            try:
                got = await _reports_of(m["cik"], CONSENSUS_QUARTERS)
                return m, got["reports"]
            except Exception as exc:
                failures.append({"cik": m["cik"], "label": m["label"], "reason": str(exc)[:160]})
                return m, None

    results = await asyncio.gather(*(one(m) for m in rows))
    by_cusip: dict[str, dict[str, Any]] = {}
    counted = 0
    for _m, reports in results:
        if not reports:
            continue
        counted += 1
        latest = reports[0]
        prev = reports[1] if len(reports) > 1 else None
        for ch in diff_holdings(latest, prev):
            if ch.get("put_call"):
                continue  # 비중 집계는 현물 줄만 — Put/Call 은 종목 상세에 따로 보인다
            slot = by_cusip.setdefault(
                ch["cusip"],
                {
                    "cusip": ch["cusip"],
                    "issuer": ch["issuer"],
                    "holders": 0,
                    "new": 0,
                    "added": 0,
                    "reduced": 0,
                    "exited": 0,
                    "held": 0,
                    "value_usd": 0,
                },
            )
            slot[ch["kind"]] += 1
            if ch["kind"] != "exited":
                slot["holders"] += 1
                slot["value_usd"] += int(ch["value_usd"])
    for slot in by_cusip.values():
        slot["share"] = round(slot["holders"] / counted, 4) if counted else 0.0
    return {
        "at": datetime.now(UTC).isoformat(),
        "managers": counted,
        "tracked": len(rows),
        "failures": failures,
        "by_cusip": by_cusip,
    }


@router.get("/consensus")
async def consensus() -> dict[str, Any]:
    """추적 보고자 전부 — CUSIP 마다 몇 명이 들고 있고(비율) 직전 분기에 몇 명이 늘리고 줄였나.

    처음엔 느리다(보고자 x 2분기 x 요청 2 · 초당 10 요청) — 서버가 6시간 기억한다.
    """
    _client_or_503()
    return await _CONSENSUS.get_or_fetch("*", _consensus_build)


@router.get("/stocks/{cusip}")
async def stock(cusip: str) -> dict[str, Any]:
    """한 종목 — 누가 들고 있고 누가 늘리고 줄였나 · 바로가기 · 그때 샀다면(T443 실현값)."""
    rows, tickers = load_managers()
    key = cusip.strip().upper()
    cons = await consensus()
    slot = cons["by_cusip"].get(key)
    realized = _realized_table()
    holders: list[dict[str, Any]] = []
    for m in rows:
        try:
            got = await _reports_of(m["cik"], CONSENSUS_QUARTERS)
        except HTTPException:
            raise
        except Exception:
            continue
        reports = got["reports"]
        if not reports:
            continue
        prev = reports[1] if len(reports) > 1 else None
        for ch in diff_holdings(reports[0], prev):
            if ch["cusip"] == key:
                # 이번 공시가 현물 신규 · 늘림이면 — 그 공시 뒤에 따라 샀다면(T443 사건 실현값).
                # T443 사건은 현물 줄만 셌다 — Put/Call 줄 · 줄임 줄에 붙이면 남의 값을 보인다.
                since = None
                if ch["kind"] in ("new", "added") and not ch.get("put_call"):
                    since = (
                        cast("Mapping[str, Any]", realized.get(m["cik"]) or {})
                        .get(str(reports[0]["filed"]), {})
                        .get(key)
                    )
                holders.append(
                    {
                        **ch,
                        "cik": m["cik"],
                        "label": m["label"],
                        "person": m["person"],
                        "period": reports[0]["period"],
                        "filed": reports[0]["filed"],
                        "since_filing": since,
                    }
                )
    holders.sort(key=lambda r: r["value_usd"], reverse=True)
    perps = await _perps()
    ticker = tickers.get(key)
    ticker_source = "config/whalesurfer/managers.yml cusip_tickers(수동)" if ticker else None
    if ticker is None:
        # D3(사용자 "키 없이 쓰자") — OpenFIGI 로 찾고 파일에 캐시한다. 실패해도 종목 상세는 뜬다.
        try:
            got = await _figi_client().map_cusips([key])
            ticker = cast("str | None", got.get(key, {}).get("ticker"))
            ticker_source = "OpenFIGI" if ticker else None
        except Exception as exc:
            _logger.info(
                "whalesurfer_figi_unavailable", payload={"cusip": key, "detail": str(exc)[:80]}
            )
    issuer = slot["issuer"] if slot else (holders[0]["issuer"] if holders else None)
    return {
        "cusip": key,
        "issuer": issuer,
        "ticker": ticker,
        "ticker_source": ticker_source,
        "consensus": slot,
        "holders": holders,
        "links": links_for(ticker, perps["symbols"]),
        "links_note": perps["failure"],
        "estimate": estimate_for(key, realized),
        "estimate_note": ESTIMATE_NOTE
        if realized
        else "T443 실현값 파일이 없다 — whalesurfer/scripts/t443_backtest.py 를 돌리면 채워진다",
        "failures": cons["failures"],
        "disclaimer": DISCLAIMER,
    }


__all__ = [
    "attach_whalesurfer",
    "diff_holdings",
    "estimate_for",
    "links_for",
    "load_managers",
    "merge_rows",
    "router",
]
