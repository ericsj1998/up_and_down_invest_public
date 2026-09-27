/**
 * 펀드 한눈에 — 요약 한 줄 · 보유 종목만 · 종목 히트맵 (사용자 2026-09-27).
 *
 * > *"펀드 화면이 굳이 저렇게 길고, 많고, 복잡해야 하나? … 포지션 진입하면 말이 다르지만 지금은 의미없는 항목의
 * > 나열 같아 … 저런 게 오히려 상세 보기로 접혀 있어야"* · *"해당 펀드들 내 종목들의 히트맵도"*
 *
 * 40줄 표(대부분 "현금 · — · 0")는 "종목 표" 단추 뒤로 접었다(`FundPanel`). 여기는 늘 보이는 것만:
 *   ① 보유 · 현금 대기 수와 다리(매매법)별 종목 수 · 자리
 *   ② 보유 중인 종목은 **종목 표와 같은 표**로(`FundPanel` · 표를 접어 둬도 보유 줄만 보인다)
 *   ③ 히트맵 — 다리 조합으로 묶고(`fundLayout.legGroups`) 묶음 안은 거래대금 순 · 색은 24시간 등락 · 보유 칸은 테두리
 *
 * 값은 전부 서버에서 온다 — 펀드 현황(`/rebalancer`) · 종목 순위(`/exchange/ranking`). 못 읽은 칸은 "—" 와 무채색.
 */

import type { FundLeg, FundStatus } from "./api";
import { heatColor, tickOf, type SymbolGroupView, type Tick } from "./fundLayout";

function money(v?: string | null): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
}

function signedPct(n: number | null | undefined): string {
  return n === null || n === undefined || !Number.isFinite(n) ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

/** 거래대금을 사람 단위로 — 1.2억 · 3,400만 (툴팁 전용). */
function turnoverText(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}억`;
  if (n >= 1e4) return `${Math.round(n / 1e4).toLocaleString()}만`;
  return n.toLocaleString();
}

function Tile({
  sym,
  tick,
  leg,
  openRun,
}: {
  sym: string;
  tick: Tick | undefined;
  leg: FundLeg | undefined;
  openRun?: (run: string, name?: string) => void;
}) {
  const color = heatColor(tick?.change);
  const held = leg?.holding === true;
  const unreal = Number(leg?.unrealized);
  const canOpen = openRun !== undefined && !!leg?.handle;
  return (
    <button
      type="button"
      className={`heat-tile${held ? " held" : ""}`}
      style={{ background: color.bg, color: color.fg }}
      title={`${sym} · 24시간 ${signedPct(tick?.change)} · 거래대금 ${turnoverText(tick?.turnover)} USDT · ${
        held && leg?.position ? `${leg.position.side} @ ${money(leg.position.entry)}` : "현금"
      }`}
      disabled={!canOpen}
      onClick={() => (canOpen && leg ? openRun?.(leg.handle, sym) : undefined)}
    >
      <b>{sym.replace("_USDT", "")}</b>
      <span>{signedPct(tick?.change)}</span>
      {held ? (
        <span className="heat-pos">
          {leg?.position?.side ?? "보유"}
          {Number.isFinite(unreal) ? ` ${unreal > 0 ? "+" : ""}${unreal.toFixed(1)}` : ""}
        </span>
      ) : null}
    </button>
  );
}

export function FundOverview({
  f,
  groups,
  ticks,
  openRun,
}: {
  f: FundStatus;
  groups: SymbolGroupView[];
  ticks: Record<string, Tick>;
  openRun?: (run: string, name?: string) => void;
}) {
  const order = groups.flatMap((g) => g.symbols);
  const held = order.filter((sym) => f.per_symbol[sym]?.holding === true);
  const cash = order.length - held.length;
  const haveTicks = Object.keys(ticks).length > 0;
  return (
    <div className="fund-overview">
      <div className="row text-sm" style={{ gap: 8, marginTop: 6 }}>
        <span>
          보유 <b>{held.length}</b>종
        </span>
        <span className="faint">· 현금 대기 {cash}종</span>
        {(f.legs ?? []).map((leg) => (
          <span key={leg.playbook} className="leg-chip" title={`${leg.playbook} · 노출 ${leg.exposure}`}>
            {leg.name} {leg.symbols.length}종 · 자리 {leg.slots}
            {leg.isolated ? " · 브레이크 제외" : ""}
          </span>
        ))}
      </div>

      {/* ③ 히트맵 */}
      <div className="text-xs faint" style={{ marginTop: 8 }}>
        종목 히트맵 — 색은 24시간 등락 · 묶음 안은 거래대금 순 · 테두리는 보유 · 칸을 누르면 그 판으로
        {haveTicks ? "" : " · 시세를 아직 못 읽었다"}
      </div>
      {groups.map((g) => (
        <section key={g.key} style={{ marginTop: 6 }}>
          {groups.length > 1 ? (
            <div className="text-xs" style={{ marginBottom: 3 }}>
              <b>{g.label}</b> <span className="faint">· {g.symbols.length}종</span>
            </div>
          ) : null}
          <div className="heat-grid">
            {g.symbols.map((sym) => (
              <Tile
                key={sym}
                sym={sym}
                tick={tickOf(ticks, sym)}
                leg={f.per_symbol[sym]}
                {...(openRun ? { openRun } : {})}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
