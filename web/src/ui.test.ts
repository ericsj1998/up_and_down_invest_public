/**
 * 표시 규칙 — **모르는 것과 아는 것을 가른다.**
 *
 * 🔴 이 프로젝트가 하루에 네 번 같은 실수를 했다: 잔고를 못 읽은 것이 "잔고 0" 으로,
 * 미점검이 정상으로, 안 본 축이 멈춘 축으로, 비교 불가가 불일치로 보였다.
 */

import { describe, expect, it } from "vitest";
import { UNKNOWN_RUN, ago, aliveness, clock, confirmedNaked, frameSeconds, frameWord, num, orderKind, pct, runOf, splitFindings, stillWorrying, when, whenSec } from "./ui";

describe("num — 모르는 값", () => {
  it("null·undefined·빈 문자열은 — 다. 0 이 아니다", () => {
    expect(num(null)).toBe("—");
    expect(num(undefined)).toBe("—");
    expect(num("")).toBe("—");
  });

  it("숫자가 아닌 문자열도 — 다", () => {
    expect(num("없음")).toBe("—");
  });

  it("⭐ 진짜 0 은 0 이다 — 모름과 갈려야 한다", () => {
    expect(num(0)).toBe("0.00");
  });
});

describe("pct — 부호를 먼저 읽게", () => {
  it("이득은 + 를 붙인다", () => {
    expect(pct(1.5)).toBe("+1.50%");
  });

  it("손실은 부호가 이미 있다", () => {
    expect(pct(-4.48)).toBe("-4.48%");
  });

  it("모르면 — 다", () => {
    expect(pct(null)).toBe("—");
  });
});

describe("frameSeconds", () => {
  it("초·분·시·일을 읽는다", () => {
    expect(frameSeconds("10s")).toBe(10);
    expect(frameSeconds("15m")).toBe(900);
    expect(frameSeconds("4h")).toBe(14400);
    expect(frameSeconds("1d")).toBe(86400);
  });

  it("⚠️ 모르는 축은 15m 로 본다 — 0 을 주면 나눗셈이 깨진다", () => {
    expect(frameSeconds("???")).toBe(900);
  });
});

describe("frameWord", () => {
  it("초를 봉 이름으로", () => {
    expect(frameWord(10)).toBe("10초봉");
    expect(frameWord(900)).toBe("15분봉");
    expect(frameWord(3600)).toBe("1시간봉");
    expect(frameWord(86400)).toBe("1일봉");
  });
});

describe("aliveness — 로직이 도는가", () => {
  it("러너를 못 읽으면 모름이다. 멈췄다고 하지 않는다", () => {
    const got = aliveness(undefined, undefined, undefined, 900);
    expect(got.label).toBe("모름");
    expect(got.tone).toBeUndefined();
  });

  it("스트림이 끊기면 멈췄다", () => {
    expect(aliveness(5, false, 10, 900).tone).toBe("loss");
  });

  it("⭐ 판정 0 은 고장이 아니다 — 첫 봉을 기다리는 중일 수 있다", () => {
    const got = aliveness(0, true, undefined, 900);
    expect(got.label).toBe("첫 판정 대기");
    expect(got.tone).toBeUndefined();
  });

  // 🔴 실제로 겪은 거짓말: 10초봉 차트를 보는 동안 화면이 *"첫 10초 봉이 마감되면
  //    돈다"* 고 적었다. 판정은 15m 에서만 도는데 90배 짧은 주기를 말한 것이고,
  //    12분밖에 안 된 정상 대기가 고장으로 읽혔다.
  it("🔴 대기 문구는 **판정 축**을 말한다 — 차트 축이 아니다", () => {
    expect(aliveness(0, true, undefined, 900).why).toContain("15분봉");
    expect(aliveness(0, true, undefined, 900).why).not.toContain("10초");
  });

  it("다음 판정까지 남은 시간을 붙인다 — 대기와 고장을 가르는 값이다", () => {
    expect(aliveness(0, true, undefined, 900, 120).why).toContain(
      "다음 2분 뒤",
    );
  });

  it("다음 판정 시각을 모르면 지어내지 않는다", () => {
    expect(aliveness(0, true, undefined, 900).why).not.toContain("다음");
  });

  it("🔴 한 주기 안이면 정상이다 — 15m 판정을 초로 재면 늘 빨개진다", () => {
    // 15m 축에서 마지막 판정이 800초 전 = 아직 한 주기 안이다.
    expect(aliveness(3, true, 800, 900).tone).toBe("gain");
  });

  it("한 주기의 1.5배를 넘기면 밀린 것이다", () => {
    expect(aliveness(3, true, 1400, 900).tone).toBe("loss");
  });

  it("10초봉은 15초만 넘겨도 밀린 것이다 — 축마다 기준이 다르다", () => {
    expect(aliveness(9, true, 20, 10).tone).toBe("loss");
    expect(aliveness(9, true, 12, 10).tone).toBe("gain");
  });
});

describe("ago", () => {
  it("초·분·시로 단위를 올린다", () => {
    expect(ago(30)).toBe("30초 전");
    expect(ago(600)).toBe("10분 전");
    expect(ago(7200)).toBe("2시간 전");
  });

  it("모르면 — 다", () => {
    expect(ago(undefined)).toBe("—");
  });
});

describe("시각 표시 — **저장은 UTC, 표시는 KST** (절대 규칙 #7)", () => {
  // 🔴 사용자 지적 2026-08-18: *"지금 시간대가 한국시 기준이 아닌 것 같네."*
  //    화면이 UTC 를 그대로 찍고 있었고, 사람은 그것을 자기 시계로 읽는다.
  it("UTC 를 +9 해서 보여 준다", () => {
    expect(when("2026-08-18T13:30:00+00:00")).toBe("08-18 22:30");
    expect(clock("2026-08-18T13:30:00+00:00")).toBe("22:30");
  });

  it("날짜를 넘어가도 맞는다 — 자정 근처가 하루씩 어긋나던 자리다", () => {
    expect(when("2026-08-18T15:30:00+00:00")).toBe("08-19 00:30");
  });

  it("초 타임스탬프도 같은 규칙이다", () => {
    // 1787060698 = 2026-08-18T13:44:58Z
    expect(whenSec("1787060698")).toBe("08-18 22:44");
  });

  it("모르면 — 다. 0 이나 오늘로 채우지 않는다", () => {
    expect(when(null)).toBe("—");
    expect(when("망가진 값")).toBe("—");
    expect(whenSec("0")).toBe("—");
    expect(clock(undefined)).toBe("—");
  });
});

describe("orderKind — 주문이 **무엇이었나**", () => {
  // 🔴 사용자 지적 2026-08-18: *"이렇게 청산으로 나오는데, 이거 손절이란 거지?"*
  //    `is_reduce_only` 하나만 보고 "청산" 이라고 적고 있었는데, 그 필드는
  //    "포지션을 줄이는 주문" 이라는 뜻일 뿐 강제청산과 아무 상관이 없다.
  it("⭐ `ao-` 는 우리가 건 조건부가 발동한 것 — **손절**이다", () => {
    expect(orderKind("ao-2089710147029958656", true)).toBe("손절 발동");
  });

  it("익절 두 다리를 가른다", () => {
    expect(orderKind("t-abc-take_profit-1", true)).toBe("1차 익절");
    expect(orderKind("t-abc-take_profit-2", true)).toBe("목표 익절");
  });

  it("진입과 사람이 닫은 것을 가른다", () => {
    expect(orderKind("t-abc-entry-0", false)).toBe("진입");
    expect(orderKind("t-cclose-1787047358", true)).toBe("손으로 닫음");
  });

  it("⛔ 강제청산을 말하지 않는다 — 구별할 근거가 없다", () => {
    const all = [
      orderKind("ao-1", true),
      orderKind("t-abc-take_profit-1", true),
      orderKind("t-cclose-1", true),
      orderKind("무엇인지 모름", true),
    ].join(" ");
    expect(all).not.toContain("강제");
  });

  it("모르는 이름이면 줄이는 주문인지만 말한다", () => {
    expect(orderKind("무엇인지 모름", true)).toBe("포지션 줄임");
    expect(orderKind("무엇인지 모름", false)).toBe("");
  });
});

describe("runOf — 주문 이름에서 판을 되뽑는다 (T18 ⑤)", () => {
  it("새 형식에서 판 표식을 읽는다", () => {
    expect(runOf("t-3642fc-863ce363-en-0")).toBe("3642fc");
    expect(runOf("t-3642fc-863ce363-tp-1")).toBe("3642fc");
  });

  it("옛 형식은 판 미상이다 — 지어내지 않는다", () => {
    // ⚠️ 규격을 바꾸기 전에 나간 주문이다. 매매 id 12자가 그 자리에 있으므로
    //    길이로 갈린다 — 6자가 아니면 판 표식이 아니다.
    expect(runOf("t-863ce36336a2-take_profit-1")).toBe(UNKNOWN_RUN);
  });

  it("거래소가 만든 주문은 어느 방법으로도 못 엮는다", () => {
    // 🔴 조건부가 발동해 Gate 가 만든 주문이라 이름이 우리 것이 아니다.
    expect(runOf("ao-1234567890")).toBe(UNKNOWN_RUN);
    expect(runOf("")).toBe(UNKNOWN_RUN);
  });

  it("아무 판에나 붙이지 않는다", () => {
    // ⛔ 붙이면 남의 판 성적에 남의 주문이 섞이고 나중에 못 가른다.
    expect(runOf("t-abc")).toBe(UNKNOWN_RUN);
    expect(runOf("t--863ce363-en-0")).toBe(UNKNOWN_RUN);
  });
});

describe("splitFindings — 못 믿을 것과 알아 둘 것", () => {
  const at = (code: string, level: string) => ({ code, level, detail: code });

  it("error 만 붉다", () => {
    // 🔴 예전에는 전부 붉게 묶어 "판정을 믿으면 안 된다" 를 붙였다. 그 문장은
    //    error 에만 참이다.
    const got = splitFindings([
      at("pnl_sign_split", "error"),
      at("wallet_unattributable", "warn"),
    ]);
    expect(got.bad.map((item) => item.code)).toEqual(["pnl_sign_split"]);
    expect(got.soft.map((item) => item.code)).toEqual([
      "wallet_unattributable",
    ]);
  });

  it("판이 여럿이라 못 가르는 것은 결함이 아니다", () => {
    // ⚠️ 판 5개를 띄우면 늘 뜬다 — 사실 보고이지 이상이 아니다. 이것 때문에
    //    정상 상태가 경보처럼 보였다 (2026-08-20).
    const got = splitFindings([at("wallet_unattributable", "warn")]);
    expect(got.bad).toHaveLength(0);
    expect(got.soft).toHaveLength(1);
  });

  it("등급을 모르면 경고 쪽이다 — 치명으로 올리지 않는다", () => {
    // ⛔ 모르는 것을 붉게 만들면 늘 붉어지고, 늘 붉으면 아무도 안 본다.
    const got = splitFindings([at("newish", ""), at("other", "info")]);
    expect(got.bad).toHaveLength(0);
    expect(got.soft).toHaveLength(2);
  });

  it("버리지 않는다 — 합치면 원래 개수다", () => {
    // 🔴 조용히 버리면 "왜 이 판만 대조가 없지" 에 화면이 답을 못 한다 (규칙 #8).
    const items = [at("a", "error"), at("b", "warn"), at("c", "error")];
    const got = splitFindings(items);
    expect(got.bad.length + got.soft.length).toBe(items.length);
  });
});

describe("confirmedNaked — 무장 중을 무방비로 외치지 않는다", () => {
  const row = (symbol: string) => ({ symbol });
  const GRACE = 8000;

  it("처음 본 순간에는 아직 모른다", () => {
    // 🔴 여기가 사용자 신고의 핵심 — 체결 직후 폴링이 경보음을 냈다.
    const step = confirmedNaked([row("ETH_USDT")], new Map(), 1000, GRACE);
    expect(step.sure).toEqual([]);
    expect(step.seen.get("ETH_USDT")).toBe(1000);
  });

  it("유예가 지나면 진짜로 친다 — 감추는 것이 아니다", () => {
    const seen = new Map([["ETH_USDT", 1000]]);
    expect(confirmedNaked([row("ETH_USDT")], seen, 1000 + GRACE, GRACE).sure).toHaveLength(1);
  });

  it("처음 본 시각을 유지한다 — 갱신하면 영원히 안 익는다", () => {
    let seen = new Map<string, number>();
    for (const now of [0, 4000]) {
      seen = confirmedNaked([row("ETH_USDT")], seen, now, GRACE).seen;
    }
    expect(seen.get("ETH_USDT")).toBe(0);
    expect(confirmedNaked([row("ETH_USDT")], seen, 8000, GRACE).sure).toHaveLength(1);
  });

  it("손절이 걸리면 표에서 빠진다", () => {
    // ⚠️ 안 빼면 다음에 무방비가 됐을 때 옛 시각으로 **즉시** 익어 거짓 경보가 다시 난다.
    const seen = new Map([["ETH_USDT", 0]]);
    const step = confirmedNaked([], seen, 9000, GRACE);
    expect(step.seen.size).toBe(0);
    // 다시 무방비가 되면 그때부터 새로 잰다.
    expect(confirmedNaked([row("ETH_USDT")], step.seen, 9000, GRACE).sure).toEqual([]);
  });

  it("종목마다 따로 잰다", () => {
    const seen = new Map([["ETH_USDT", 0]]);
    const step = confirmedNaked([row("ETH_USDT"), row("BTC_USDT")], seen, 8000, GRACE);
    expect(step.sure.map((item) => item.symbol)).toEqual(["ETH_USDT"]);
  });

  it("무방비가 없으면 조용하다", () => {
    expect(confirmedNaked([], new Map(), 9999, GRACE).sure).toEqual([]);
  });
});

describe("stillWorrying — 확인 배너가 다시 떠야 하나", () => {
  it("건너뛴 것이 없으면 안 뜬다", () => {
    expect(stillWorrying(0, null)).toBe(false);
    expect(stillWorrying(0, 0)).toBe(false);
  });

  it("확인한 적이 없으면 뜬다", () => {
    expect(stillWorrying(1, null)).toBe(true);
  });

  it("확인한 만큼이면 닫혀 있다", () => {
    expect(stillWorrying(3, 3)).toBe(false);
  });

  it("🔴 확인 뒤 **새로** 나면 다시 뜬다 — 한 번 누르는 것이 영구 침묵이 되면 안 된다", () => {
    expect(stillWorrying(4, 3)).toBe(true);
  });

  it("재시작으로 수가 줄어도 조용하다 (과거를 다시 꺼내지 않는다)", () => {
    expect(stillWorrying(1, 5)).toBe(false);
  });

  it("저장소가 깨진 값을 주면 뜨는 쪽으로 — 조용한 실패보다 낫다", () => {
    expect(stillWorrying(2, Number.NaN)).toBe(true);
  });
});
