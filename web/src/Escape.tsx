/**
 * **호가가 말랐을 때 나가는 길** (사용자 요구 2026-08-20).
 *
 * 사용자 지적: *"말랐을 때, 어떻게 대처해야 하는지도 있어야 하는 거 아닌가?"* 맞다 —
 * 배너만 띄우는 것은 불났다고 알리고 소화기를 안 주는 것이다.
 *
 * 🔴 **그런데 값은 기계가 정할 수 없다.** 실측이 그 이유다:
 *
 * ```
 * SPCX 를 그때 시장가로 던졌다면   -420
 * 표시가 아래 지정가로 기다렸더니   -74   (138.50 · 15시간 31분)
 * ```
 *
 * 138.50 은 -74 였고 130 이었으면 -280 이었다. **얼마에 거느냐가 곧 손실 결정**이라,
 * 화면이 계산해 나란히 놓고 사람이 누른다.
 *
 * ⛔ 자동 청산은 없다. 호가는 잠깐 얇아졌다 돌아오는 일이 흔하고, 그때마다 던지면
 * 멀쩡한 포지션을 매번 손해 보고 닫는다.
 */

import { useCallback, useEffect, useState } from "react";
import { escapePlace, escapeView, type EscapePlan } from "./api";
import { num } from "./ui";

/** 손익 한 줄 — 부호가 색을 정한다. */
function Money({ value }: { value: number | null }) {
  if (value === null) return <span className="faint">—</span>;
  return (
    <b className={value >= 0 ? "gain" : "loss"}>
      {value >= 0 ? "+" : ""}
      {num(value, 2)} USDT
    </b>
  );
}

export function Escape({
  symbol,
  market = "GATE",
  onDone,
}: {
  symbol: string;
  market?: string;
  /** 걸고 나면 부모가 거래소를 다시 읽는다. */
  onDone: () => void;
}) {
  const [plan, setPlan] = useState<EscapePlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 🔴 되돌릴 수 없는 주문이다 — 한 번 더 묻는다 (전량 청산과 같은 규칙).
  const [sure, setSure] = useState(false);

  const pull = useCallback(() => {
    escapeView(symbol, market)
      .then((body) => {
        setPlan(body.plan);
        setError("");
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, [symbol, market]);

  useEffect(() => {
    pull();
    // ⚠️ 호가가 움직이면 권장 자리도 움직인다 — 낡은 값을 누르면 거래소가 거절한다.
    const timer = setInterval(pull, 15_000);
    return () => clearInterval(timer);
  }, [pull]);

  // ⭐ **시장가로 나갈 수 있으면 이 창을 안 그린다** — 그때는 전량 청산이 답이고,
  //   선택지를 늘리면 사람이 더 나쁜 값을 고를 수 있다.
  if (!plan || plan.market_ok) return null;

  const place = () => {
    setBusy(true);
    setError("");
    escapePlace(symbol, plan.suggested, market)
      .then(() => {
        setSure(false);
        onDone();
        pull();
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="notice warn">
      <b>
        {plan.symbol} 나가는 길 — {plan.side} {plan.size}계약
      </b>
      {error ? <p className="loss">{error}</p> : null}
      <div className="table-wrap">
        <table>
          <tbody>
            <tr>
              <td>진입 평단</td>
              <td className="num mono">{plan.entry}</td>
              <td className="faint">표시가 {plan.mark}</td>
            </tr>
            {/* 🔴 **즉시 나가는 값을 먼저 보여 준다** — 그것이 안 되는 상황을 사람이
                먼저 알아야, 아래 권장 자리가 왜 필요한지가 이해된다. */}
            <tr>
              <td>지금 즉시 나가면</td>
              <td className="num mono">{plan.touch || "—"}</td>
              <td>
                {plan.touch_ok ? (
                  <Money value={plan.touch_realized} />
                ) : (
                  <span
                    className="chip loss"
                    title={`표시가 ${plan.mark} 에서 너무 멀다 — Gate 가 안 받는다 (한계 ${plan.limit})`}
                  >
                    거래소가 안 받는다
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <td>
                <b>권장 자리</b>
                <br />
                <span className="faint">파는 줄 맨 앞</span>
              </td>
              <td className="num mono">
                <b>{plan.suggested}</b>
              </td>
              <td>
                <Money value={plan.realized} />
                <br />
                <span className="faint">수수료 전</span>
              </td>
            </tr>
            <tr>
              <td>걸 수 있는 한계</td>
              <td className="num mono">{plan.limit}</td>
              <td className="faint">
                이보다 불리하게는 Gate 가 안 받는다
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {/* ⛔ 자동으로 안 던진다 — 얼마에 거느냐가 곧 손실 결정이다. */}
      {sure ? (
        <div className="row" style={{ gap: "8px" }}>
          <button className="btn danger" disabled={busy} onClick={place}>
            {busy ? "거는 중…" : `${plan.suggested} 에 정말 건다`}
          </button>
          <button className="btn" disabled={busy} onClick={() => setSure(false)}>
            그만둔다
          </button>
        </div>
      ) : (
        <div className="row" style={{ gap: "8px" }}>
          <button className="btn" onClick={() => setSure(true)}>
            {plan.suggested} 에 탈출 지정가 걸기
          </button>
          <span className="faint">
            줄었다 늘지 않는다 (reduce-only) · 체결될 때까지 기다린다
          </span>
        </div>
      )}
    </div>
  );
}
