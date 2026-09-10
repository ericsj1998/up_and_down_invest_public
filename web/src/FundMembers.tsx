/**
 * 펀드 종목 상세 (T261 · 사용자 요구 2026-09-10 "상세보기") — 종목마다 마감 일봉 차트 + 몫·포지션·등락 한 줄.
 *
 * 값은 전부 서버(`GET /rebalancer/{id}/members`)에서 온다. 차트는 RUN 상세와 같은 부품(`PriceChart`)이라
 * 겹칩선·자릿수 규칙이 같다. 팝업 없음 — 카드 아래에 펼쳐진다.
 */

import { useEffect, useState } from "react";
import { fundMembers, type FundMember } from "./api";
import { DEFAULT_SETTINGS } from "./chart/indicators";
import { PriceChart } from "./chart/PriceChart";
import { changeText, changeTone, toOhlc } from "./fundMembers";
import { ErrorCard } from "./ui";

const DAY_SECONDS = 86_400;

function money(v?: string | null): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
}

function MemberCard({ m }: { m: FundMember }) {
  const bars = toOhlc(m.bars);
  return (
    <div className="card" style={{ padding: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
        <strong>
          {m.symbol.replace("_USDT", "")} <span className="faint">비중 {m.weight}</span>
        </strong>
        <span className="text-sm">
          현재가 <b>{money(m.last)}</b> · 1일 <b className={changeTone(m.change_1d_pct)}>{changeText(m.change_1d_pct)}</b> · 5일{" "}
          <b className={changeTone(m.change_5d_pct)}>{changeText(m.change_5d_pct)}</b>
        </span>
      </div>
      <div className="text-sm faint" style={{ margin: "2px 0 6px" }}>
        몫 {money(m.equity)}
        {m.holding && m.position ? (
          <>
            {" "}
            · 포지션 <b>{m.position.side}</b> 진입 {money(m.position.entry)} · 손절 {money(m.position.stop)} · 목표{" "}
            {money(m.position.target)} · 미실현 <b className={changeTone(m.unrealized)}>{money(m.unrealized)}</b>
          </>
        ) : (
          " · 포지션 없음"
        )}
        {m.reconciled === false || m.accounting_ok === false ? " · ⚠︎ 거래소와 갈림" : ""}
      </div>
      {bars.length > 1 ? (
        <PriceChart bars={bars} step={DAY_SECONDS} settings={DEFAULT_SETTINGS} height={180} />
      ) : (
        <p className="faint text-sm">{m.bars_error ? `봉을 못 받았다 — ${m.bars_error}` : "봉이 없다"}</p>
      )}
    </div>
  );
}

export function FundMembers({ fundId }: { fundId: string }) {
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
        <MemberCard key={m.symbol} m={m} />
      ))}
    </div>
  );
}
