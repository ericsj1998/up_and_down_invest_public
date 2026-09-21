/**
 * 펀드 종목 상세 (T261 · 사용자 요구 2026-09-10 "상세보기") — 종목마다 마감 일봉 차트 + 몫·포지션·등락.
 *
 * 값은 전부 서버(`GET /rebalancer/{id}/members`)에서 온다. 차트는 RUN 상세와 같은 부품(`PriceChart`)이라
 * 겹칩선·자릿수 규칙이 같다. 팝업 없음 — 카드 아래에 펼쳐진다.
 *
 * 2026-09-21 사용자 요구 넷:
 *   ① 이익이면 초록 테두리 · 손해면 붉은 테두리
 *   ② 카드 차트에도 진입가 · 손절가 · 상자
 *   ③ 우측 하단 **접힘 모서리**를 누르면 그 RUN 상세로
 *   ④ 포지션 · 손익 · 손익금액 · 증거금을 **먼저**, 나머지는 아래 작은 글씨로
 */

import { useEffect, useState } from "react";
import { fundMembers, type FundMember } from "./api";
import { DEFAULT_SETTINGS } from "./chart/indicators";
import { PriceChart } from "./chart/PriceChart";
import type { TradeMark } from "./chart/trades";
import { cardTone, changeText, changeTone, toOhlc, unrealizedPct } from "./fundMembers";
import { ErrorCard } from "./ui";

const DAY_SECONDS = 86_400;

function money(v?: string | null): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
}

function signed(v?: string | null): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

/**
 * 열린 포지션 → 차트 상자 하나.
 *
 * ⚠️ 진입 시각이나 지금가가 없으면 **안 그린다** — 상자의 한쪽 끝을 지어내지 않는다.
 * 그때는 가로선도 안 나오는데, 없는 자리에 선을 긋는 것보다 낫다.
 */
function markOf(m: FundMember): TradeMark | null {
  const pos = m.position;
  const last = Number(m.last);
  if (!pos || !pos.opened_at || !Number.isFinite(last)) return null;
  const entry = Number(pos.entry);
  const stop = Number(pos.stop);
  const openedTs = Math.floor(Date.parse(pos.opened_at) / 1000);
  if (![entry, stop].every(Number.isFinite) || !(openedTs > 0)) return null;
  const pct = unrealizedPct(m);
  return {
    id: m.handle || m.symbol,
    symbol: m.symbol,
    side: pos.side === "숏" ? -1 : 1,
    entry,
    exit: last,
    stop,
    openedTs,
    // 마지막 마감 봉까지 — 일봉 카드라 "지금" 은 마지막 봉이다.
    closedTs: m.bars.length > 0 ? (m.bars[m.bars.length - 1]?.time ?? openedTs) : openedTs,
    pnl: pct,
    reason: "보유중",
    open: true,
  };
}

function MemberCard({
  m,
  openRun,
}: {
  m: FundMember;
  openRun?: (run: string, name?: string) => void;
}) {
  const bars = toOhlc(m.bars);
  const tone = cardTone(m);
  const mark = markOf(m);
  const pct = unrealizedPct(m);
  // 🔴 **목표가 익절선이 아닐 수 있다** (사용자 지적 2026-09-21). 추세추종은 고정 익절이 없고
  //    `target` 은 진입+100R 자리표시자다 — 그것을 '목표' 로 적으면 화면이 거짓말한다.
  const showTarget = m.position !== undefined && m.position.full_ride !== true;
  const canOpen = openRun !== undefined && m.handle !== "";
  return (
    <div
      className={`card member-card${tone ? ` ${tone}` : ""}`}
      style={{ padding: 8, position: "relative" }}
    >
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
        <strong>
          {m.symbol.replace("_USDT", "")} <span className="faint">비중 {m.weight}</span>
        </strong>
        <span className="text-sm">
          현재가 <b>{money(m.last)}</b> · 1일{" "}
          <b className={changeTone(m.change_1d_pct)}>{changeText(m.change_1d_pct)}</b> · 5일{" "}
          <b className={changeTone(m.change_5d_pct)}>{changeText(m.change_5d_pct)}</b>
        </span>
      </div>

      {/* ⭐ **먼저 보여야 하는 것** — 포지션 · 손익 · 손익금액 · 증거금 (사용자 요구 2026-09-21).
          전에는 몫·손절·목표와 한 줄에 섞여 작은 회색 글씨였다. */}
      {m.holding && m.position ? (
        <div style={{ margin: "4px 0 2px" }}>
          <b>{m.position.side}</b> <span className="faint">진입</span>{" "}
          <b>{money(m.position.entry)}</b>
          {" · "}
          <span className="faint">미실현</span>{" "}
          <b className={changeTone(m.unrealized)}>
            {signed(m.unrealized)} USDT
            {pct === null ? "" : ` (${pct > 0 ? "+" : ""}${pct.toFixed(2)}%)`}
          </b>
          {" · "}
          <span className="faint">증거금</span> <b>{money(m.margin)}</b>
        </div>
      ) : (
        <div style={{ margin: "4px 0 2px" }} className="faint">
          포지션 없음
        </div>
      )}

      {/* 부가 정보 — 손절선 · 몫 · 갈림 경고. 작은 글씨로 내린다. */}
      <div className="text-sm faint" style={{ marginBottom: 6 }}>
        몫 {money(m.equity)}
        {m.holding && m.position ? (
          <>
            {" · 손절 "}
            {money(m.position.stop)}
            {/* ⚠️ 고정 익절이 없는 매매법은 목표를 **안 적는다** — 진입+100R 자리표시자다. */}
            {showTarget ? ` · 목표 ${money(m.position.target)}` : " · 익절선 없음(추세 추종)"}
          </>
        ) : null}
        {m.reconciled === false || m.accounting_ok === false ? " · ⚠︎ 거래소와 갈림" : ""}
      </div>

      {bars.length > 1 ? (
        <PriceChart
          bars={bars}
          step={DAY_SECONDS}
          settings={DEFAULT_SETTINGS}
          height={180}
          // ⭐ 진입가 · 손절가 가로선 + 상자 (사용자 요구 2026-09-21).
          //    ⚠️ 90일 개요를 보는 카드라 **확대는 끈다** — 하루짜리 매매로 당기면 목적이 사라진다.
          {...(mark ? { trades: [mark], focusId: mark.id } : {})}
          autoZoom={false}
        />
      ) : (
        <p className="faint text-sm">{m.bars_error ? `봉을 못 받았다 — ${m.bars_error}` : "봉이 없다"}</p>
      )}

      {/* ⭐ **접힘 모서리** — 누르면 그 RUN 상세로 (사용자 요구 2026-09-21).
          ⚠️ 판이 없는 종목(`handle` 빈 값)에는 안 그린다 — 눌러 보고 나서야 아는 단추를 두지 않는다. */}
      {canOpen ? (
        <button
          type="button"
          className="corner-fold"
          title={`${m.symbol} RUN 상세로`}
          aria-label={`${m.symbol} RUN 상세로`}
          onClick={() => openRun?.(m.handle, m.symbol)}
        />
      ) : null}
    </div>
  );
}

export function FundMembers({
  fundId,
  openRun,
}: {
  fundId: string;
  openRun?: (run: string, name?: string) => void;
}) {
  const [rows, setRows] = useState<FundMember[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setRows(null);
    setError("");
    fundMembers(fundId)
      .then((body) => {
        if (alive) setRows(body.members);
      })
      .catch((exc: unknown) => {
        if (alive) setError(String(exc));
      });
    return () => {
      alive = false;
    };
  }, [fundId]);
  if (error) return <ErrorCard title="상세를 못 읽었다" message={error} />;
  if (rows === null) return <p className="faint text-sm">종목 봉을 읽는 중…</p>;
  if (rows.length === 0) return <p className="faint text-sm">종목이 없다.</p>;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: 8,
        marginTop: 6,
      }}
    >
      {rows.map((m) => (
        <MemberCard key={m.symbol} m={m} {...(openRun ? { openRun } : {})} />
      ))}
    </div>
  );
}
