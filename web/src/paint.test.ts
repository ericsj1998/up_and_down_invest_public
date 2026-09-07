/**
 * `Cannot update oldest data` — 축을 바꿀 때 터지던 그 순서를 그대로 재현한다.
 *
 * 🔴 사용자 신고 2026-08-30: RUN 상세가 가끔 흰 화면. 콘솔에 남은 것:
 *
 *     Cannot update oldest data, last time=[object Object], new time=[object Object]
 *
 * 옛 가드는 **props** 와 비교했는데, `update()` 가 보는 것은 **시리즈에 실제로 들어간
 * 값**이다. 그 둘이 갈리는 순간이 축 전환이다.
 */

import { describe, expect, it } from "vitest";
import SOURCE from "./Chart.tsx?raw";
import { mayPaint, mayReplant } from "./paint";

describe("mayPaint — update 는 뒤로 못 간다", () => {
  it("처음이면 무엇이든 그린다", () => {
    expect(mayPaint(null, 1_700_000_000)).toBe(true);
  });

  it("더 새로운 조각은 그린다", () => {
    expect(mayPaint(1_700_000_000, 1_700_000_060)).toBe(true);
  });

  it("같은 시각은 그린다 — 만들어지는 중인 봉을 갱신하는 정상 경로다", () => {
    // ⚠️ 여기서 막으면 꼬리가 안 흔들린다.
    expect(mayPaint(1_700_000_000, 1_700_000_000)).toBe(true);
  });

  it("🔴 과거 조각은 버린다 — 그리면 던진다", () => {
    expect(mayPaint(1_700_000_060, 1_700_000_000)).toBe(false);
  });

  it("🔴 축 전환 순서를 그대로 밟는다", () => {
    // 4h 로 바꿔 시리즈가 4h 봉(16:00)까지 채워졌다.
    const fourHour = Date.parse("2026-08-29T16:00:00Z") / 1000;
    // 그런데 `forming` 은 아직 1m 조각(18:03)이다 — 옛 가드는 이것을 통과시켰다.
    const stale1m = Date.parse("2026-08-29T18:03:00Z") / 1000;
    expect(mayPaint(fourHour, stale1m)).toBe(true); // 여기까지는 막을 수 없다

    // 이제 시리즈의 마지막은 18:03 이다. 곧 **진짜** 4h 조각(16:00)이 온다.
    // 옛 가드는 props(16:00)와 비교해 또 통과시켰고, 거기서 터졌다.
    expect(mayPaint(stale1m, fourHour)).toBe(false);
  });

  it("판을 새로 지으면 기억이 비고, 첫 조각이 다시 그려진다", () => {
    // ⚠️ 안 비우면 옛 축의 마지막 시각 때문에 새 축의 첫 조각이 "과거" 로 보여
    //    영영 안 그려진다 — 조용한 빈 차트가 된다.
    expect(mayPaint(null, Date.parse("2026-08-29T16:00:00Z") / 1000)).toBe(true);
  });
});

describe("mayReplant — 다시 그린 뒤 진행 중 봉을 도로 얹기", () => {
  /**
   * 🔴 사용자 신고 2026-08-30: *"꼬리 자체가 천천히 올라갔다 사라지거나 그런게 아니라,
   * 그냥 띡 사라지고 봉 생기고 다시 꼬리 생기고 이런식으로 엄청 불편해."*
   *
   * 원인이 **둘**이었고 둘 다 이 근처였다:
   *
   *   ① `setData` 가 매초 돌았다 — `forming` 이 그 효과의 의존성에 있었다.
   *      진행 중 봉은 1초마다 새 객체로 오므로 봉 400개를 **매초 갈아 끼웠다.**
   *      코드에는 *"1초에 한 번이면 눈에 안 띈다"* 고 적혀 있었는데 **추측이었다.**
   *
   *   ② 다시 그린 뒤 **마감된 봉 위에** 손에 든 진행 중 조각을 덮어썼다.
   *      그 조각은 봉이 끝나기 전에 찍힌 것이라 고가가 더 낮다 → **꼬리가 줄고**,
   *      다음 틱에 다시 자란다.
   */
  it("마감 봉보다 미래면 얹는다 — 아직 안 끝난 봉이다", () => {
    expect(mayReplant(90, 100)).toBe(true);
  });

  it("🔴 **같은 시각이면 안 얹는다** — 마감된 값이 최종본이다", () => {
    // 여기가 `mayPaint` 와 갈리는 자리다. 한 글자가 깜빡임이었다.
    expect(mayReplant(100, 100)).toBe(false);
  });

  it("과거면 당연히 안 얹는다", () => {
    expect(mayReplant(100, 90)).toBe(false);
  });

  it("마감 봉이 아예 없으면 얹는다", () => {
    expect(mayReplant(null, 100)).toBe(true);
  });

  it("⚠️ `mayPaint` 와 **규칙이 달라야 한다** — 같으면 둘 중 하나가 틀린 것이다", () => {
    // `mayPaint` 는 같은 시각을 허용한다 (꼬리가 자라는 정상 경로).
    // `mayReplant` 는 허용하지 않는다 (마감된 값이 사실이다).
    expect(mayPaint(100, 100)).toBe(true);
    expect(mayReplant(100, 100)).toBe(false);
  });

  it("🔴 실제로 겪은 순서를 재현한다 — 꼬리가 **줄지 않는다**", () => {
    // 10초봉. 시각 100 의 봉이 만들어지는 중이다.
    const painted = { at: 90 as number | null, high: 78047.6 };

    // ① 틱: 진행 중 봉 100 이 온다 → 얹는다 (자란다)
    expect(mayPaint(painted.at, 100)).toBe(true);
    painted.at = 100;
    painted.high = 78047.6;

    // ② 틱: 같은 봉이 더 자란다 → 같은 시각도 허용해야 한다
    expect(mayPaint(painted.at, 100)).toBe(true);
    painted.high = 78047.7;

    // ③ 구조물 폴링이 돌아온다. 이제 **100 이 마감 봉**이고 고가는 78047.7 이다.
    //    손에 든 조각은 ② 이전에 찍힌 78047.6 일 수 있다.
    const lastClosed = 100;
    expect(mayReplant(lastClosed, 100)).toBe(false);
    // ⇒ 안 얹으므로 마감된 78047.7 이 남는다. 예전에는 여기서 78047.6 으로 덮여
    //   **꼬리가 줄었다가** 다음 틱에 다시 자랐다.
  });
});

describe("Chart — setData 를 매초 부르지 않는다", () => {
  /** 마감 봉을 붓는 효과 본문 — 파일 전체에서 찾으면 남의 코드가 통과시킨다. */
  function redraw(): string {
    const head = SOURCE.indexOf("series.current.setData(sorted.map");
    expect(head).toBeGreaterThan(-1);
    return SOURCE.slice(head, SOURCE.indexOf("]);", head) + 3);
  }

  it("⛔ 그 효과의 의존성에 `forming` 이 없다", () => {
    // 있으면 봉 400개짜리 `setData` 가 **매초** 돈다.
    const deps = redraw().slice(redraw().lastIndexOf("}, ["));
    expect(deps).toContain("frame.candles");
    expect(deps).not.toContain("forming");
  });

  it("⚠️ 그래도 **최신 조각**을 얹는다 — 상자로 읽는다", () => {
    // 의존성에서 그냥 빼면 낡은 조각을 얹는다 (닫힌 클로저).
    expect(SOURCE).toContain("const tick = useRef(forming)");
    expect(redraw()).toContain("tick.current");
  });

  it("🔴 다시 그린 뒤에는 `mayReplant` 를 쓴다 — `mayPaint` 가 아니다", () => {
    expect(redraw()).toContain("mayReplant(");
  });

  it("⛔ 진행 중 봉 효과의 의존성에 `frame.candles` 가 없다", () => {
    // 🔴 깜빡임의 **세 번째** 갈래였다. 있으면 마감 봉이 새로 올 때 이 효과가 또 돌면서
    //    방금 확정된 마감 봉 위에 손에 든 낡은 조각을 덮어쓴다 → 꼬리가 줄었다 자란다.
    const head = SOURCE.indexOf("만드는 중 봉 — 꼬리가 흔들린다");
    expect(head).toBeGreaterThan(-1);
    const body = SOURCE.slice(head, SOURCE.indexOf("]);", head) + 3);
    const deps = body.slice(body.lastIndexOf("}, ["));
    expect(deps).toContain("forming");
    expect(deps).not.toContain("frame.candles");
  });

  it("🔴 두 경로가 **다른 규칙**을 쓴다 — 같아지면 하나가 틀린 것이다", () => {
    // 다시 그린 뒤: `mayReplant` (같은 시각 금지 — 마감된 값이 사실)
    // 새 틱:       `mayPaint`   (같은 시각 허용 — 꼬리가 자란다)
    const head = SOURCE.indexOf("만드는 중 봉 — 꼬리가 흔들린다");
    const body = SOURCE.slice(head, SOURCE.indexOf("]);", head) + 3);
    expect(body).toContain("mayPaint(");
    expect(body).not.toContain("mayReplant(");
  });

  it("⚠️ 원문을 실제로 읽었나", () => {
    expect(SOURCE.length).toBeGreaterThan(1000);
  });
});
