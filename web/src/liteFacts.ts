/**
 * 첫 화면 포지션 줄의 계산값 — 거래소 값에서 **나눗셈만** 한다(판단 없음).
 *
 * `손절 시` 는 진입가에서 손절선까지의 손익이다. 명목은 지금가로 잰 값이라 계약 x 승수 = 명목 ÷ 지금가로 되돌린다.
 * 수수료 · 미끄러짐은 안 넣는다. 몫이 여럿이면 손절이 몫마다 달라 한 값으로 못 적으므로 null 이다.
 */
import type { LitePosition } from "./api";

function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export type LiteFacts = {
  /** 명목 ÷ 증거금. */
  leverage: number | null;
  /** 몫 하나일 때의 손절선. */
  stop: number | null;
  /** 지금가에서 손절까지 % — 손절 쪽은 음수. */
  stopDistancePct: number | null;
  /** 손절에 닿으면 이 포지션이 진입가 대비 얼마가 되나(USDT · 비용 전). */
  stopPnl: number | null;
  /** 거래소 조건부 수가 이상하면 경고 문구. */
  stopWarning: string | null;
};

/** 한 종목에 조건부가 이보다 많으면 겹쳐 쌓인 것으로 본다(서버 점검 `STOP_COUNT_LIMIT` 과 같은 값). */
const STOP_COUNT_LIMIT = 5;

export function liteFacts(p: LitePosition): LiteFacts {
  const side = p.side === "long" ? 1 : -1;
  const margin = num(p.margin);
  const notional = num(p.notional);
  const entry = num(p.entry);
  const mark = num(p.mark);
  const stop = p.shares.length === 1 ? num(p.shares[0]?.stop) : null;
  const known = stop !== null && stop > 0 ? stop : null;
  return {
    leverage:
      notional !== null && margin !== null && margin > 0
        ? notional / margin
        : null,
    stop: known,
    stopDistancePct:
      known !== null && mark !== null && mark > 0
        ? ((known - mark) / mark) * side * 100
        : null,
    stopPnl:
      known !== null &&
      entry !== null &&
      mark !== null &&
      mark > 0 &&
      notional !== null
        ? (notional / mark) * (known - entry) * side
        : null,
    stopWarning:
      p.exchange_stops === null
        ? null
        : p.exchange_stops === 0
          ? "거래소에 걸린 손절 없음"
          : p.exchange_stops > STOP_COUNT_LIMIT
            ? `거래소 손절 ${p.exchange_stops}건 — 겹쳐 쌓임`
            : null,
  };
}

/** 가장 먼저 든 몫부터 지금까지 몇 시간 · 며칠 — 모르면 빈 값. */
export function heldText(p: LitePosition, nowMs: number): string {
  const times = p.shares
    .map((s) => (s.opened_at ? Date.parse(s.opened_at) : Number.NaN))
    .filter(Number.isFinite);
  if (times.length === 0) return "";
  const hours = (nowMs - Math.min(...times)) / 3_600_000;
  if (hours < 0) return "";
  return hours < 48
    ? `${hours.toFixed(1)}시간`
    : `${(hours / 24).toFixed(1)}일`;
}

/** 다리 이름들 — 같은 이름은 한 번만(엔진 다리 몫 여럿). */
export function legNames(p: LitePosition): string {
  return [...new Set(p.shares.map((s) => s.name))].join(" + ");
}
