/**
 * 예측 설명 칸 — 차트 아래 한 줄씩 (T820 · T825 · 표시 전용).
 *
 * 차트의 띠 · 부채꼴 · 갈래가 **무엇을 근거로 언제 기준인지**를 글로 적는다. 숫자는 서버 값 그대로다.
 *
 * - 기준 시각이 연구가 잰 간격(진입 뒤 4 시간 배수)이 아니면 "연구 검증 간격 아님" 을 적는다.
 * - 처음 손절을 몰라 지금 손절로 친 매매는 "덜 정확" 을 적는다(사용자 2026-11-11).
 * - 그리지 못한 매매는 까닭을 적는다 — 조용히 빼지 않는다.
 */
import type { Forecast, ForecastTrade } from "./api";
import { fmtPrice } from "./chart/trades";
import { when } from "./ui";

const pct = (v: number): string => `${Math.round(v * 100)}%`;

/** 매매 하나의 설명 문장들. */
export function forecastLines(t: ForecastTrade): string[] {
  if (!t.ok || t.basis === undefined || t.band === undefined) {
    return [`${t.leg}: 예측 안 그림 — ${t.reason ?? "까닭 모름"}`];
  }
  const b = t.basis;
  const flags = [
    b.on_grid ? null : "연구 검증 간격 아님",
    b.stop_source === "initial"
      ? null
      : b.stop_source === "adopted"
        ? "이어받은 포지션 · 지금 손절 기준(덜 정확)"
        : "처음 손절 모름 · 지금 손절 기준(덜 정확)",
    b.btc_known ? null : "BTC 국면 모름",
  ].filter((v): v is string => v !== null);
  const head = `${t.leg}: 기준 ${when(b.at)} · 진입 뒤 ${Math.round(b.hold_h)}시간${flags.length ? ` · ${flags.join(" · ")}` : ""}`;
  const band =
    `청산 가운데 ${fmtPrice(t.band.q50)} (절반이 ${fmtPrice(Math.min(t.band.q25, t.band.q75))} ~ ${fmtPrice(Math.max(t.band.q25, t.band.q75))})` +
    (t.band.q90 !== null ? ` · 꼬리 90% ${fmtPrice(t.band.q90)}` : "");
  const out = [head, band];
  if (t.band.note) out.push(t.band.note);
  if (t.branch) {
    const named = t.branch.lines.map((l) => `${l.name} ${pct(l.p)}`).join(" · ");
    out.push(`48시간 뒤 갈래: ${named}`);
  }
  return out;
}

export function ForecastNotes({
  forecast,
  shown,
  busy,
  onToggle,
  onNow,
}: {
  forecast: Forecast;
  shown: boolean;
  busy: boolean;
  onToggle: () => void;
  onNow: () => void;
}) {
  if (!forecast.available) {
    return <p className="card-hint">예측: {forecast.reason ?? "모형 값이 없다"}</p>;
  }
  if (forecast.trades.length === 0) return null;
  return (
    <div style={{ marginTop: 6, fontSize: 12 }}>
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <strong>예측</strong>
        <span className="card-hint">연구 매매에서 맞춘 모형 · 표시 전용(매매에 안 씀)</span>
        <label className="card-hint" style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={shown} onChange={onToggle} /> 차트에 그리기
        </label>
        <button className="btn small" disabled={busy} onClick={onNow} title="마지막으로 닫힌 1시간봉 기준으로 다시 계산한다">
          {busy ? "계산 중…" : "지금 기준으로 예측"}
        </button>
      </div>
      <ul style={{ margin: "4px 0 0", paddingLeft: 18, opacity: 0.85 }}>
        {forecast.trades.map((t) => (
          <li key={t.trade_id}>
            {forecastLines(t).map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </li>
        ))}
      </ul>
      <p className="card-hint" style={{ margin: "4px 0 0" }}>
        띠 = 비슷한 과거 매매가 끝난 자리(절반이 이 안) · 부채꼴 = 12 · 24 · 48시간 뒤 있을 법한 범위(진한 곳 절반 · 옅은 곳 10번 중 8번) ·
        갈래 = 유리 · 옆 · 불리 쪽 가운데 길과 확률. 맞히는 예측이 아니라 대략의 범위다.
      </p>
    </div>
  );
}
