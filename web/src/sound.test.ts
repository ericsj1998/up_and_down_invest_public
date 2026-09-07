/**
 * 매매 소리 규칙 — **언제 울리고 언제 조용한가**.
 *
 * 🔴 야간 음소거가 자정을 넘는 구간이라 `from <= h && h < to` 로 쓰면 22~8 이 영원히
 * 거짓이 되어 **밤새 울린다.** 그 실수는 새벽에만 드러난다.
 */

import { describe, expect, it } from "vitest";
import { DEFAULTS, audible, freshFills, loudest, quietNow, ringOf } from "./sound";

describe("quietNow — 조용히 할 시간인가", () => {
  it("자정을 안 넘는 구간 (기본 0~8시)", () => {
    expect(quietNow(0, 0, 8)).toBe(true);
    expect(quietNow(7, 0, 8)).toBe(true);
    // ⚠️ 끝 시각은 **포함하지 않는다** — 8시에는 다시 울린다.
    expect(quietNow(8, 0, 8)).toBe(false);
    expect(quietNow(13, 0, 8)).toBe(false);
  });

  it("자정을 넘는 구간 (22~8시)", () => {
    // 🔴 여기가 틀리면 밤새 울린다.
    expect(quietNow(22, 22, 8)).toBe(true);
    expect(quietNow(23, 22, 8)).toBe(true);
    expect(quietNow(0, 22, 8)).toBe(true);
    expect(quietNow(7, 22, 8)).toBe(true);
    expect(quietNow(8, 22, 8)).toBe(false);
    expect(quietNow(12, 22, 8)).toBe(false);
  });

  it("시작과 끝이 같으면 구간이 없다", () => {
    // ⚠️ "24시간 내내" 로 읽으면 켜 둔 사람이 아무 소리도 못 듣고, 이유가 안 보인다.
    for (const hour of [0, 5, 12, 23]) expect(quietNow(hour, 3, 3)).toBe(false);
  });
});

describe("audible — 지금 울려도 되나", () => {
  // 2026-08-20 02:00 KST = 2026-08-19 17:00 UTC
  const night = Date.parse("2026-08-19T17:00:00Z");
  const noon = Date.parse("2026-08-20T03:00:00Z"); // 12:00 KST

  it("꺼져 있으면 언제든 안 울린다", () => {
    expect(audible({ ...DEFAULTS, on: false }, noon)).toBe(false);
  });

  it("켜져 있고 낮이면 울린다", () => {
    expect(audible({ ...DEFAULTS, on: true }, noon)).toBe(true);
  });

  it("켜져 있어도 야간 음소거 구간이면 조용하다", () => {
    expect(audible({ ...DEFAULTS, on: true }, night)).toBe(false);
  });

  it("야간 음소거를 끄면 밤에도 울린다", () => {
    expect(audible({ ...DEFAULTS, on: true, night: false }, night)).toBe(true);
  });

  it("기본값은 꺼짐이다", () => {
    // ⭐ 켜진 채로 시작하면 화면을 처음 여는 사람이 놀란다.
    expect(DEFAULTS.on).toBe(false);
    expect(DEFAULTS.night).toBe(true);
    expect([DEFAULTS.from, DEFAULTS.to]).toEqual([0, 8]);
  });
});

describe("ringOf — 무슨 소리인가", () => {
  const out = { is_reduce_only: "True" };

  it("줄이는 주문이 아니면 진입이다", () => {
    expect(ringOf({})).toBe("entry");
    expect(ringOf({ pnl: "" })).toBe("entry");
  });

  it("이익이면 gain, 손실이면 loss", () => {
    expect(ringOf({ ...out, pnl: "1.24" })).toBe("gain");
    expect(ringOf({ ...out, pnl: "0" })).toBe("gain");
    expect(ringOf({ ...out, pnl: "-3.5" })).toBe("loss");
  });

  it("🔴 손익을 못 읽은 청산이 진입 소리로 나면 안 된다", () => {
    // 실측 2026-08-20: 체결 200건 중 21건이 줄이는 주문인데 pnl 이 비어 있었다.
    // Gate 는 **부분 체결된 청산에 pnl 을 안 준다.**
    expect(ringOf(out)).toBe("exit");
    expect(ringOf({ ...out, pnl: "" })).toBe("exit");
    expect(ringOf({ ...out, pnl: "n/a" })).toBe("exit");
  });

  it("🔴 조건부 발동(ao-)은 손절이다 — 진입 소리로 나면 가장 나쁜 거짓말이다", () => {
    // 실측: ETH 85계약 x 3건이 ao- 로 체결됐는데 pnl 이 없어 진입 소리가 났다.
    expect(ringOf({ text: "ao-2090406159629418496" })).toBe("exit");
    expect(ringOf({ text: "ao-209040", pnl: "-9.1" })).toBe("loss");
  });

  it("⛔ 모르는 것을 이겼다/졌다로 지어내지 않는다", () => {
    // 나간 것은 사실이고 방향은 아직 사실이 아니다 — 중립 소리가 그 상태 그대로다.
    expect(ringOf(out)).not.toBe("gain");
    expect(ringOf(out)).not.toBe("loss");
  });

  it("⚠️ is_reduce_only 는 선언만 돼 있고 안 읽혔다 — 이제 읽는다", () => {
    // 같은 행을 화면(orderKind)은 옳게 읽고 있었다. 규칙이 두 벌이었던 것이 병이다.
    expect(ringOf({ is_reduce_only: "False", pnl: "" })).toBe("entry");
    expect(ringOf({ is_reduce_only: "True", pnl: "" })).toBe("exit");
  });
});

describe("freshFills — 새로고침에 옛 체결이 울리지 않는다", () => {
  const row = (id: string) => ({ id, finish_as: "filled" });
  const old = [row("3"), row("2"), row("1")]; // 이력은 최신이 앞이다

  it("아직 안 왔으면 아무것도 하지 않는다", () => {
    // 🔴 여기가 이번 버그의 뿌리다. 응답 전  로 심으면 곧 오는 것이 다 새것이 된다.
    expect(freshFills(null, null)).toBeNull();
    expect(freshFills(null, new Set(["1"]))).toBeNull();
  });

  it("첫 응답은 심는 것이고 울리지 않는다", () => {
    const step = freshFills(old, null);
    expect(step?.ring).toEqual([]);
    expect([...(step?.seen ?? [])].sort()).toEqual(["1", "2", "3"]);
  });

  it("새로고침 흐름 전체 — 심고 나면 옛 것은 다시 안 운다", () => {
    // ① 첫 렌더: 응답 전이다
    expect(freshFills(null, null)).toBeNull();
    // ② 첫 응답: 이력 3건이 한꺼번에 왔지만 **심는 것**이다
    const seeded = freshFills(old, null);
    expect(seeded?.ring).toEqual([]);
    // ③ 다음 폴링: 같은 이력이면 조용하다
    expect(freshFills(old, seeded?.seen ?? null)?.ring).toEqual([]);
  });

  it("진짜 새 체결만 울린다 — 옛 것부터", () => {
    const next = [row("5"), row("4"), ...old];
    const step = freshFills(next, new Set(["1", "2", "3"]));
    // ⚠️ 순서가 뒤집혀야 한다 — 이력은 최신이 앞이다.
    expect(step?.ring.map((item) => item.id)).toEqual(["4", "5"]);
    expect([...(step?.seen ?? [])].sort()).toEqual(["1", "2", "3", "4", "5"]);
  });

  it("안 채워진 주문은 울리지 않는다", () => {
    const mixed = [{ id: "9", finish_as: "cancelled" }, row("8")];
    const step = freshFills(mixed, new Set<string>());
    expect(step?.ring.map((item) => item.id)).toEqual(["8"]);
  });

  it("이력이 진짜로 비어 있으면 심고, 첫 체결은 울린다", () => {
    // ⭐  는 "없다" 이지 "안 왔다" 가 아니다 — 새 계정의 첫 체결을 삼키면 안 된다.
    const seeded = freshFills([], null);
    expect(seeded?.ring).toEqual([]);
    expect(freshFills([row("1")], seeded?.seen ?? null)?.ring.map((i) => i.id)).toEqual(["1"]);
  });
});

describe("loudest — 밀린 체결 중 무엇을 울릴 것인가", () => {
  // 체결 한 줄을 만드는 지름길. `is_reduce_only` 가 "나가는 주문인가" 를 가른다.
  const enter = { id: "1", is_reduce_only: "False" };
  const exit = { id: "2", is_reduce_only: "True", pnl: "" };
  const gain = { id: "3", is_reduce_only: "True", pnl: "12.5" };
  const loss = { id: "4", is_reduce_only: "True", pnl: "-8.0" };

  it("아무것도 없으면 울릴 것도 없다", () => {
    expect(loudest([])).toBeNull();
  });

  it("한 건이면 그것이다", () => {
    expect(loudest([gain])).toBe(gain);
  });

  it("🔴 손절이 섞여 있으면 손절이 이긴다 — 마지막이 진입이어도", () => {
    // 예전 코드는 전부 울렸고, "최신만" 으로 고치면 여기서 진입 소리가 난다.
    // 그러면 손절이 **소리 없이** 지나간다 (2026-08-20 에 이미 겪은 거짓말).
    expect(loudest([gain, loss, enter])).toBe(loss);
  });

  it("손절이 없으면 익절이 중립 청산·진입을 이긴다", () => {
    expect(loudest([enter, exit, gain])).toBe(gain);
  });

  it("나가는 것이 없으면 진입이다", () => {
    expect(loudest([enter, { id: "5", is_reduce_only: "False" }])).toEqual(enter);
  });

  it("무게가 같으면 먼저 온 것 — 무엇이 울릴지 예측할 수 있어야 한다", () => {
    const first = { id: "a", is_reduce_only: "True", pnl: "-1" };
    const later = { id: "b", is_reduce_only: "True", pnl: "-2" };
    expect(loudest([first, later])).toBe(first);
  });
});
