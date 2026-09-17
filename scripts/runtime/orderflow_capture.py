"""T283 — 실시간 호가·체결·미결제약정 수집기 (로컬 WSL · 공개 엔드포인트 · 키 없음).

왜: T279 반전 트랙 후보 1~5 가 봉 안의 정보와 펀딩으로는 전부 0 띠였다.
남은 정보원은 봉 밖 실시간 자료인데 거래소가 과거분을 주지 않아 백테스트를 할 수 없다.
그래서 지금부터 90일을 모은다 — docs/planning/tasks/T283_orderflow_capture.md.

무엇을 (1분마다 · 종목 12개):
    book    호가 상위 20단계 — 최우선 호가 · 상위 5/20단 잔량 합 · 불균형 (bid - ask) / (bid + ask)
    trades  직전 60초 체결 — 매수/매도 수량·건수 (Gate: size 부호 · 업비트: ask_bid)
    stats   (Gate · 5분마다) 미결제약정 · 롱/숏 비율 · 청산량 — /futures/usdt/contract_stats

어디에: logs/orderflow/<market>/<symbol>/<YYYY-MM-DD>.jsonl (한 줄 = 한 관측 · UTC 분 단위)
        heartbeat 는 logs/orderflow/heartbeat.json.

절대 규칙 #0(어댑터 직접 생성 금지)은 주문 경로의 규칙이다. 이 스크립트는 공개 시세만 httpx 로
읽고(키 없음 · 주문 없음) `src/` 를 import 하지 않는다. 실계좌 서버·호출 예산(ON_LIVE #3)과 무관.

    uv run python scripts/runtime/orderflow_capture.py --once   # 한 바퀴 시험
    bash scripts/runtime/orderflow_ctl.sh start|stop|status      # 상시 수집
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "logs" / "orderflow"
GATE = "https://api.gateio.ws/api/v4"
UPBIT = "https://api.upbit.com/v1"
# 종목은 환경 변수로 바꿀 수 있다(서버 데모 우주 등 · 쉼표 구분 · 비면 기본 6종). 기본은 핵심 6종.
_GATE_DEFAULT = "BTC_USDT,ETH_USDT,XRP_USDT,SOL_USDT,DOGE_USDT,ADA_USDT"
_UPBIT_DEFAULT = "KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-DOGE,KRW-ADA"
GATE_CONTRACTS = tuple(
    s.strip() for s in os.environ.get("ORDERFLOW_GATE", _GATE_DEFAULT).split(",") if s.strip()
)
UPBIT_MARKETS = tuple(
    s.strip() for s in os.environ.get("ORDERFLOW_UPBIT", _UPBIT_DEFAULT).split(",") if s.strip()
)
STATS_EVERY = 5  # 분


def _minute(ts: datetime) -> str:
    return ts.replace(second=0, microsecond=0).isoformat()


def _write(market: str, symbol: str, row: dict[str, Any]) -> None:
    day = row["ts"][:10]
    path = OUT / market / symbol / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _book_summary(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]]
) -> dict[str, Any]:
    """bids/asks = (가격, 수량) · 최우선부터."""
    b5 = sum(q for _, q in bids[:5])
    a5 = sum(q for _, q in asks[:5])
    b20 = sum(q for _, q in bids[:20])
    a20 = sum(q for _, q in asks[:20])
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    spread_bp = ((best_ask - best_bid) / best_bid * 1e4) if best_bid and best_ask else None
    return {
        "bid": best_bid,
        "ask": best_ask,
        "spread_bp": round(spread_bp, 3) if spread_bp is not None else None,
        "bid5": b5,
        "ask5": a5,
        "bid20": b20,
        "ask20": a20,
        "imb5": round((b5 - a5) / (b5 + a5), 4) if (b5 + a5) else None,
        "imb20": round((b20 - a20) / (b20 + a20), 4) if (b20 + a20) else None,
    }


class Capture:
    def __init__(self, client: httpx.Client) -> None:
        self.c = client
        self.last_trade_id: dict[str, int] = {}
        self.last_upbit_seq: dict[str, int] = {}
        self.errors = 0

    # ── Gate ────────────────────────────────────────────────
    def gate_book(self, contract: str, ts: datetime) -> None:
        r = self.c.get(
            f"{GATE}/futures/usdt/order_book", params={"contract": contract, "limit": 20}
        )
        r.raise_for_status()
        d = r.json()
        bids = [(float(x["p"]), float(x["s"])) for x in d.get("bids", [])]
        asks = [(float(x["p"]), float(x["s"])) for x in d.get("asks", [])]
        _write("GATE", contract, {"ts": _minute(ts), "kind": "book", **_book_summary(bids, asks)})

    def gate_trades(self, contract: str, ts: datetime) -> None:
        r = self.c.get(f"{GATE}/futures/usdt/trades", params={"contract": contract, "limit": 500})
        r.raise_for_status()
        rows = r.json()
        since = ts.timestamp() - 60
        last = self.last_trade_id.get(contract, 0)
        buy_q = sell_q = 0.0
        buy_n = sell_n = 0
        max_id = last
        for t in rows:
            tid = int(t.get("id", 0))
            if tid <= last or float(t.get("create_time", 0)) < since:
                continue
            max_id = max(max_id, tid)
            size = float(t.get("size", 0))
            if size > 0:
                buy_q += size
                buy_n += 1
            else:
                sell_q += -size
                sell_n += 1
        self.last_trade_id[contract] = max_id
        tot = buy_q + sell_q
        _write(
            "GATE",
            contract,
            {
                "ts": _minute(ts),
                "kind": "trades",
                "buy_q": buy_q,
                "sell_q": sell_q,
                "buy_n": buy_n,
                "sell_n": sell_n,
                "buy_ratio": round(buy_q / tot, 4) if tot else None,
            },
        )

    def gate_stats(self, contract: str, ts: datetime) -> None:
        r = self.c.get(
            f"{GATE}/futures/usdt/contract_stats",
            params={"contract": contract, "interval": "5m", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        d = rows[-1]
        keep = (
            "open_interest",
            "open_interest_usd",
            "lsr_taker",
            "lsr_account",
            "top_lsr_account",
            "top_lsr_size",
            "long_liq_size",
            "short_liq_size",
            "long_liq_usd",
            "short_liq_usd",
            "mark_price",
        )
        _write(
            "GATE", contract, {"ts": _minute(ts), "kind": "stats", **{k: d.get(k) for k in keep}}
        )

    # ── Upbit ───────────────────────────────────────────────
    def upbit_books(self, ts: datetime) -> None:
        r = self.c.get(f"{UPBIT}/orderbook", params={"markets": ",".join(UPBIT_MARKETS)})
        r.raise_for_status()
        for d in r.json():
            units = d.get("orderbook_units", [])
            bids = [(float(u["bid_price"]), float(u["bid_size"])) for u in units]
            asks = [(float(u["ask_price"]), float(u["ask_size"])) for u in units]
            _write(
                "UPBIT",
                d["market"],
                {"ts": _minute(ts), "kind": "book", **_book_summary(bids, asks)},
            )

    def upbit_trades(self, market: str, ts: datetime) -> None:
        r = self.c.get(f"{UPBIT}/trades/ticks", params={"market": market, "count": 200})
        r.raise_for_status()
        rows = r.json()
        since_ms = (ts.timestamp() - 60) * 1000
        last = self.last_upbit_seq.get(market, 0)
        buy_q = sell_q = 0.0
        buy_n = sell_n = 0
        max_seq = last
        for t in rows:
            seq = int(t.get("sequential_id", 0))
            if seq <= last or float(t.get("timestamp", 0)) < since_ms:
                continue
            max_seq = max(max_seq, seq)
            vol = float(t.get("trade_volume", 0))
            if t.get("ask_bid") == "BID":
                buy_q += vol
                buy_n += 1
            else:
                sell_q += vol
                sell_n += 1
        self.last_upbit_seq[market] = max_seq
        tot = buy_q + sell_q
        _write(
            "UPBIT",
            market,
            {
                "ts": _minute(ts),
                "kind": "trades",
                "buy_q": buy_q,
                "sell_q": sell_q,
                "buy_n": buy_n,
                "sell_n": sell_n,
                "buy_ratio": round(buy_q / tot, 4) if tot else None,
            },
        )

    # ── 한 바퀴 ─────────────────────────────────────────────
    def cycle(self, ts: datetime, with_stats: bool) -> dict[str, int]:
        ok = err = 0
        steps: list[tuple[str, Any]] = [("upbit_books", lambda: self.upbit_books(ts))]
        for m in UPBIT_MARKETS:
            steps.append((f"upbit_trades:{m}", lambda m=m: self.upbit_trades(m, ts)))
        for cn in GATE_CONTRACTS:
            steps.append((f"gate_book:{cn}", lambda cn=cn: self.gate_book(cn, ts)))
            steps.append((f"gate_trades:{cn}", lambda cn=cn: self.gate_trades(cn, ts)))
            if with_stats:
                steps.append((f"gate_stats:{cn}", lambda cn=cn: self.gate_stats(cn, ts)))
        for name, fn in steps:
            try:
                fn()
                ok += 1
            except Exception as exc:
                err += 1
                self.errors += 1
                print(f"{_minute(ts)} {name} 실패: {type(exc).__name__}", file=sys.stderr)
            time.sleep(0.15)  # 공개 한도(업비트 10/s · Gate 200/10s) 훨씬 아래
        return {"ok": ok, "err": err}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--once", action="store_true", help="한 바퀴만 돌고 끝낸다(시험)")
    p.add_argument("--interval", type=int, default=60, help="주기(초) · 기본 60")
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cycles = 0
    with httpx.Client(
        timeout=10.0, headers={"User-Agent": "updown-orderflow-capture/1.0"}
    ) as client:
        cap = Capture(client)
        while True:
            ts = datetime.now(UTC)
            res = cap.cycle(ts, with_stats=(cycles % STATS_EVERY == 0))
            cycles += 1
            (OUT / "heartbeat.json").write_text(
                json.dumps(
                    {
                        "last": ts.isoformat(),
                        "cycles": cycles,
                        "last_ok": res["ok"],
                        "last_err": res["err"],
                        "errors_total": cap.errors,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if args.once:
                print(json.dumps({"cycle": res, "errors_total": cap.errors}))
                return 0 if res["err"] == 0 else 1
            # 다음 분 경계까지
            time.sleep(max(1.0, args.interval - (datetime.now(UTC) - ts).total_seconds()))


if __name__ == "__main__":
    raise SystemExit(main())
