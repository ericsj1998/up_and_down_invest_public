# ruff: noqa: E501 — 사람이 읽는 안내 문장이 많다(fix_fund_manual_1015.py 와 같은 면제)
"""T607 — 계좌 분리 · 옛 펀드 보관 · 새 펀드 새 출발 (준비 2026-11-02 · 실행은 사용자가 말할 때).

사용자 결정(11-02): 옛 펀드는 **보관**(archive · 지우지 않음) · 새 펀드의 시작 금액 = **만드는 시점의 지갑
총액** · 매매법 선언은 지금 실계좌 혼합 4.4(`private_strategy`) 그대로 · 멤버 40.

## 단계 (`--step N` 하나씩 · 돈이 움직이는 1 ~ 3 은 `--apply` 가 없으면 **계산만**)

    0  사전 점검(읽기만) — 리더 · 포지션 0 · 대기 주문 0 · 조건부 0 · 전 판 정지(auto 전부 꺼짐) ·
       지갑 총액 · 지금 펀드(id · 매매법 · 종목 40) · 펀드 밖 판 → `logs/t607/state.json` 에 적는다
    1  옛 펀드 보관 — `DELETE /rebalancer/{id}` (세션 거둠 · 정의 파일은 archive/ 로 · wf_runs · wf_trades 는 남는다)
    2  펀드 밖 판 접기 — `DELETE /walkforward/sessions/{key}` (GATE 라이브 판 중 어느 펀드에도 안 묶인 것)
    3  새 펀드 — `POST /rebalancer` {label · total_cash = 지갑 총액 · playbook · market GATE · members 40}
       (배율 · 자리 · 브레이크는 보내지 않는다 — 선언이 유일한 출처)
    4  뒤 점검(읽기만) — GATE 펀드 1 · 종목 40 · 묶인 판 = 열린 판 · 첫 앵커 account · 앵커 건너뜀 없음

## 왜 HTTP 인가

펀드 · 판은 리더 api **프로세스 메모리**에 산다. 컨테이너 안에서 파이썬으로 모듈을 불러도 다른 프로세스라
아무것도 안 바뀐다. 돈이 움직이는 길은 설계상 **구글 로그인 쿠키 + 최근 인증(1 시간)** 뿐이다(개인 토큰은
읽기 전용 · 실계좌는 시험 우회 없음). 그래서 이 스크립트는 사람이 브라우저에서 꺼낸 `updown_session` 쿠키
값을 **파일로** 받아 그 사람으로 API 를 부른다. 쿠키는 어디에도 찍지 않는다.

## 어디서 돌리나

  · 로컬(WSL · 쿠키가 있는 PC · 기본): `uv run python scripts/ops/fresh_start_t607.py --step 0`
      - 주소 `https://<PUBLIC_DOMAIN>/api` (scripts/ops/host.env) · 쿠키 `logs/t607/session`(logs/* 비추적)
  · 서버 api 컨테이너 안: `bash scripts/ops/remote.sh scripts/ops/fresh_start_t607.py --step 0`
      - 주소 `http://127.0.0.1:8000` · 쿠키 `/tmp/t607_session` (컨테이너 안에 사람이 넣는다)

## 멱등

두 번 돌려도 안전하다 — 1 은 그 펀드가 이미 없으면 건너뛰고, 2 는 펀드 밖 판이 없으면 건너뛰고, 3 은 같은
이름의 펀드가 있으면 건너뛴다(GATE 에 다른 펀드가 있으면 **거절** — 앵커가 멈춘다 · T606). 실행 전 로그를
먼저 적고, 조용히 넘기는 분기는 없다(규칙 #8).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IN_CONTAINER = Path("/app/logs").is_dir() and not (ROOT / "pyproject.toml").exists()
DEFAULT_PLAYBOOK = "private_strategy"
"""지금 실계좌 혼합 4.4 묶음 선언 id — 0 단계가 옛 펀드에서 읽은 값과 다르면 멈춘다."""
DEFAULT_LABEL = "새 펀드 · 계좌 분리(T607)"
DEFAULT_MEMBERS = 40
MARKET = "GATE"
READ_TIMEOUT_S = 90
CREATE_TIMEOUT_S = 900
"""펀드 생성은 종목당 수 초 ~ 15 초(느린 서버) x 39 — 시한을 넘기면 목록을 폴링해 결과를 찾는다."""
PACE_S = 2.0
"""거래 요청 사이 간격 — 사람당 분당 30 회 상한(`TRADE_RATE_MAX`)을 멀찍이 피한다."""
COOKIE_NAME = "updown_session"
UNBOUND_RE = re.compile(r"펀드가 묶은 판이 열린 판이 아님 \d+종\((.*)\)")
SYMBOL_RE = re.compile(r"([A-Z0-9]+_[A-Z]+)\(")


class StepError(RuntimeError):
    """단계를 멈추는 사유 — 메시지가 곧 사람에게 보이는 줄이다."""


class Api(Protocol):
    """API 호출 — 시험은 가짜로 바꿔 끼운다."""

    def get(self, path: str) -> Any: ...

    def post(
        self, path: str, body: Mapping[str, Any], *, timeout: float = READ_TIMEOUT_S
    ) -> Any: ...

    def delete(self, path: str) -> Any: ...


class HttpApi:
    """urllib 로 리더 api 를 부른다 — 쿠키는 머리에만 실리고 어디에도 찍히지 않는다."""

    def __init__(self, base_url: str, cookie: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._cookie = cookie

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None, timeout: float) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Cookie", f"{COOKIE_NAME}={self._cookie}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:400]
            hint = ""
            if exc.code == 401:
                hint = " — 로그인 또는 재인증이 필요하다: 브라우저에서 /auth/login?force=1 뒤 쿠키를 다시 복사"
            elif exc.code == 403:
                hint = " — 권한(거래 · 삭제 · 매매법 · 시장) 또는 개인 토큰(읽기 전용)"
            elif exc.code == 503:
                hint = " — 거래 리더가 아니거나 거래소 응답 실패 · 잠시 뒤 다시"
            raise StepError(f"{method} {path} → {exc.code}{hint} · 본문 {text}") from exc
        return json.loads(raw.decode("utf-8")) if raw else None

    def get(self, path: str) -> Any:
        return self._call("GET", path, None, READ_TIMEOUT_S)

    def post(self, path: str, body: Mapping[str, Any], *, timeout: float = READ_TIMEOUT_S) -> Any:
        return self._call("POST", path, body, timeout)

    def delete(self, path: str) -> Any:
        return self._call("DELETE", path, None, READ_TIMEOUT_S)


# ── 순수 함수(시험 대상) ────────────────────────────────────────────────────


def kst(now: datetime | None = None) -> str:
    """보고용 시각 — KST."""
    at = now or datetime.now(UTC)
    return (at + timedelta(hours=9)).strftime("%m-%d %H:%M:%S KST")


def floor_cents(value: Decimal) -> Decimal:
    """센트 아래를 버린다 — 시작 자본이 지갑을 한 푼도 넘지 않게."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def gate_funds(funds_body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`GET /rebalancer` 응답에서 GATE 펀드만."""
    rows = funds_body.get("funds") or []
    return [f for f in rows if str(f.get("market")) == MARKET]


def fund_handles(fund: Mapping[str, Any]) -> set[str]:
    """펀드가 묶은 판 키(엔진 다리 · 빠진 판 제외)."""
    per = fund.get("per_symbol") or {}
    out: set[str] = set()
    for row in per.values():
        if not isinstance(row, dict):
            continue
        handle = row.get("handle")
        if handle:
            out.add(str(handle))
    return out


def live_gate_sessions(sessions_body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`GET /walkforward/sessions` 에서 GATE 라이브 판(메모리에 살아 있는 것만)."""
    rows = sessions_body.get("sessions") or []
    return [
        s
        for s in rows
        if s.get("live") and str(s.get("market")) == MARKET and not s.get("stored", False)
    ]


def strangers_of(
    sessions_body: Mapping[str, Any], funds_body: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """어느 GATE 펀드에도 안 묶인 GATE 라이브 판 — 앵커가 "펀드 밖 단독 판" 으로 보는 것과 같은 정의."""
    bound: set[str] = set()
    for fund in gate_funds(funds_body):
        bound |= fund_handles(fund)
    return [s for s in live_gate_sessions(sessions_body) if str(s.get("session_id")) not in bound]


def expected_unbound_from_check(check_json_text: str) -> list[str] | None:
    """로컬 라이브 점검(check.json)의 "펀드가 묶은 판이 열린 판이 아님 N종(…)" 에서 종목만.

    Returns:
        종목 목록(정렬). 그 문구가 없으면 None — 기대치 없음(0 종이라는 뜻이 아니다).
    """
    found = UNBOUND_RE.search(check_json_text)
    if found is None:
        return None
    return sorted(set(SYMBOL_RE.findall(found.group(1))))


def exchange_facts(state: Mapping[str, Any]) -> dict[str, Any]:
    """`GET /exchange/state` 에서 점검에 쓰는 네 값 — 총액 · 포지션 · 미결 주문 · 조건부."""
    balance = state.get("balance") or {}
    total_raw = balance.get("total")
    total = None if total_raw in (None, "") else Decimal(str(total_raw))
    positions = [
        p for p in state.get("positions") or [] if str(p.get("size", "0")) not in ("0", "0.0", "")
    ]
    return {
        "total": total,
        "positions": positions,
        "orders": list(state.get("orders") or []),
        "stops": list(state.get("stops") or []),
    }


def payload_for(
    *, label: str, total_cash: Decimal, playbook: str, members: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """`POST /rebalancer` 본문 — 배율 · 자리 · 브레이크는 **보내지 않는다**(선언이 유일한 출처)."""
    return {
        "label": label,
        "total_cash": str(floor_cents(total_cash)),
        "playbook": playbook,
        "market": MARKET,
        "members": [{"symbol": str(m["symbol"]), "weight": str(m["weight"])} for m in members],
    }


@dataclass
class Check:
    """뒤 점검 한 줄."""

    ok: bool
    what: str
    value: str

    def line(self) -> str:
        return f"{'✅' if self.ok else '⛔'} {self.what} — {self.value}"


def verify_after(
    funds_body: Mapping[str, Any],
    sessions_body: Mapping[str, Any],
    *,
    label: str,
    playbook: str,
    members: int,
    wallet_total: Decimal | None,
) -> list[Check]:
    """4 단계 — 새 펀드 하나 · 종목 수 · 묶인 판 = 열린 판 · 첫 앵커 · 장부 ≈ 지갑."""
    funds = gate_funds(funds_body)
    out: list[Check] = [Check(len(funds) == 1, "GATE 펀드 수", f"{len(funds)} (기준 1)")]
    if len(funds) != 1:
        return out
    fund = funds[0]
    out.append(Check(str(fund.get("label")) == label, "이름", str(fund.get("label"))))
    out.append(
        Check(str(fund.get("playbook")) == playbook, "매매법 선언", str(fund.get("playbook")))
    )
    symbols = list(fund.get("symbols") or [])
    out.append(Check(len(symbols) == members, "종목 수", f"{len(symbols)} (기준 {members})"))
    per = fund.get("per_symbol") or {}
    missing = sorted(
        str(s) for s, row in per.items() if isinstance(row, dict) and row.get("missing")
    )
    out.append(Check(not missing, "빠진 판(묶었는데 없음)", ", ".join(missing) or "없음"))
    handles = fund_handles(fund)
    live = {str(s.get("session_id")) for s in live_gate_sessions(sessions_body)}
    strangers = sorted(live - handles)
    out.append(Check(not strangers, "펀드 밖 판", f"{len(strangers)}" if strangers else "0"))
    dead = sorted(handles - live)
    out.append(Check(not dead, "묶었는데 열려 있지 않은 판", f"{len(dead)}" if dead else "0"))
    anchor = fund.get("anchor") or {}
    mode = anchor.get("mode")
    skipped = anchor.get("skipped")
    out.append(Check(mode == "account", "첫 앵커 모드", f"{mode} (기준 account)"))
    out.append(
        Check(skipped in (None, ""), "앵커 건너뜀(fund_anchor_skipped)", str(skipped or "없음"))
    )
    balance_raw = fund.get("balance")
    if wallet_total is not None and balance_raw not in (None, ""):
        diff = Decimal(str(balance_raw)) - wallet_total
        out.append(
            Check(
                abs(diff) <= Decimal("0.5"),
                "장부 ↔ 지갑",
                f"{balance_raw} ↔ {wallet_total} · 차 {diff:+.2f}",
            )
        )
    autos = [bool(s.get("auto")) for s in live_gate_sessions(sessions_body)]
    on = sum(autos)
    out.append(
        Check(
            True, "새 진입 켜진 판(auto)", f"{on}/{len(autos)} — 정지 상태로 시작했으면 0 이 정상"
        )
    )
    return out


# ── 실행기 ─────────────────────────────────────────────────────────────────


@dataclass
class Runner:
    """단계를 돈다 — 모든 행동은 로그를 먼저 적고(`say`) 그 다음 API 를 부른다."""

    api: Api
    dry_run: bool
    state_path: Path
    say: Callable[[str], None]
    label: str = DEFAULT_LABEL
    playbook: str | None = None
    members: int = DEFAULT_MEMBERS
    expected_unbound: list[str] | None = None
    force: bool = False
    sleep: Callable[[float], None] = time.sleep
    state: dict[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise StepError(
                    f"상태 파일이 깨졌다: {self.state_path} — 0 단계를 다시 돌린다"
                ) from exc

    # 공통 읽기
    def _funds(self) -> dict[str, Any]:
        return self.api.get("/rebalancer") or {}

    def _sessions(self) -> dict[str, Any]:
        return self.api.get("/walkforward/sessions") or {}

    def _exchange(self) -> dict[str, Any]:
        return exchange_facts(
            self.api.get(f"/exchange/state?symbol=BTC_USDT&market={MARKET}") or {}
        )

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def _book(self) -> str:
        book = self.playbook or str(self.state.get("playbook") or "")
        if not book:
            raise StepError("매매법 선언 id 를 모른다 — 0 단계를 먼저 돌리거나 --playbook 을 준다")
        return book

    def _require_flat(self, facts: Mapping[str, Any]) -> None:
        """포지션 · 미결 · 조건부가 하나라도 있으면 멈춘다 — 접기가 시장가 청산이 되게 두지 않는다."""
        if facts["positions"] or facts["orders"] or facts["stops"]:
            raise StepError(
                f"거래소가 비어 있지 않다 — 포지션 {len(facts['positions'])} · 미결 {len(facts['orders'])} · "
                f"조건부 {len(facts['stops'])}. 사람이 먼저 정리한다(런북)"
            )

    # 0 ───────────────────────────────────────────────
    def step0(self) -> None:
        self.say("0 단계 · 사전 점검(읽기만)")
        health = self.api.get("/health") or {}
        leader = health.get("trading_leader")
        self.say(f"  리더 trading_leader={leader} · 서버 v{health.get('version')}")
        facts = self._exchange()
        self.say(
            f"  거래소 — 총액 {facts['total']} USDT · 포지션 {len(facts['positions'])} · "
            f"미결 {len(facts['orders'])} · 조건부 {len(facts['stops'])}"
        )
        funds_body, sessions_body = self._funds(), self._sessions()
        funds = gate_funds(funds_body)
        for f in funds:
            anchor = f.get("anchor") or {}
            self.say(
                f"  펀드 {f.get('fund_id')} '{f.get('label')}' · 매매법 {f.get('playbook')} · "
                f"종목 {len(f.get('symbols') or [])} · 장부 {f.get('balance')} · 앵커 {anchor.get('mode')} · "
                f"건너뜀 {anchor.get('skipped') or '없음'}"
            )
        live = live_gate_sessions(sessions_body)
        autos = sum(bool(s.get("auto")) for s in live)
        strangers = strangers_of(sessions_body, funds_body)
        self.say(
            f"  GATE 라이브 판 {len(live)} · 새 진입 켜진 판 {autos} · 펀드 밖 판 {len(strangers)}"
            + (
                f" ({' '.join(sorted(str(s.get('symbol')) for s in strangers))})"
                if strangers
                else ""
            )
        )
        if self.expected_unbound is not None:
            got = sorted(str(s.get("symbol")) for s in strangers)
            mark = "같다" if got == self.expected_unbound else "⚠️ 다르다"
            self.say(
                f"  check.json 기대(펀드 밖) {len(self.expected_unbound)}종 ↔ 지금 {len(got)}종 — {mark}"
            )
        problems: list[str] = []
        if leader is not True:
            problems.append("거래 리더가 아니다")
        if facts["positions"] or facts["orders"] or facts["stops"]:
            problems.append("거래소에 포지션 · 미결 · 조건부가 남아 있다")
        if autos:
            problems.append(
                f"새 진입이 켜진 판 {autos} — 전 판 정지(화면 '전부 정지' · set_live_entries_halted.sh) 먼저"
            )
        if facts["total"] is None or facts["total"] <= 0:
            problems.append("지갑 총액을 못 읽었거나 0 이다")
        if len(funds) != 1:
            problems.append(f"GATE 펀드가 {len(funds)}개 — 1 단계는 --old-fund 로 id 를 준다")
        old = funds[0] if len(funds) == 1 else None
        book = str(old.get("playbook")) if old else ""
        if old and book != DEFAULT_PLAYBOOK:
            problems.append(
                f"옛 펀드 매매법 {book} ≠ 기본값 {DEFAULT_PLAYBOOK} — 3 단계는 --playbook 을 명시한다"
            )
        self.state = {
            "at": datetime.now(UTC).isoformat(),
            "old_fund": None if old is None else str(old.get("fund_id")),
            "playbook": book or None,
            "symbols": sorted(str(s) for s in (old.get("symbols") or [])) if old else [],
            "wallet_total": None if facts["total"] is None else str(facts["total"]),
            "strangers": sorted(str(s.get("symbol")) for s in strangers),
            "problems": problems,
        }
        self._save_state()
        self.say(f"  상태 기록 {self.state_path}")
        if problems:
            self.say("  ⚠️ 1 ~ 3 단계 전에 풀 것: " + " · ".join(problems))
        else:
            self.say("  ✅ 사전 점검 통과 — 1 단계로")

    # 1 ───────────────────────────────────────────────
    def step1(self, old_fund: str | None) -> None:
        target = old_fund or self.state.get("old_fund")
        if not target:
            raise StepError("접을 펀드 id 가 없다 — --old-fund 또는 0 단계 기록")
        self.say(f"1 단계 · 옛 펀드 보관(archive) — {target}")
        funds = {str(f.get("fund_id")): f for f in gate_funds(self._funds())}
        found = funds.get(str(target))
        if found is None:
            self.say("  이미 없다(보관됐거나 id 가 다르다) — 건너뜀")
            return
        if str(found.get("label")) == self.label:
            raise StepError(
                f"{target} 은 새 펀드 이름('{self.label}')이다 — 옛 펀드가 아니다. 멈춘다"
            )
        self._require_flat(self._exchange())
        handles = fund_handles(found)
        self.say(
            f"  '{found.get('label')}' · 매매법 {found.get('playbook')} · 묶인 판 {len(handles)} · "
            f"장부 {found.get('balance')} → DELETE /rebalancer/{target}"
        )
        self.say(
            "  보관 뒤 정의 파일은 logs/funds/archive/ 에 남고 wf_runs · wf_trades 는 옛 펀드 id 로 남는다"
        )
        if self.dry_run:
            self.say("  [계산만] 보내지 않았다 · --apply 로 실행")
            return
        body = self.api.delete(f"/rebalancer/{target}") or {}
        self.say(
            f"  보관됨 dropped={body.get('dropped')} · 거둔 세션 {len(body.get('sessions') or [])}"
        )

    # 2 ───────────────────────────────────────────────
    def step2(self) -> None:
        self.say("2 단계 · 펀드 밖 판 접기")
        funds_body, sessions_body = self._funds(), self._sessions()
        strangers = strangers_of(sessions_body, funds_body)
        if not strangers:
            self.say("  펀드 밖 판 없음 — 건너뜀")
            return
        symbols = sorted(str(s.get("symbol")) for s in strangers)
        self.say(f"  {len(strangers)}개 — {' '.join(symbols)}")
        if gate_funds(funds_body):
            # 옛 펀드가 아직 있으면 "펀드 밖" 은 반쪽 전환 잔재 8 판이어야 한다 — check.json 과 맞춰 본다.
            if self.expected_unbound is None and not self.force:
                raise StepError(
                    "기대 목록(check.json)이 없다 — make live-check 뒤 다시, 또는 --force"
                )
            if (
                self.expected_unbound is not None
                and symbols != self.expected_unbound
                and not self.force
            ):
                raise StepError(
                    f"check.json 기대 {self.expected_unbound} 와 다르다 {symbols} — 라이브 점검을 새로 뜨고 다시, 또는 --force"
                )
        self._require_flat(self._exchange())
        for s in strangers:
            key = str(s.get("session_id"))
            self.say(
                f"  DELETE /walkforward/sessions/{key} ({s.get('symbol')} · 거래 {s.get('trades')})"
            )
            if self.dry_run:
                continue
            body = self.api.delete(f"/walkforward/sessions/{key}") or {}
            if body.get("closed"):
                self.say(
                    f"    ⚠️ 포지션을 닫았다: {json.dumps(body['closed'], ensure_ascii=False)[:200]}"
                )
            self.sleep(PACE_S)
        if self.dry_run:
            self.say("  [계산만] 보내지 않았다 · --apply 로 실행")
            return
        left = strangers_of(self._sessions(), self._funds())
        if left:
            raise StepError(
                f"접은 뒤에도 펀드 밖 판 {len(left)}개가 남았다 — 다시 돌리거나 로그를 본다"
            )
        self.say("  ✅ 펀드 밖 판 0")

    # 3 ───────────────────────────────────────────────
    def step3(self) -> None:
        book = self._book()
        self.say(f"3 단계 · 새 펀드 — '{self.label}' · 매매법 {book} · 멤버 {self.members}")
        funds_body, sessions_body = self._funds(), self._sessions()
        funds = gate_funds(funds_body)
        if any(str(f.get("label")) == self.label for f in funds):
            self.say("  같은 이름의 펀드가 이미 있다 — 건너뜀(4 단계로)")
            return
        if funds:
            raise StepError(
                f"GATE 에 펀드 {len(funds)}개가 있다({', '.join(str(f.get('fund_id')) for f in funds)}) — "
                "새 펀드의 앵커가 멈춘다(T606). 1 단계 먼저"
            )
        strangers = strangers_of(sessions_body, funds_body)
        if strangers:
            raise StepError(f"펀드 밖 판 {len(strangers)}개 — 2 단계 먼저")
        facts = self._exchange()
        self._require_flat(facts)
        total = facts["total"]
        if total is None or total <= 0:
            raise StepError(f"지갑 총액을 못 읽었거나 0 이다: {total}")
        defaults = self.api.get(f"/rebalancer/defaults?market={MARKET}&playbook={book}") or {}
        if str(defaults.get("playbook")) != book:
            raise StepError(f"서버가 아는 매매법이 다르다: {defaults.get('playbook')} ≠ {book}")
        members = list(defaults.get("members") or [])
        if defaults.get("missing"):
            raise StepError(
                f"이 거래소에서 빠지는 종목이 있다: {defaults.get('missing')} — 실계좌면 있을 수 없다"
            )
        if len(members) != self.members:
            raise StepError(f"멤버 수 {len(members)} ≠ 기대 {self.members}")
        want = set(self.state.get("symbols") or [])
        have = {str(m["symbol"]) for m in members}
        if want and want != have and not self.force:
            raise StepError(
                f"옛 펀드 종목과 다르다 — 빠짐 {sorted(want - have)} · 더함 {sorted(have - want)} (--force 로 강행)"
            )
        payload = payload_for(label=self.label, total_cash=total, playbook=book, members=members)
        self.say(
            f"  POST /rebalancer — total_cash {payload['total_cash']} (지갑 {total}) · 종목 {len(members)} · "
            "배율 · 자리 · 브레이크는 선언에서"
        )
        if self.dry_run:
            self.say("  [계산만] 보내지 않았다 · --apply 로 실행")
            return
        self.state["new_total_cash"] = payload["total_cash"]
        self._save_state()
        made: dict[str, Any] | None
        try:
            made = self.api.post("/rebalancer", payload, timeout=CREATE_TIMEOUT_S)
        except (StepError, TimeoutError, OSError) as exc:
            # 생성은 서버에서 shield 로 끝까지 간다 — 끊겼으면 목록에서 찾는다.
            self.say(f"  ⚠️ 응답을 못 받았다({str(exc)[:160]}) — 목록을 폴링해 찾는다(최대 15 분)")
            made = self._wait_for_fund()
        if made is None:
            raise StepError("새 펀드를 찾지 못했다 — 서버 로그(fund_ · rebalancer)를 본다")
        self.state["new_fund"] = str(made.get("fund_id"))
        self._save_state()
        anchor = made.get("anchor") or {}
        self.say(
            f"  ✅ 만들어짐 {made.get('fund_id')} · 장부 {made.get('balance')} · 종목 {len(made.get('symbols') or [])} · "
            f"첫 앵커 {anchor.get('mode')} · 건너뜀 {anchor.get('skipped') or '없음'}"
        )

    def _wait_for_fund(self, tries: int = 60, every: float = 15.0) -> dict[str, Any] | None:
        for _ in range(tries):
            for f in gate_funds(self._funds()):
                if str(f.get("label")) == self.label:
                    return f
            self.sleep(every)
        return None

    # 4 ───────────────────────────────────────────────
    def step4(self) -> bool:
        book = self._book()
        self.say("4 단계 · 뒤 점검(읽기만)")
        facts = self._exchange()
        checks = verify_after(
            self._funds(),
            self._sessions(),
            label=self.label,
            playbook=book,
            members=self.members,
            wallet_total=facts["total"],
        )
        for c in checks:
            self.say("  " + c.line())
        ok = all(c.ok for c in checks)
        self.say(
            "  ✅ 전부 기준 안 — 다음은 make live-check 1 절" if ok else "  ⛔ 기준 밖 항목이 있다"
        )
        return ok


# ── 진입점 ─────────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--step", type=int, required=True, choices=[0, 1, 2, 3, 4])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="계산만(기본) — 돈이 움직이는 호출을 보내지 않는다"
    )
    mode.add_argument("--apply", action="store_true", help="실제로 보낸다(1 ~ 3 단계)")
    p.add_argument("--old-fund", default=None, help="1 단계 — 접을 펀드 id(없으면 0 단계 기록)")
    p.add_argument(
        "--playbook",
        default=None,
        help=f"3 단계 — 선언 id(없으면 0 단계 기록 · 기본 {DEFAULT_PLAYBOOK})",
    )
    p.add_argument("--label", default=DEFAULT_LABEL, help="새 펀드 이름(멱등 판정 열쇠)")
    p.add_argument("--members", type=int, default=DEFAULT_MEMBERS, help="기대 멤버 수")
    p.add_argument(
        "--base-url",
        default=None,
        help="api 주소(기본 host.env 의 https://<PUBLIC_DOMAIN>/api · 컨테이너 안이면 127.0.0.1:8000)",
    )
    p.add_argument(
        "--cookie-file",
        default=None,
        help=f"{COOKIE_NAME} 쿠키 값 파일(기본 logs/t607/session · 컨테이너 안 /tmp/t607_session)",
    )
    p.add_argument(
        "--check-json",
        default=None,
        help="라이브 점검 check.json(기본 logs/live_review/check.json · 2 단계 기대치)",
    )
    p.add_argument("--state", default=None, help="단계 사이 기록 파일(기본 logs/t607/state.json)")
    p.add_argument(
        "--force", action="store_true", help="기대 목록 불일치를 무시한다(사유를 알 때만)"
    )
    args = p.parse_args(list(argv))
    if not args.dry_run and not args.apply:
        args.dry_run = True
    return args


def read_host_env() -> dict[str, str]:
    """scripts/ops/host.env — 값은 따옴표 없이 · 주석은 뗀다."""
    path = HERE / "host.env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if "=" in body:
            k, v = body.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def resolve_base_url(given: str | None) -> str:
    if given:
        return given
    if IN_CONTAINER:
        return "http://127.0.0.1:8000"
    domain = read_host_env().get("PUBLIC_DOMAIN")
    if not domain:
        raise StepError("api 주소를 모른다 — scripts/ops/host.env 의 PUBLIC_DOMAIN 또는 --base-url")
    return f"https://{domain}/api"


def read_cookie(given: str | None) -> str:
    path = (
        Path(given)
        if given
        else (Path("/tmp/t607_session") if IN_CONTAINER else ROOT / "logs" / "t607" / "session")
    )
    if not path.exists():
        raise StepError(
            f"쿠키 파일이 없다: {path} — 브라우저(구글 재인증 뒤)에서 {COOKIE_NAME} 값만 한 줄로 넣는다(비추적 · 끝나면 지운다)"
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or value.startswith(f"{COOKIE_NAME}="):
        raise StepError(f"쿠키 파일은 값 한 줄만 — '{COOKIE_NAME}=' 접두어 없이")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logs_root = Path("/app/logs") if IN_CONTAINER else ROOT / "logs"
    log_dir = logs_root / "t607"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"fresh_start_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_step{args.step}.log"
    mode = "계산만" if args.dry_run else "🔴 실행"

    def say(line: str) -> None:
        text = f"{kst()} {line}"
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:  # 로그 먼저 — 파일이 곧 기록
            fh.write(text + "\n")

    try:
        base = resolve_base_url(args.base_url)
        say(f"=== T607 새 출발 · 단계 {args.step} · {mode} · {base} · 로그 {log_path}")
        api = HttpApi(base, read_cookie(args.cookie_file))
        expected: list[str] | None = None
        check_path = (
            Path(args.check_json)
            if args.check_json
            else ROOT / "logs" / "live_review" / "check.json"
        )
        if check_path.exists():
            expected = expected_unbound_from_check(check_path.read_text(encoding="utf-8"))
        runner = Runner(
            api=api,
            dry_run=args.dry_run,
            state_path=Path(args.state) if args.state else log_dir / "state.json",
            say=say,
            label=args.label,
            playbook=args.playbook,
            members=args.members,
            expected_unbound=expected,
            force=args.force,
        )
        if args.step == 0:
            runner.step0()
        elif args.step == 1:
            runner.step1(args.old_fund)
        elif args.step == 2:
            runner.step2()
        elif args.step == 3:
            runner.step3()
        else:
            return 0 if runner.step4() else 2
    except StepError as exc:
        say(f"⛔ 멈춤 — {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
