/**
 * "청산 난 미래" 카드 — **전체 매매 대비 확률**로 읽게 한다 (사용자 2026-09-06: "이것만 있으면 청산 많이 당한 것 같으니까").
 *
 * 분모(매매 수)는 결과 표에 없다 — 상세 세트(`/evidence/synthetic` · `synth_detail`)에만 있다. 그래서 세상의 원문 파일(`source`)이
 * 상세 세트의 기준표(`basis`)와 같을 때만 확률과 종목별 분해를 붙이고, 아니면 건수만 적고 어느 탭에서 볼 수 있는지 말한다.
 * 카드를 누르면 종목별·시나리오별 분해가 펼쳐진다.
 */
import { useEffect, useState } from "react";
import { syntheticList, type SyntheticList } from "./charts";
import type { World } from "./model";

let cached: Promise<SyntheticList> | null = null;
function listOnce(): Promise<SyntheticList> {
  cached ??= syntheticList().catch((exc: unknown) => {
    cached = null;
    throw exc;
  });
  return cached;
}

export function LiquidationFact({ world }: { world: World }) {
  const [list, setList] = useState<SyntheticList | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    listOnce()
      .then((got) => alive && setList(got))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const s = world.summary;
  const matched = list && world.source && list.basis === world.source ? list.summary ?? null : null;
  const base = `${s.liquidated_runs}/${s.n} 미래 · ${s.liquidations_total}건`;

  if (!matched) {
    return (
      <div className="rounded-xl border border-blue-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
        <div className="text-xs text-blue-gray-500 dark:text-blue-gray-300">청산 난 미래</div>
        <div className="mt-1 break-words font-mono text-base font-semibold text-blue-gray-900 dark:text-white">{base}</div>
        <div className="mt-1 text-[11px] leading-snug text-blue-gray-400">
          {failed
            ? "매매 수를 못 읽어 확률은 비운다"
            : list
              ? "이 표엔 매매 수가 없다 — 전체 매매 대비 확률·종목별은 '2026-09-06 재생성' 탭에서"
              : "매매 수를 읽는 중…"}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-blue-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
      <button type="button" className="w-full text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <div className="flex items-center justify-between text-xs text-blue-gray-500 dark:text-blue-gray-300">
          <span>청산 발생 확률 (전체 매매 대비)</span>
          <span aria-hidden="true">{open ? "▴" : "▾"}</span>
        </div>
        <div className={`mt-1 break-words font-mono text-base font-semibold ${matched.liquidations ? "text-loss" : "text-blue-gray-900 dark:text-white"}`}>
          {matched.pct.toFixed(2)}%
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-blue-gray-500 dark:text-blue-gray-300">
          {matched.liquidations}건 / 매매 {matched.trades.toLocaleString("ko-KR")}건 · {base}
        </div>
      </button>
      {open ? (
        <div className="mt-3 border-t border-blue-gray-50 pt-2 text-[11px] dark:border-gray-800">
          <div className="mb-1 font-semibold text-blue-gray-700 dark:text-blue-gray-200">어느 종목에서</div>
          <table className="w-full text-left">
            <thead>
              <tr className="text-blue-gray-400">
                <th className="py-0.5 pr-2 font-normal">종목</th>
                <th className="py-0.5 pr-2 text-right font-normal">청산</th>
                <th className="py-0.5 pr-2 text-right font-normal">매매</th>
                <th className="py-0.5 pr-2 text-right font-normal">확률</th>
                <th className="py-0.5 text-right font-normal">미래 수</th>
              </tr>
            </thead>
            <tbody>
              {matched.by_symbol.map((r) => (
                <tr key={r.symbol} className={r.liquidations ? "" : "text-blue-gray-400"}>
                  <td className="py-0.5 pr-2 font-medium">{r.symbol.replace("USDT", "")}</td>
                  <td className={`py-0.5 pr-2 text-right font-mono ${r.liquidations ? "text-loss" : ""}`}>{r.liquidations}</td>
                  <td className="py-0.5 pr-2 text-right font-mono">{r.trades.toLocaleString("ko-KR")}</td>
                  <td className="py-0.5 pr-2 text-right font-mono">{r.pct.toFixed(2)}%</td>
                  <td className="py-0.5 text-right font-mono">{r.futures}/{matched.futures}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mb-1 mt-3 font-semibold text-blue-gray-700 dark:text-blue-gray-200">어느 시나리오에서</div>
          <div className="flex flex-wrap gap-1">
            {matched.by_scenario.map((r) => (
              <span
                key={r.scenario}
                className={`rounded-md px-2 py-0.5 ${r.liquidations ? "bg-loss-wash text-loss" : "bg-blue-gray-50 text-blue-gray-500 dark:bg-gray-800"}`}
              >
                {r.scenario} {r.liquidations}건 · {r.futures_hit}/{r.futures}미래
              </span>
            ))}
          </div>
          <p className="mt-2 leading-snug text-blue-gray-400">
            확률 = 강제청산 / 그 세상의 전체 매매(손절·약화 청산·익절 포함). 미래 하나를 고르면 각 청산의 시각·가격·손절선 넘김이 표로 나온다.
          </p>
        </div>
      ) : null}
    </div>
  );
}
