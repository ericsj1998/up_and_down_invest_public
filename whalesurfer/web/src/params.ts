/**
 * 주소 쿼리(`?cik=…&cusip=…`) 상태 — 라우터 없이. 종목 · 보고자는 주소로 공유된다.
 */

import { useCallback, useEffect, useState } from "react";

export function useQueryParams(): [URLSearchParams, (next: Record<string, string | null>) => void] {
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));

  useEffect(() => {
    const onPop = () => setParams(new URLSearchParams(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const update = useCallback((next: Record<string, string | null>) => {
    const q = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(next)) {
      if (v) q.set(k, v);
      else q.delete(k);
    }
    const qs = q.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    setParams(q);
  }, []);

  return [params, update];
}
