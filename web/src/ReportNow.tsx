/**
 * 지금 보내기 — 페이퍼 콘솔에서 성과 리포트를 **한 번에** 보낸다 (사용자 요청 2026-08-23).
 *
 * 리포트 탭(T35)은 미리보기 → 확인 → 보내기다. 여기는 판을 보다가 바로 쏘는 카드라
 * 확인 단계를 뺐다 — 수신자는 기본값(REPORT_TO · 내 메일)이고, 실패는 500 이 아니라
 * `ok:false` 로 돌아와 화면에 남는다 (규칙 #8). 미리보기가 필요하면 리포트 탭으로.
 */

import { useState } from "react";
import { reportSend } from "./api";

/**
 * 지금 보내기 — `symbol` 을 주면 **그 판만** (판 이메일), 없으면 전체 취합 (콘솔 이메일).
 *
 * 🔴 판 화면에서는 그 판 하나만 담아야 한다 (사용자 2026-08-24) — 판 이메일에 전체
 * 매매가 섞이던 것을 바로잡는다. 콘솔에서는 symbol 없이 전체를 취합해 보낸다.
 */
export function ReportNow({ symbol }: { symbol?: string }) {
  const [hours, setHours] = useState("24");
  // ⭐ 거래소 범위 (사용자 요구 2026-08-26) — "" = 전체(거래소별 절 포함).
  const [market, setMarket] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const scope = symbol ? `${symbol} 이 판만` : market ? `${market} 전체` : "전체";

  const send = async () => {
    setBusy(true);
    setResult("");
    try {
      const body = await reportSend({
        hours: Number(hours) || 24,
        symbol,
        market: market || undefined,
      });
      setResult(
        body.ok
          ? `보냈다 (${scope} · 지난 ${Number(hours) || 24}시간 · 기본 수신자)${body.html ? " · 차트 포함" : ""} ${new Date().toLocaleTimeString()}`
          : "보내지 못했다 — 리포트 탭의 발송 이력을 본다",
      );
    } catch (error) {
      setResult(`발송 실패: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card wide">
      <span className="card-name">
        성과 리포트 지금 보내기 {symbol ? "· 이 판만" : "· 전체"}
      </span>
      <div className="row">
        <label className="field">
          지난 시간
          <input value={hours} onChange={(e) => setHours(e.target.value)} inputMode="numeric" />
        </label>
        {/* 판 이메일(symbol)에는 범위 선택이 없다 — 그 판의 거래소가 곧 범위다. */}
        {!symbol && (
          <label className="field">
            범위
            <select value={market} onChange={(e) => setMarket(e.target.value)}>
              <option value="">전체 (거래소별 절 포함)</option>
              <option value="GATE">GATE 전체</option>
              <option value="BINANCE">BINANCE 전체</option>
            </select>
          </label>
        )}
        <button className="btn primary" disabled={busy} onClick={send}>
          {busy ? "보내는 중…" : "지금 보내기"}
        </button>
        {result ? <span className="card-hint">{result}</span> : null}
      </div>
      <span className="card-hint">
        기본 수신자(REPORT_TO)로 간다. 원장과 거래소 체결을 나란히 싣고, 갈리면 갈렸다고
        쓴다. 전체 리포트에는 거래소별·펀드 절이 붙고, 거래소 범위를 고르면 그
        거래소만 평문으로 간다 — 수신자를 바꾸거나 미리 보려면 리포트 탭. 매일 09:05
        KST 자동 발송(전체)은 그대로다.
      </span>
    </div>
  );
}
