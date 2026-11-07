/**
 * 엔진 다리 펼침 — 판(RUN)이 없는 다리(나스닥)를 히트맵 칸 아래에 그 자리에서 보여 준다 (사용자 2026-11-06).
 *
 * 목록 · 히트맵은 코인 판과 같은 `per_symbol` 줄을 쓰지만, RUN 상세 화면은 세션 하나에 묶여 있어 엔진을 못 연다.
 * 그래서 값은 서버가 준 `engine` 요약을 그대로 쓰고, 차트는 종목 차트와 같은 `analysisFrame` · `Chart` 를 재사용한다.
 * 차트는 펼칠 때 한 번만 받는다(화면 폴링을 늘리지 않는다).
 *
 * 가상 몫 = 펀드 시작 전 신호나 증거금 양보로 실제 주문 없이 계산만 잇는 몫 — 돈이 들어가 있지 않다.
 */
import { useEffect, useState } from "react";
import {
  type AnalysisFrame,
  analysisFrame,
  type FundEngineStatus,
} from "./api";
import { Chart } from "./Chart";

function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fixed(value: string | number | null | undefined, digits = 2): string {
  const parsed = num(value);
  return parsed === null
    ? "—"
    : parsed.toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}

export function EngineLegPanel({
  symbol,
  market,
  name,
  status,
  onClose,
  closeLabel = "접기",
}: {
  symbol: string;
  market: string;
  name: string;
  status: FundEngineStatus;
  onClose: () => void;
  closeLabel?: string;
}) {
  const [frame, setFrame] = useState<AnalysisFrame | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!market) {
      setError("펀드 거래소 값이 비어 있다");
      return;
    }
    let alive = true;
    analysisFrame({
      symbol,
      market,
      flags: ["trend.structure"],
      timeframe: "1d",
    })
      .then((got) => {
        if (alive) setFrame(got);
      })
      .catch((exc: unknown) => {
        if (alive) setError(String(exc));
      });
    return () => {
      alive = false;
    };
  }, [symbol, market]);

  const real = status.units.filter((u) => !u.ghost);
  const ghosts = status.units.filter((u) => u.ghost);
  return (
    <div
      className="member-card"
      data-testid="engine-leg"
      style={{ marginTop: 8 }}
    >
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <b>
          {name} · {symbol.replace("_USDT", "")}
        </b>
        <button type="button" className="btn" onClick={onClose}>
          {closeLabel}
        </button>
      </div>
      <div
        className="row text-sm"
        style={{ gap: 12, marginTop: 6, flexWrap: "wrap" }}
      >
        <span>
          실제 <b>{real.length}</b>몫 · {status.contracts}계약
        </span>
        <span className="faint">가상 {ghosts.length}몫(돈 안 들어감)</span>
        <span>
          순환 구간 <b>{status.zone ? "안" : "밖"}</b>
        </span>
        <span>
          실현 <b>{fixed(status.realized)}</b> USDT
        </span>
        <span>
          미실현 <b>{fixed(status.unrealized)}</b> USDT
        </span>
        <span className="faint">마지막 처리 {status.last_day ?? "—"}</span>
      </div>
      {status.buy ? (
        <div className="text-sm" style={{ marginTop: 4 }}>
          걸어 둔 매수 <b>{fixed(status.buy.level)}</b> · {status.buy.contracts}
          계약
          {status.buy.filled ? (
            <>
              {" · "}
              <b>{status.buy.filled}계약 찼다</b>(다음 마감에 몫이 된다) · 재난
              손절 <b>{fixed(status.buy.stop)}</b>
            </>
          ) : null}
        </div>
      ) : null}
      {status.findings.length > 0 ? (
        <div className="notice bad" style={{ marginTop: 4 }}>
          확인 필요: {status.findings.join(" · ")}
        </div>
      ) : null}
      {status.units.length > 0 ? (
        <div style={{ overflowX: "auto", marginTop: 6 }}>
          <table className="text-sm" style={{ width: "100%" }}>
            <thead>
              <tr className="faint" style={{ textAlign: "left" }}>
                <th>몫</th>
                <th>종류</th>
                <th>상태</th>
                <th>진입가</th>
                <th>계약</th>
                <th>손절</th>
                <th>진입일</th>
              </tr>
            </thead>
            <tbody>
              {status.units.map((u) => (
                <tr key={u.key} className={u.ghost ? "faint" : ""}>
                  <td>{u.key}</td>
                  <td>{u.kind}</td>
                  <td>{u.ghost ? "가상" : "실제"}</td>
                  <td>{fixed(u.fill)}</td>
                  <td>{u.contracts}</td>
                  <td>{fixed(u.stop)}</td>
                  <td>{u.opened.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div style={{ marginTop: 8 }}>
        {error ? (
          <p className="notice bad">차트를 못 읽었다 — {error}</p>
        ) : null}
        {frame ? (
          <Chart frame={frame} />
        ) : error ? null : (
          <p className="faint">차트 불러오는 중…</p>
        )}
      </div>
    </div>
  );
}
