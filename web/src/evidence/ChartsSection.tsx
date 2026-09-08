/**
 * ⑤ 차트로 본다 — **백테스트(E1)** 와 **합성 45미래** 를 한 자리에서 고른다 (사용자 2026-09-06: "여기서 선택해서 보는 게 아니야?").
 *
 * ③ 절의 산점도·표에서 누르는 길은 그대로 두고, 여기에도 고르는 길을 둔다 — 같은 상세 패널(`SyntheticDetailPanel`)이 열린다.
 * `/evidence?future=<k>` 로 오면 이 절이 합성 탭으로 시작한다.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BacktestPanel } from "./BacktestDetail";
import { syntheticList, type SyntheticList, type SyntheticRow } from "./charts";
import { mdd, pct } from "./model";
import { SyntheticDetailPanel } from "./SyntheticDetail";

type Tab = "backtest" | "synthetic";

/** `backtestOnly` — 감사 없는 사람: 합성 45미래 탭을 숨기고 백테스트(견본)만 (2026-09-08). */
export function ChartsSection({
  backtestOnly = false,
}: {
  backtestOnly?: boolean;
}) {
  const [params] = useSearchParams();
  const fromUrl = params.get("future");
  const [tab, setTab] = useState<Tab>(
    fromUrl !== null && !backtestOnly ? "synthetic" : "backtest",
  );
  const [list, setList] = useState<SyntheticList | null>(null);
  const [error, setError] = useState("");
  const [k, setK] = useState<number | null>(
    fromUrl !== null && Number.isInteger(Number(fromUrl))
      ? Number(fromUrl)
      : null,
  );

  useEffect(() => {
    if (tab !== "synthetic" || list) return;
    let alive = true;
    syntheticList()
      .then((got) => {
        if (!alive) return;
        setList(got);
        // 아무것도 안 골랐으면 **강제청산이 난 미래** 부터 — 사람이 찾는 것이 그것이다.
        if (k === null) {
          const first =
            got.futures.find((f) => (f.liquidations ?? 0) > 0) ??
            got.futures[0];
          if (first) setK(first.k);
        }
      })
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [tab, list, k]);

  const groups = useMemo(() => {
    const by = new Map<string, SyntheticRow[]>();
    for (const f of list?.futures ?? []) {
      if (!by.has(f.scenario)) by.set(f.scenario, []);
      by.get(f.scenario)?.push(f);
    }
    return [...by.entries()];
  }, [list]);

  const tabClass = (on: boolean) =>
    `rounded-lg border px-3 py-1.5 text-xs font-medium ${
      on
        ? "border-gray-900 bg-gray-900 text-white dark:border-blue-gray-100 dark:bg-blue-gray-100 dark:text-gray-900"
        : "border-blue-gray-200 text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
    }`;

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex flex-wrap items-center gap-2"
        role="tablist"
        aria-label="차트 종류"
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "backtest"}
          className={tabClass(tab === "backtest")}
          onClick={() => setTab("backtest")}
        >
          백테스트 (실측 봉)
        </button>
        {backtestOnly ? null : (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "synthetic"}
            className={tabClass(tab === "synthetic")}
            onClick={() => setTab("synthetic")}
          >
            합성 45미래
          </button>
        )}
      </div>

      {tab === "backtest" ? <BacktestPanel /> : null}

      {tab === "synthetic" ? (
        <div className="flex flex-col gap-4">
          {error ? (
            <div
              className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
              role="alert"
            >
              45미래 목록을 못 읽었다 — {error}
            </div>
          ) : null}
          {list ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <label
                htmlFor="future-pick"
                className="text-blue-gray-600 dark:text-blue-gray-300"
              >
                미래 고르기
              </label>
              <select
                id="future-pick"
                value={k ?? ""}
                onChange={(e) => setK(Number(e.target.value))}
                className="max-w-full rounded-md border border-blue-gray-200 bg-white px-2 py-1 font-mono text-xs dark:border-gray-700 dark:bg-gray-800"
              >
                {groups.map(([scenario, rows]) => (
                  <optgroup key={scenario} label={scenario}>
                    {rows.map((f) => (
                      <option key={f.k} value={f.k}>
                        #{f.k} s{f.seed} · {pct(f.total_pct)} · MDD{" "}
                        {mdd(f.mdd_pct)}
                        {(f.liquidations ?? 0) > 0
                          ? ` · 강제청산 ${f.liquidations}`
                          : ""}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <div className="flex flex-wrap gap-1">
                {list.futures
                  .filter((f) => (f.liquidations ?? 0) > 0)
                  .map((f) => (
                    <button
                      key={f.k}
                      type="button"
                      className={`rounded-md border px-2 py-0.5 text-[11px] ${
                        f.k === k
                          ? "border-loss bg-loss text-white"
                          : "border-loss/40 text-loss hover:bg-loss-wash"
                      }`}
                      onClick={() => setK(f.k)}
                      title={`${f.scenario} s${f.seed} · 강제청산 ${f.liquidations}건`}
                    >
                      #{f.k} 청산 {f.liquidations}
                    </button>
                  ))}
              </div>
              <span className="text-blue-gray-400">
                {list.futures.length}미래 · 블록 {list.block ?? "—"} · 생성{" "}
                {list.generated?.slice(0, 10) ?? "—"}. ③ 절의 점·행을 눌러도
                같은 화면이다.
              </span>
            </div>
          ) : (
            <p className="faint">45미래 목록을 읽는 중…</p>
          )}
          {k !== null ? (
            <div className="rounded-xl border border-[#8957e5]/40 bg-[#8957e5]/5 p-4 dark:bg-gray-900">
              <SyntheticDetailPanel
                pick={{ k }}
                onClose={() => setTab("backtest")}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
