/**
 * 라이브 판 하나를 따라간다 — 목록·상태·건강을 한 곳에서.
 *
 * 🔴 **폴링이 겹치지 않게 한다.** 서버는 한 줄(단일 이벤트 루프)이고 러너가 그 CPU 를
 * 쓴다. 응답이 간격보다 늦어지는 순간 요청이 쌓이고, 브라우저 연결 한도에 걸리면
 * 화면 전체가 무한 로딩이 된다 — 옛 화면이 실제로 그랬다.
 *
 * ⚠️ 겹침 방어 깃발은 **반드시 `finally` 에서 내린다.** 안 그러면 한 번의 실패로
 * 폴링이 영원히 멈춘다. 요청 시한(`api.ts`)이 그 짝이다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  connection,
  health as fetchHealth,
  sessions as fetchSessions,
  state as fetchState,
  type Health,
  type Reconcile,
  type State,
  type Summary,
  type Watch,
} from "./api";

/** 목록 갱신 간격 — 느긋하게. 판 수만큼 요약을 만드는 일이다.
 *
 * 2026-09-05: 2.5초 → 10초. 1 GB 서버에서 RUN 상세 화면 하나가 3분에 요청 330건(1.5~2.5초 간격)을
 * 만들었다. 4시간 봉 전략의 판을 사람이 보는 데 1.5초 갱신은 필요 없다 — 봉 형성은 useForming 이 따로 본다. */
const LIST_MS = 10_000;
/** 상태·건강 갱신 간격 — 사람이 보는 화면이라 목록보다 조금 더 자주. */
const DETAIL_MS = 5_000;

export type LiveView = {
  rows: Summary[];
  /** 감시자가 본 이상 (T20 ③). 비어 있는 것이 정상이다. */
  watch: Watch[];
  /** 방향별 동시 보유 판 수 (T24 ③). 같은 방향 2판부터 경고감이다. */
  exposure: Record<string, number>;
  /** 거래소 대조 상태 — **정상일 때도** 온다 (안 오면 서버가 옛 판이다). */
  reconcile: Reconcile | null;
  current: Summary | null;
  state: State | null;
  health: Health | null;
  link: { ok: boolean; silentFor: number };
  error: string;
  /** 목록을 즉시 다시 당긴다 (동작 직후에 쓴다). */
  refresh: () => void;
  setError: (message: string) => void;
};

/**
 * @param live 라이브 판만 볼 것인가. 거짓이면 백테스트만.
 */
export function useLive(live: boolean, frame?: string, pick?: string | null): LiveView {
  const [rows, setRows] = useState<Summary[]>([]);
  const [watch, setWatch] = useState<Watch[]>([]);
  const [exposure, setExposure] = useState<Record<string, number>>({});
  const [reconcile, setReconcile] = useState<Reconcile | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [link, setLink] = useState({ ok: true, silentFor: 0 });
  const [error, setError] = useState("");
  const listing = useRef(false);
  const detailing = useRef(false);

  const pullList = useCallback(() => {
    if (listing.current) return;
    listing.current = true;
    fetchSessions()
      .then((body) => {
        setRows(body.sessions.filter((row) => !!row.live === live));
        // 🔴 감시자가 본 것 (T20 ③). 목록과 따로 온다 — 죽은 판은 목록에 없는 것이
        //    증상이라, 행에 매달면 바로 그 경우에 사라진다.
        setWatch(body.watch ?? []);
        setExposure(body.exposure ?? {});
        setReconcile(body.reconcile ?? null);
      })
      // ⚠️ 목록 실패로 화면 전체에 빨간 글씨를 띄우지 않는다 — 배너가 말한다.
      .catch(() => undefined)
      .finally(() => {
        listing.current = false;
        setLink(connection());
      });
  }, [live]);

  useEffect(() => {
    pullList();
    const timer = setInterval(pullList, LIST_MS);
    return () => clearInterval(timer);
  }, [pullList]);

  // ⭐ **고른 판이 있으면 그것을 본다.** 없으면 도는 판을 자동으로 따라간다 — 사람이
  //    고르지 않아도 화면이 채워져야 한다.
  //
  // ⚠️ 고른 판이 목록에서 사라지면(지웠다) 자동 선택으로 돌아간다. 없는 판을 붙들고
  //    있으면 화면이 빈 채로 멈춘다.
  const chosen = pick ? (rows.find((row) => row.session_id === pick) ?? null) : null;
  const target = chosen ?? rows.find((row) => row.running) ?? rows[0] ?? null;
  const key = target?.session_id;

  useEffect(() => {
    if (!key) {
      setState(null);
      setHealth(null);
      return;
    }
    const pull = () => {
      if (detailing.current) return;
      detailing.current = true;
      Promise.allSettled([fetchState(key, frame), fetchHealth(key)])
        .then(([one, two]) => {
          if (one.status === "fulfilled") setState(one.value);
          // ⚠️ 건강 조회 404 는 정상이다 — 저널에서 되살린 판은 러너가 없다.
          setHealth(two.status === "fulfilled" ? two.value : null);
        })
        .finally(() => {
          detailing.current = false;
          setLink(connection());
        });
    };
    pull();
    const timer = setInterval(pull, DETAIL_MS);
    return () => clearInterval(timer);
    // ⚠️ 시간축이 바뀌면 **즉시** 다시 당긴다 — 안 그러면 탭을 눌러도 최대
    //    한 주기 동안 옛 축이 그려진다.
  }, [key, frame]);

  return {
    rows,
    watch,
    exposure,
    reconcile,
    current: target,
    state,
    health,
    link,
    error,
    refresh: pullList,
    setError,
  };
}
