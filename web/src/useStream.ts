/**
 * 거래소가 **미는** 봉을 듣는다 (SSE) — 차트 주문의 실시간.
 *
 * 🔴 사용자 2026-08-30: *"실제 거래소처럼 동적이게 움직이게 하고 싶은건데... 이러면
 * 사실상 못써먹겠는데?"*
 *
 * ## 폴링으로는 안 되는 이유
 *
 * 1초마다 봉 하나를 REST 로 받는 것까지 했다. 깜빡임은 다 잡았지만 **부드러워지지는
 * 않는다** — 1초에 한 번 계단식으로 값이 바뀌는 것이 한계다. 거래소 차트가 부드러운
 * 이유는 **체결마다** 값이 오기 때문이고, 그것은 다듬어서 될 일이 아니라 **통로가
 * 달라야** 하는 일이었다.
 *
 * ## ⚠️ 폴링을 **버리지 않는다**
 *
 * 스트림이 유일한 통로면 한 번 끊길 때 화면이 멈춘다. `EventSource` 는 스스로 다시
 * 붙지만 그 사이가 있고, 프록시·방화벽이 SSE 를 막는 곳도 있다.
 *
 * ⇒ 둘을 같이 둔다. 스트림이 살아 있으면 그 값이 이기고, 끊기면 폴링이 받는다.
 *   **어느 쪽이 그리고 있는지 화면이 말한다** — 조용히 느려지면 사람은 고장으로 읽는다.
 */

import { useEffect, useRef, useState } from "react";
import type { Tick } from "./api";

/** 스트림이 준 봉 + 살아 있는지. */
export interface Streamed {
  bar: Tick["bar"];
  /** 스트림이 붙어 있나 — 화면이 이것으로 "실시간" 배지를 켠다. */
  live: boolean;
}

export const STALE_MS = 20_000;
/**
 * 이 시간 동안 아무것도 안 오면 **살아 있다고 하지 않는다**.
 *
 * ⚠️ 서버가 15초마다 하트비트를 보내므로, 그보다 넉넉히 잡는다. 짧으면 조용한 장에서
 * 배지가 깜빡이고, 길면 죽은 연결을 살아 있다고 말한다.
 */

/**
 * @param symbol 종목. 비면 안 붙는다.
 * @param market 거래소.
 * @param frame 시간축.
 *
 * ⛔ **`bar` 를 못 받았을 때 옛 값을 남기지 않는다** — 못 받은 것을 지금 값으로 계속
 * 그리면 화면이 거짓말한다.
 */
export function useStream(symbol: string, market: string, frame: string): Streamed {
  const [bar, setBar] = useState<Tick["bar"]>(null);
  const [live, setLive] = useState(false);
  // ⚠️ 마지막으로 **무언가 받은** 시각. 하트비트도 여기를 갱신한다 — 조용한 장과
  //    죽은 연결을 가르는 것이 이 값이다.
  const seen = useRef(0);

  useEffect(() => {
    if (!symbol) {
      setBar(null);
      setLive(false);
      return;
    }
    // 🔴 보는 것이 바뀌면 **즉시 비운다.** 안 비우면 새 값이 올 때까지 옛 축의 봉이
    //    새 축의 오른쪽 끝에 그려진다 — 전혀 다른 가격이 "지금" 으로 보인다.
    setBar(null);
    setLive(false);
    seen.current = 0;

    const query = new URLSearchParams({ symbol, market, timeframe: frame });
    const source = new EventSource(`/api/analysis/stream?${query.toString()}`);

    source.onmessage = (event) => {
      seen.current = Date.now();
      setLive(true);
      try {
        const body: unknown = JSON.parse(event.data as string);
        // ⚠️ 서버가 무엇을 주든 **모양을 확인하고** 쓴다 — 아니면 차트가 던진다.
        if (body && typeof body === "object" && "ts" in body) {
          setBar(body as NonNullable<Tick["bar"]>);
        }
      } catch {
        // 한 줄이 깨진 것은 다음 줄로 넘어간다 — 연결을 버릴 이유가 아니다.
      }
    };
    // ⚠️ `EventSource` 는 오류 뒤 **스스로 다시 붙는다.** 여기서 닫으면 그 성질을
    //    버리는 것이다 — 배지만 끄고 둔다.
    source.onerror = () => setLive(false);

    // 하트비트(`: ping`)는 `onmessage` 를 안 부른다 — 그래서 시계로 따로 본다.
    const watch = window.setInterval(() => {
      if (seen.current > 0 && Date.now() - seen.current > STALE_MS) setLive(false);
    }, 5000);

    return () => {
      window.clearInterval(watch);
      source.close();
    };
  }, [symbol, market, frame]);

  return { bar, live };
}

/**
 * 스트림과 폴링 중 **무엇을 그릴지** 고른다.
 *
 * @param streamed 스트림이 준 것.
 * @param polled 폴링이 준 것.
 * @returns 그릴 봉.
 *
 * 🔴 **스트림이 살아 있으면 스트림이 이긴다.** 둘을 섞으면 시각이 앞뒤로 흔들리고,
 * 그러면 차트가 `Cannot update oldest data` 로 던진다.
 *
 * ⚠️ 스트림이 붙었는데 아직 봉을 못 받았으면 **폴링 값으로 버틴다** — 그 사이가
 * 비면 새로 연 창이 잠깐 빈 화면이다.
 */
export function pick(streamed: Streamed, polled: Tick["bar"]): Tick["bar"] {
  if (streamed.live && streamed.bar !== null) return streamed.bar;
  return polled;
}
