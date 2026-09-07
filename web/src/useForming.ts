/**
 * 진행 중인 봉을 **빠르게** 따라간다 — 꼬리가 실시간으로 흔들리게.
 *
 * 🔴 사용자 요구 2026-08-18: *"주가창에서 꼬리 위아래로 왔다갔다 하는 거, 10초 축으로
 * 실제 호가를 보고 싶다."*
 *
 * ⚠️ **`/state` 폴링과 섞지 않는다.** 그쪽은 봉 800개 + 도형이라 1.5초가 한계고, 이쪽은
 * 봉 하나라 1초면 된다. 한 주기로 묶으면 둘 중 하나가 반드시 잘못된 주기로 돈다.
 *
 * ⚠️ 겹침 방어 깃발은 **반드시 `finally` 에서** 내린다 — 한 번의 실패로 폴링이 영원히
 * 멈춘 적이 있다.
 */

import { useEffect, useRef, useState } from "react";
import { analysisTick, tick as fetchTick, type Tick } from "./api";

/** 가장 빠른 주기(ms). 서버 캐시가 0.7초라 이보다 짧게 해도 같은 값만 온다. */
const FAST_MS = 1000;

/**
 * 폴링 주기 — **축과 무관하게 1초다.**
 *
 * 🔴 처음에는 `refresh()` 를 따라 *간격의 1/10* 로 짰다가 테스트에서 1분봉이 6초로
 * 나오는 것을 보고 **전제가 틀린 것**을 알았다:
 *
 *     refresh()   "새 봉이 언제 생기나" — 그래서 간격에 비례하는 것이 맞다
 *     forming()   "지금 가격이 얼마인가" — **간격과 아무 상관이 없다**
 *
 * 1분봉을 보든 1일봉을 보든 그 봉의 종가는 **매 체결마다** 바뀐다. 6초에 한 번 물으면
 * 사용자가 보려던 그 움직임이 6초 단위로 뭉개진다.
 *
 * ⚠️ 그래도 하한은 둔다 — 0 이 나오면 브라우저가 폭주한다. 거래소 부하는 서버
 * TTL(0.7초)이 막으므로, 화면이 빨라도 REST 는 초당 한 번을 넘지 않는다.
 */
export function formingMs(_frame: string): number {
  // ⚠️ 축을 받되 쓰지 않는다 — 다시 반영하고 싶어질 때 여기가 그 자리이고, 위 주석이
  //    "왜 안 하는지" 를 지킨다.
  return FAST_MS;
}

/**
 * @param key 세션 id. 없으면 안 돈다.
 * @param frame 보고 있는 시간축.
 */
export function useForming(key: string | undefined, frame: string): Tick["bar"] | null {
  return useTick(
    // ⚠️ `key` 가 없으면 **부를 것이 없다** — 판이 없는 화면이다.
    key ? () => fetchTick(key, frame) : null,
    `${key ?? ""}:${frame}`,
    frame,
  );
}

/**
 * **판 없이** 지금 만들어지는 봉을 따라간다 — 차트 주문이 쓴다.
 *
 * @param symbol 종목.
 * @param market 거래소.
 * @param frame 보고 있는 시간축.
 *
 * 🔴 이것이 없어서 차트 주문의 꼬리가 안 자랐다 (사용자 신고 2026-08-30:
 * *"꼬리가 안보이는게 너무 답답하네 … 그냥 10초마다 받아오는 느낌이야."*).
 * 마감된 봉만 받으면 그 봉은 이미 다 그려진 봉이라 **자랄 일이 없다.**
 */
export function useAnalysisForming(
  symbol: string,
  market: string,
  frame: string,
): Tick["bar"] | null {
  return useTick(
    symbol ? () => analysisTick({ symbol, market, timeframe: frame }) : null,
    `${market}:${symbol}:${frame}`,
    frame,
  );
}

/**
 * 틱 폴링의 **알맹이** — 무엇을 부를지만 다르고 나머지는 같다.
 *
 * @param fetcher 한 번 받아 오는 함수. `null` 이면 안 돈다.
 * @param id 무엇을 보고 있나 — 바뀌면 **즉시 비우고** 다시 시작한다.
 * @param frame 주기를 정하는 축.
 *
 * 🔴 **두 화면이 같은 알맹이를 쓴다.** 겹침 방어와 실패 처리를 두 벌로 쓰면 한쪽만
 * 고쳐진다 — 이 코드에는 *"한 번의 실패로 폴링이 영원히 멈춘"* 이력이 있고, 그 고침이
 * `finally` 한 줄이었다. 그런 것이 두 벌이면 반드시 한쪽에 남는다.
 */
function useTick(
  fetcher: (() => Promise<Tick>) | null,
  id: string,
  frame: string,
): Tick["bar"] | null {
  const [bar, setBar] = useState<Tick["bar"] | null>(null);
  const busy = useRef(false);
  // ⚠️ 함수는 매 렌더 새로 만들어진다 — 의존성에 넣으면 타이머가 매 렌더 다시 선다.
  //    무엇을 보고 있는지는 `id` 가 말하므로, 함수는 상자에 담아 둔다.
  const box = useRef(fetcher);
  box.current = fetcher;

  useEffect(() => {
    if (box.current === null) {
      setBar(null);
      return;
    }
    // 🔴 보는 것이 바뀌면 **즉시 비운다.** 안 비우면 새 값이 올 때까지 옛 축의 봉이
    //    새 축의 오른쪽 끝에 그려진다 — 전혀 다른 가격이 "지금" 으로 보인다.
    setBar(null);
    const pull = () => {
      const ask = box.current;
      if (ask === null || busy.current) return;
      busy.current = true;
      ask()
        .then((body) => setBar(body.bar))
        // ⚠️ 실패는 조용히 넘긴다 — 연결 배너가 이미 말한다. 다만 **옛 값을 지운다**:
        //    못 받은 것을 지금 값으로 계속 그리면 화면이 거짓말한다.
        .catch(() => setBar(null))
        // ⛔ **반드시 `finally` 에서** 내린다 — 한 번의 실패로 폴링이 영원히 멈춘 적이 있다.
        .finally(() => {
          busy.current = false;
        });
    };
    pull();
    const timer = setInterval(pull, formingMs(frame));
    return () => clearInterval(timer);
  }, [id, frame]);

  return bar;
}
