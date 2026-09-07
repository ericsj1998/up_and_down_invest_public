/**
 * 서버가 살아 있나 — 사이드바의 "서버가 돌린다" 가 **글자가 아니라 상태**가 되게 (T220 UX 점검 2026-09-05).
 *
 * `/health` 는 공개 경로라 로그인 전에도 답한다. 30초마다 묻고, 마지막으로 답한 시각을 기억한다.
 * 🔴 실패를 조용히 삼키지 않는다 — 두 번 연속 못 받으면 빨강으로 바꾸고 "N초 전 응답" 을 적는다 (규칙 #8).
 */
import { useEffect, useState } from "react";

const EVERY_MS = 30_000;

export interface Health {
  /** 마지막 성공 응답 시각(ms) — 없으면 null. */
  lastOk: number | null;
  /** 최근 시도가 실패했나. */
  failing: boolean;
  /** 지금 기준 마지막 응답이 몇 초 전인가. */
  ageS: number | null;
}

export function useHealth(): Health {
  const [lastOk, setLastOk] = useState<number | null>(null);
  const [failing, setFailing] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    const ping = async () => {
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (!alive) return;
        if (res.ok) {
          setLastOk(Date.now());
          setFailing(false);
        } else {
          setFailing(true);
        }
      } catch {
        if (alive) setFailing(true);
      }
      if (alive) setNow(Date.now());
    };
    void ping();
    const timer = setInterval(ping, EVERY_MS);
    // 초 단위 나이는 10초마다만 다시 그린다 — 매초 그리면 사이드바가 쉴 새 없이 바뀐다.
    const tick = setInterval(() => alive && setNow(Date.now()), 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
      clearInterval(tick);
    };
  }, []);

  return { lastOk, failing, ageS: lastOk === null ? null : Math.round((now - lastOk) / 1000) };
}
