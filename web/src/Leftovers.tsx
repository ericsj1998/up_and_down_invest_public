/**
 * 주인 없는 잔재 — **어느 판도 맡지 않는** 종목의 남은 것들 (사용자 요구 2026-08-21).
 *
 * 🔴 **화면에 없으면 없는 것이 된다.** XRP 조건부 하나가 판이 지워진 뒤로 남아 있었는데
 * 판 목록에도, 판 상세에도, 콘솔 어디에도 나올 자리가 없었다. 거래소 상태를 직접 찔러서야
 * 찾았다 — 판을 안 띄운 종목은 볼 사람이 없다.
 *
 * ⚠️ 남은 조건부는 `size: 0` = *"포지션 전량 닫기"* 다. 같은 종목으로 새 판을 띄우면
 * **그 트리거가 새 포지션을 통째로 닫는다.** 그래서 이 목록은 장식이 아니다.
 *
 * ## 두 갈래를 섞지 않는다
 *
 * ```
 * 주문   답이 하나다 — 거둔다. 손익이 없다
 * 포지션 닫는 순간 손익이 확정된다 — 사람이 정한다
 * ```
 *
 * ⛔ 그래서 **포지션 행에는 거두기 단추를 안 그린다.** 그 주문들은 잔재가 아니라
 * 그 포지션의 **보호막**이고, 거두면 무방비가 된다 (§1.2.1 · 절대 규칙 #3).
 */

import { useCallback, useEffect, useState } from "react";
import { leftovers, sweepLeftovers, type Leftover } from "./api";
import { num } from "./ui";

/** ⚠️ 콘솔 폴링과 같은 박자 — 따로 두면 한쪽만 고쳐진다. */
// 2026-09-05: 서버 CPU 절약 — 잔여물은 15초면 늦지 않다.
const POLL_MS = 15_000;

/**
 * 이 포지션을 지금 닫으면 얼마인가 — **마크 기준 미실현**이다.
 *
 * @param held 거래소 포지션 스냅샷.
 * @returns 사람 말로 쓴 손익. 못 읽으면 null.
 *
 * 🔴 **체결가가 아니다.** 실제 체결은 호가 반대편에서 나고 수수료가 더 빠지므로
 * 이 값은 **항상 낙관 방향**으로 틀린다. 이 프로젝트가 반복해 당한 자리라
 * (비용 없는 주식 결과 · 조작된 +64.77%) 화면에도 그 말을 같이 적는다.
 */
export function unrealised(held: Record<string, string> | null): string | null {
  if (!held) return null;
  // 🔴 **`Number("")` 은 0 이고 유한하다.** 빈 값이 `+0.0000` 으로 그려졌다 —
  //    수익률 칸에서 똑같이 당한 함정이라 여기서 먼저 막는다 (시험이 잡았다).
  if (!held["unrealised_pnl"]) return null;
  const raw = Number(held["unrealised_pnl"]);
  if (!Number.isFinite(raw)) return null;
  const margin = Number(held["margin"]);
  const pct = Number.isFinite(margin) && margin > 0 ? ` (증거금의 ${num((raw / margin) * 100, 1)}%)` : "";
  return `${raw >= 0 ? "+" : ""}${num(raw, 4)} USDT${pct}`;
}

/**
 * 이 행에서 거두기를 눌러도 되나.
 *
 * @param row 잔재 한 줄.
 * @returns 포지션이 없고 거둘 주문이 있으면 true.
 *
 * ⛔ 순수 함수로 뺀다 — 이 판정이 틀리면 **살아 있는 포지션의 손절을 지운다.**
 * 그림이 아니라 규칙이므로 시험이 잡아야 한다.
 */
export function canSweep(row: Leftover): boolean {
  return row.kind === "주문" && row.orders.length > 0;
}

export function Leftovers() {
  const [rows, setRows] = useState<Leftover[]>([]);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try {
      setRows((await leftovers()).rows);
    } catch {
      // ⚠️ 조회 실패로 화면을 비우지 않는다 — "잔재 없음" 으로 보이면 안 된다.
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (rows.length === 0) return null;

  const sweep = async (symbol: string) => {
    setBusy(symbol);
    try {
      const done = await sweepLeftovers(symbol);
      setNote(done.swept.length ? `${symbol} — ${done.swept.join(" · ")} 거뒀다` : `${symbol} — 거둘 것이 없었다`);
      await load();
    } catch (err) {
      setNote(`${symbol} — 못 거뒀다: ${String(err)}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="card" style={{ borderColor: "var(--warn, #b58900)" }}>
      <h3>주인 없는 잔재 {rows.length}건</h3>
      {/* 🔴 왜 위험한지를 목록 위에 적는다 — 숫자만 보면 치울 이유를 모른다. */}
      <p className="card-hint">
        어느 판도 맡지 않는 종목이다. 남은 조건부는 <b>포지션 전량 닫기</b> 트리거라, 같은
        종목으로 새 판을 띄우면 <b>새 포지션을 통째로 닫는다</b>.
      </p>
      <table>
        <thead>
          <tr>
            <th>종목</th>
            <th>남은 것</th>
            <th>상태</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.symbol}>
              <td>
                <b>{row.symbol}</b>
              </td>
              <td>
                {row.orders.length === 0
                  ? "—"
                  : row.orders.map((item) => `${item.kind} ${item.at}`).join(" · ")}
              </td>
              <td>
                {row.kind === "포지션" ? (
                  <>
                    포지션 {row.position?.["size"]} 계약 · 미실현{" "}
                    <b>{unrealised(row.position) ?? "—"}</b>
                    {/* ⚠️ 마크 기준이라는 말을 빼면 사람이 확정 손익으로 읽는다. */}
                    <div className="card-hint">
                      마크 기준이다 — 실제 체결은 호가 반대편이고 수수료가 더 빠진다
                    </div>
                  </>
                ) : (
                  "포지션 없음"
                )}
              </td>
              <td>
                {canSweep(row) ? (
                  <button
                    className="btn small"
                    disabled={busy === row.symbol}
                    onClick={() => void sweep(row.symbol)}
                  >
                    {busy === row.symbol ? "거두는 중" : "거두기"}
                  </button>
                ) : (
                  // ⛔ 포지션 행에는 거두기를 안 그린다 — 그 주문들이 유일한 보호막이다.
                  <span className="card-hint">아래 포지션 표에서 닫는다</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {note && <p className="card-hint">{note}</p>}
    </section>
  );
}
