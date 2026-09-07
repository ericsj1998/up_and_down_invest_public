/**
 * 탭은 주소다. **판도 주소다.**
 *
 * 🔴 상태로만 들고 있으면 **새로고침이 늘 첫 탭으로 돌아간다.** 옛 화면이 그랬고,
 * 첫 탭이 무거운 화면이라 안 쓸 화면이 API 를 여러 번 불렀다.
 *
 * 🔴 **판 id 가 주소에 있어야 여러 창을 띄운다** (사용자 요구 2026-08-19: *"여러 창에
 * 띄워두고 보는 게 나을 것 같네"*). 화면 상태로 고르면 창마다 같은 판을 보게 되고,
 * 판을 나란히 놓고 비교할 방법이 없다.
 */

import { useEffect, useState } from "react";

/** 주소를 칸으로 자른다. `/paper/live123?x=1` → `["paper", "live123"]`. */
export function segments(path: string): string[] {
  return path
    .replace(/[?#].*$/, "")
    .replace(/^\/+/, "")
    .split("/")
    .filter(Boolean);
}

/** 주소의 첫 칸. `/paper?x=1` → `paper`. */
export function firstSegment(path: string): string {
  return segments(path)[0] ?? "";
}

/** 아는 값이면 그것, 아니면 기본값. */
export function pickRoute<T extends string>(
  path: string,
  known: readonly string[],
  fallback: T,
): T {
  const head = firstSegment(path);
  return (known.includes(head) ? head : fallback) as T;
}

/**
 * 두 번째 칸 — **판 id**. 없으면 빈 문자열.
 *
 * ⚠️ 값을 검사하지 않는다. 없는 판이면 화면이 *"그런 판이 없다"* 고 말하는 것이 맞고,
 * 여기서 조용히 다른 판으로 바꾸면 주소와 화면이 다른 것을 가리킨다.
 */
export function routeParam(path: string): string {
  return segments(path)[1] ?? "";
}

export type Place<T extends string> = {
  route: T;
  /** 두 번째 칸. `/paper/live123` 이면 `live123`. */
  param: string;
  /** 주소를 바꾼다. `param` 을 주면 뒤에 붙인다. */
  go: (next: T, param?: string) => void;
};

export function useRoute<T extends string>(
  known: readonly string[],
  fallback: T,
): Place<T> {
  const read = () => ({
    route: pickRoute(window.location.pathname, known, fallback),
    param: routeParam(window.location.pathname),
  });
  const [place, setPlace] = useState(read);

  useEffect(() => {
    const onPop = () => setPlace(read());
    window.addEventListener("popstate", onPop);
    // ⚠️ 첫 진입에 주소를 정규화한다 — `/` 로 들어오면 기본 탭 주소가 되게.
    //    ⛔ 판 id 가 붙어 있으면 건드리지 않는다. 지우면 새로고침마다 판이 사라진다.
    if (firstSegment(window.location.pathname) !== place.route && !place.param) {
      window.history.replaceState(null, "", `/${place.route}`);
    }
    return () => window.removeEventListener("popstate", onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [known, fallback, place.route, place.param]);

  const go = (next: T, param?: string) => {
    const path = param ? `/${next}/${param}` : `/${next}`;
    if (path === window.location.pathname) return;
    window.history.pushState(null, "", path);
    setPlace({ route: next, param: param ?? "" });
  };

  return { ...place, go };
}
