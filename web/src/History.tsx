/**
 * 체결 이력 — **무슨 일이 있었나** (사용자 요구 2026-08-20).
 *
 * 사용자 지적: *"지금 체결 이력이 너무 보기 힘들어."* 요구는 다섯이었다:
 *
 * ```
 * RUN 열에 고유 id 말고 종목명 · 판을 지워도 남게
 * 방향을 롱 / 숏 으로
 * 실현 손익에 단위 (퍼센트가 아니라 USDT 다)
 * 레버리지도 판을 지워도 남게
 * 주문을 누르면 펼쳐서 진입가 · 손절가 · 1차익절가 · 익절 + 각 시각
 * ```
 *
 * 🔴 **거래소 행에는 체결가와 실현 손익뿐이다.** 계획(손절선·1차 익절·목표)과 배율은
 * 우리 원장에만 있고, 조인 열쇠는 주문 이름에 박은 매매 id 다 (T18 ⑤).
 *
 * ⭐ **판을 지워도 남는다.** 지우는 것은 `closed_at` 을 찍는 일이라 행이 그대로다.
 */

import { Fragment, useState } from "react";
import type { Exchange, RunTag, TradePlan } from "./api";
import { num, orderKind, runOf, tradeOf, whenSec } from "./ui";

/** Gate 의 `finish_as` — **끝난 이유**다. 상태가 아니라 결말이라 사람 말로 옮긴다. */
const FINISH: Record<string, { label: string; why: string }> = {
  filled: { label: "체결", why: "전량 채워져 끝났다 — 정상 종료다" },
  cancelled: { label: "취소", why: "우리가 거뒀거나 계획이 사라져 거둬졌다" },
  liquidated: {
    label: "강제청산",
    why: "증거금이 모자라 거래소가 강제로 닫았다",
  },
  ioc: { label: "즉시체결", why: "즉시 체결 조건이라 남은 수량은 버려졌다" },
  auto_deleveraged: {
    label: "자동디레버리징",
    why: "거래소가 반대편 손실을 메우려 내 포지션을 줄였다",
  },
  reduce_only: {
    label: "줄이기 거절",
    why: "줄이는 주문인데 줄일 포지션이 없어 거절됐다",
  },
  position_closed: {
    label: "포지션 없음",
    why: "포지션이 이미 닫혀 이 주문이 뜻을 잃었다",
  },
  stp: { label: "자전 방지", why: "내 주문끼리 체결될 뻔해 거래소가 막았다" },
};

/** 포지션이 **롱이었나 숏이었나** — 주문의 매수/매도가 아니다.
 *
 * 🔴 사용자 요구: *"방향도 롱, 숏 으로 표기해줬으면 좋겠어."*
 *
 * ⚠️ 줄이는 주문은 **반대로 읽어야** 한다 — 롱을 닫는 것은 매도이고, 매수로 닫히는
 * 것은 숏이다. 매수/매도를 그대로 보여 주면 손절 발동이 전부 "매수" 로 보인다.
 */
export function sideOf(size: number, reduceOnly: boolean): "롱" | "숏" {
  const long = reduceOnly ? size < 0 : size > 0;
  return long ? "롱" : "숏";
}

/**
 * 이 주문으로 **몇 % 벌었나** — 서버가 준 값을 읽기만 한다 (사용자 신고 2026-08-20).
 *
 * 🔴 사용자 신고: *"이거는 실현 손익이 마이너스인데, 어떻게 돈을 번거야?"*
 *
 * 화면이 **원장 진입가로** 재고 옆 칸은 **거래소 실현**을 보여 줘서 부호가 갈렸다.
 * 둘 다 사실이었다 — 가격으로는 이겼고 수수료로 졌다:
 *
 * ```
 * 가격 변동  +0.0702%  x 20배 =  +1.40%    수수료 전
 * 거래소 실현              -0.5866 USDT = -0.60%   수수료 포함
 * ```
 *
 * ⇒ **계산을 화면에서 없앴다.** `pnl` 과 **같은 근거**(거래소 청산 기록)에서 나와야
 *   두 칸이 같은 것을 재고, 그 계산에는 계약 승수가 필요해 서버에만 있다.
 */
export function shownPct(row: {
  gain_pct?: string;
  move_pct?: string;
}): { net: number; move: number | null } | null {
  // ⛔ 못 읽으면 **지어내지 않는다.** 0 으로 채우면 "본전" 으로 읽힌다.
  // ⚠️ 빈 문자열을 따로 막는다 — `Number("")` 는 0 이고 유한하다.
  if (!row.gain_pct) return null;
  const net = Number(row.gain_pct);
  if (!Number.isFinite(net)) return null;
  const move = Number(row.move_pct);
  return { net, move: Number.isFinite(move) ? move : null };
}

/**
 * **수수료가 얼마나 먹었나** 한 문장 — 툴팁에 쓴다.
 *
 * 🔴 이 문장이 사용자가 부딪힌 모순의 답이다. 가격으로는 이겼는데 수수료로 졌고,
 * **20배에서 왕복 수수료가 증거금의 2%p** 다.
 */
export function costBite(
  gain: { net: number; move: number | null },
  leverage: string,
): string {
  const lever = Number(leverage);
  if (gain.move === null || !Number.isFinite(lever) || lever <= 0) {
    return "증거금 대비이고 수수료가 들어간 값이다";
  }
  const gross = gain.move * lever;
  return (
    `증거금 대비이고 **수수료가 들어간** 값이다 · ` +
    `가격은 ${gain.move >= 0 ? "+" : ""}${gain.move.toFixed(3)}% 움직였고 ` +
    `배율 ${lever}x 를 곱하면 ${gross >= 0 ? "+" : ""}${gross.toFixed(2)}% 인데, ` +
    `그 차이 ${Math.abs(gross - gain.net).toFixed(2)}%p 가 수수료가 먹은 몫이다`
  );
}

/**
 * 아직 **안 닫힌 진입**의 미실현 수익률 — 지금 마크가로 잰 증거금 대비 % 다.
 *
 * 🔴 사용자 요구 2026-08-23: *"주문이 미실현일 때도 손익률이 얼마인 상태인지 찍어줘."*
 *
 * ⚠️ **실현과 다른 값이다.** 거래소 실현(`pnl`)은 청산 기록에서 수수료까지 든 값이고,
 * 이것은 `unrealised_pnl`(마크 기준·수수료 전)을 증거금으로 나눈 값이다. 그래서 화면은
 * `미실현` 이라 못박고 두 칸을 섞지 않는다. 포지션은 하나라 **포지션 전체 기준**이다 —
 * 분할 진입이면 같은 포지션의 여러 진입 줄에 같은 값이 뜬다.
 *
 * ⛔ 증거금·미실현을 못 읽으면 **지어내지 않는다** — null 을 돌려주고 화면은 빈칸이다.
 */
export function unrealizedOf(
  pos: (Record<string, string> & { symbol: string }) | undefined,
): { pct: number; usdt: number } | null {
  if (!pos) return null;
  const usdt = Number(pos.unrealised_pnl);
  const margin = Number(pos.margin);
  if (!Number.isFinite(usdt) || !Number.isFinite(margin) || margin <= 0) {
    return null;
  }
  return { pct: (usdt / margin) * 100, usdt };
}

/** 계획가 한 줄 — 없으면 지어내지 않는다. */
function Cell({
  name,
  price,
  at,
  tone,
}: {
  name: string;
  price?: string | null;
  at?: string | null;
  tone?: string;
}) {
  return (
    <div>
      <span className="faint">{name}</span>
      <br />
      <b className={tone}>{price ? num(price, 4) : "—"}</b>
      <br />
      <span className="faint">{at ? whenSec(at) : "—"}</span>
    </div>
  );
}

/** 펼친 상세 — 계획과 시각을 나란히. */
function Detail({ plan }: { plan: TradePlan | undefined }) {
  if (!plan) {
    return (
      <p className="faint">
        원장에서 이 주문의 계획을 못 찾았다 — 조건부 발동(<code>ao-</code>)은
        Gate 가 만든 주문이라 이름으로 이을 수 없고, 규격 이전 주문도 그렇다.
        <b> 지어내지 않는다.</b>
      </p>
    );
  }
  return (
    <>
      <p className="faint">
        {plan.symbol} · {plan.playbook} · {plan.direction} · 배율{" "}
        {num(plan.leverage, 0)}x · 결과 <b>{plan.outcome}</b>
        {plan.half_by ? ` (반익 사유: ${plan.half_by})` : ""}
      </p>
      <div className="row" style={{ gap: "22px", flexWrap: "wrap" }}>
        {/* ⭐ 계획한 값과 **실제로 난 값**을 같은 줄에 둔다 — 갈리는 것이 곧 사건이다. */}
        <Cell name="진입" price={plan.entry} at={plan.opened_at} />
        <Cell name="손절선(계획)" price={plan.stop} tone="loss" />
        <Cell
          name="1차 익절"
          price={plan.first}
          at={plan.half_at}
          tone="gain"
        />
        <Cell name="목표 익절" price={plan.target} tone="gain" />
        <Cell name="실제 청산" price={plan.exit} at={plan.closed_at} />
      </div>
      {plan.half_price ? (
        <p className="faint">
          1차 익절은 <b>{num(plan.half_price, 4)}</b> 에 실제로 났다 — 계획
          {num(plan.first, 4)} 과 다르면 그 차이가 슬리피지다.
        </p>
      ) : null}
    </>
  );
}

export function History({
  rows,
  plans,
  runs,
  positions = [],
}: {
  rows: Exchange["history"];
  plans: Record<string, TradePlan>;
  runs: Record<string, RunTag>;
  /** 🔴 **열려 있는 포지션들** — 안 닫힌 진입 줄에 미실현 손익률을 찍으려면 필요하다. */
  positions?: Exchange["positions"];
}) {
  const [open, setOpen] = useState("");
  // ⭐ 종목 → 지금 열린 포지션. 미실현은 포지션에만 있고 주문 이력엔 없다.
  const held: Record<string, Exchange["positions"][number]> = {};
  for (const pos of positions) held[pos.symbol] = pos;

  if (!rows.length) return <p className="empty">체결 이력이 없다</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>종목</th>
          <th>방향</th>
          <th className="num">체결가</th>
          {/* ⭐ **내 돈 기준이다** (사용자 요구 2026-08-20). 가격 변동률을 놓으면 20배
              에서 20배 작게 보여, 옆의 USDT 와 다른 것을 재게 된다. */}
          {/* 🔴 분모 이름표 (사용자 확정 2026-09-06): 이 열만 **증거금 대비**(배율 반영 · 매매 단위 RR 통계용)다.
              상단 카드는 계좌 총액, RUN 표·펀드 표는 판 예산 — 같은 모양의 % 가 섞이지 않게 여기만 이름을 단다. */}
          <th
            className="num"
            title="한 매매의 손익 ÷ 그 매매의 증거금 — 배율이 반영된 수익률(RR 통계용). 계좌·판 기준 % 와 다르다"
          >
            수익률 (증거금 대비 · 배율 반영)
          </th>
          {/* 🔴 **손익은 청산 줄에만 있다.** 주문 이력이 주는 것은 체결가까지다.
              ⚠️ 줄이지 않는 주문(진입)에는 손익이 없다 — 0 으로 채우면 "본전" 으로
              읽힌다. 빈칸이 맞다. */}
          <th className="num">실현 손익 (USDT)</th>
          <th
            className="num"
            title="이 매매의 노출 = 명목 ÷ 판 예산 (= 건당 리스크 r ÷ 손절 거리). 1 아래면 예산보다 작게 들어간 것이다. 거래소에 건 배율(예: 6x)은 판 카드에 있다"
          >
            노출
          </th>
          <th>결말</th>
          <th>시각</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const qty = Number(row.size);
          const left = Number(row.left);
          const partial = Number.isFinite(left) && left !== 0;
          const reduceOnly = row.is_reduce_only === "True";
          const tag = runOf(row.text || "");
          const plan = plans[tradeOf(row.text || "")];
          // ⭐ **종목은 세 곳에서 온다** — 거래소 행 · 원장 계획 · 판 표식. 하나라도
          //   있으면 쓴다. 판을 지워도 남는 것이 요구의 핵심이다.
          const symbol = row.symbol ?? plan?.symbol ?? runs[tag]?.symbol ?? "—";
          // ⭐ 전체 범위에서는 어느 거래소의 체결인지 표식이 필요하다 (2026-08-26).
          const mktTag =
            row.market === "BINANCE" ? (
              <span style={{ color: "#d29922", marginRight: 4 }}>BN</span>
            ) : row.market === "GATE" ? (
              <span className="faint" style={{ marginRight: 4 }}>
                GT
              </span>
            ) : null;
          // ⛔ 배율은 거래소 행에 없다 (실측: 늘 빈 문자열). 원장에서 찾는다.
          const lever = plan?.leverage ?? runs[tag]?.leverage ?? row.leverage;
          const done = FINISH[row.finish_as || ""] ?? {
            label: row.finish_as || row.status,
            why: "거래소가 준 값을 그대로 둔다 — 모르는 것을 옮기지 않는다",
          };
          const side = sideOf(qty, reduceOnly);
          const kind = orderKind(row.text || "", reduceOnly);
          const shown = open === row.id;
          // ⭐ **서버가 거래소 청산 기록에서 계산해 준다** — 옆 칸의 `pnl` 과 같은
          //   근거라야 두 칸이 같은 것을 잰다 (사용자 신고 2026-08-20).
          const gain = shownPct(row);
          // ⭐ **미실현 손익률** (사용자 요구 2026-08-23). 실현이 아직 없고(진입 줄),
          //    이 매매가 안 닫혔고, 그 종목에 포지션이 열려 있을 때만 지금 값을 찍는다.
          //    계획이 있으면 `closed_at` 이 닫힘의 근거다 — 없으면 포지션 유무로 본다.
          const tradeOpen = plan ? !plan.closed_at : Boolean(held[symbol]);
          const live =
            gain === null && !reduceOnly && tradeOpen
              ? unrealizedOf(held[symbol])
              : null;
          return (
            // ⚠️ **열쇠는 조각(Fragment)에 단다** — 한 줄이 두 `<tr>` 이 되므로 안쪽에
            //    달면 React 가 목록을 다시 그릴 때마다 펼친 줄이 엉뚱한 데로 옮겨 간다.
            <Fragment key={row.id}>
              <tr
                onClick={() => setOpen(shown ? "" : row.id)}
                style={{ cursor: "pointer" }}
                title="누르면 계획과 시각이 펼쳐진다"
              >
                <td className="mono">
                  {mktTag}
                  {symbol}
                  {/* ⚠️ 판 표식은 **작게 남긴다** — 종목이 앞이지만, 같은 종목을 여러
                      판이 지나갔을 때 가를 유일한 근거다. */}
                  {tag ? <span className="faint"> · {tag}</span> : null}
                </td>
                <td className={side === "롱" ? "gain" : "loss"}>
                  {shown ? "▾" : "▸"} {side} {Math.abs(qty)}
                  {kind ? <span className="faint"> · {kind}</span> : null}
                </td>
                <td className="num">{num(row.fill_price || row.price, 4)}</td>
                {/* ⛔ **진입 줄에는 수익률이 없다** — 아직 안 났다. 그리고 계획을 못
                    찾은 줄(`ao-`)도 빈칸이다: 진입가를 모르면 잴 수가 없다. */}
                <td
                  className={`num ${
                    gain !== null
                      ? gain.net >= 0
                        ? "gain"
                        : "loss"
                      : live !== null
                        ? live.pct >= 0
                          ? "gain"
                          : "loss"
                        : ""
                  }`}
                  title={
                    gain !== null
                      ? costBite(gain, lever ?? "")
                      : live !== null
                        ? `미실현 — 지금 마크가로 잰 증거금 대비다 (포지션 전체 · 수수료·펀딩 전이라 청산하면 조금 준다) · ${live.usdt >= 0 ? "+" : ""}${live.usdt.toFixed(4)} USDT`
                        : reduceOnly
                          ? "거래소 청산 기록을 못 찾아 못 잰다 — 지어내지 않는다"
                          : "줄이지 않는 주문에는 실현 수익률이 없다 — 포지션이 닫혀야 난다"
                  }
                >
                  {gain !== null ? (
                    `${gain.net >= 0 ? "+" : ""}${gain.net.toFixed(2)}%`
                  ) : live !== null ? (
                    <>
                      {`${live.pct >= 0 ? "+" : ""}${live.pct.toFixed(2)}%`}
                      {/* ⚠️ 실현과 섞이지 않게 **미실현**이라 못박는다. */}
                      <span className="faint"> 미실현 · 증거금 대비</span>
                    </>
                  ) : (
                    "—"
                  )}
                  {/* 🔴 이름이 아니라 **시각 근접으로 추정해** 붙인 줄임을 밝힌다. */}
                  {row.pnl_guessed ? (
                    <span
                      className="faint"
                      title="주문 이름으로 못 이어 시각으로 추정했다"
                    >
                      {" "}
                      ?
                    </span>
                  ) : null}
                </td>
                <td
                  className={`num ${
                    row.pnl === undefined || row.pnl === ""
                      ? ""
                      : Number(row.pnl) >= 0
                        ? "gain"
                        : "loss"
                  }`}
                  title={
                    row.pnl
                      ? "거래소가 말하는 실현 손익 — USDT 이고 퍼센트가 아니다. 수수료·펀딩은 빠져 있다"
                      : "줄이지 않는 주문에는 손익이 없다. Gate 는 부분 체결된 청산에도 안 준다"
                  }
                >
                  {row.pnl ? num(row.pnl, 4) : "—"}
                </td>
                <td className="num faint">
                  {/* ⭐ 이 값은 **노출**(명목 ÷ 판 예산 = r ÷ 손절 거리)이라 1 아래도 정상이다 — 0.38x 를 정수로
                      반올림해 "0x" 로 보였다 (사용자 신고 2026-09-08). 거래소 배율(6x)은 판 카드에 있다. */}
                  {lever && Number(lever) > 0
                    ? `${num(lever, Number(lever) >= 10 ? 0 : Number(lever) >= 1 ? 1 : 2)}x`
                    : "—"}
                </td>
                <td className={partial ? "loss" : "muted"} title={done.why}>
                  {/* ⚠️ `finished` 는 "끝났다" 이지 "다 채워졌다" 가 아니다.
                      🔴 그런데 "미체결 N 남음" 은 **아직 걸려 있다**로 읽힌다
                      (사용자 지적 2026-08-30: *"미체결 몇 남음이 떠있는데요?"*).
                      이 표는 **끝난 주문의 이력**이라 남아 있는 것이 아니다 —
                      끝났다는 것과 몇이 못 채워졌는지를 같이 적는다. */}
                  {partial
                    ? `${left} 미체결로 종료 · ${done.label}`
                    : done.label}
                </td>
                <td className="faint">{whenSec(row.finish_time)}</td>
              </tr>
              {shown ? (
                <tr>
                  <td colSpan={8} className="muted">
                    <Detail plan={plan} />
                    <p className="faint">
                      주문 {row.id.slice(-8)} · 멱등키{" "}
                      <code>{row.text || "—"}</code>
                    </p>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
