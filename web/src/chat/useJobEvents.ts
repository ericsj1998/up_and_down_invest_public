/**
 * 작업(job) 진행을 SSE 로 따라간다 — `GET /ai/jobs/{id}/events` (이름 있는 이벤트 progress · result · error).
 *
 * `useStream.ts` 와 같은 관용구지만 기본 `message` 가 아니라 **이름 있는 이벤트**를 듣는다. 재접속하면 서버가 로그를
 * 처음부터 재생하므로 끊겨도 잃는 줄이 없다.
 */

import { useEffect, useState } from "react";

export type JobState = {
  lines: string[];
  result: Record<string, unknown> | null;
  error: string | null;
  done: boolean;
};

const EMPTY: JobState = { lines: [], result: null, error: null, done: false };

export function useJobEvents(jobId: string | null): JobState {
  const [state, setState] = useState<JobState>(EMPTY);
  useEffect(() => {
    if (!jobId) {
      setState(EMPTY);
      return;
    }
    setState(EMPTY);
    const source = new EventSource(`/api/ai/jobs/${encodeURIComponent(jobId)}/events`);
    const onProgress = (event: MessageEvent<string>) => {
      try {
        const body = JSON.parse(event.data) as { line?: string };
        if (typeof body.line === "string") setState((was) => ({ ...was, lines: [...was.lines, body.line ?? ""] }));
      } catch {
        // 모양이 다르면 무시 — 진행 줄은 장식이다.
      }
    };
    const onResult = (event: MessageEvent<string>) => {
      try {
        const body = JSON.parse(event.data) as Record<string, unknown>;
        setState((was) => ({ ...was, result: body, done: true }));
      } catch {
        setState((was) => ({ ...was, error: "결과를 읽지 못했다", done: true }));
      }
      source.close();
    };
    const onError = (event: Event) => {
      const data = (event as MessageEvent<string>).data;
      if (typeof data === "string") {
        try {
          const body = JSON.parse(data) as { detail?: string };
          setState((was) => ({ ...was, error: body.detail ?? "실패", done: true }));
          source.close();
          return;
        } catch {
          // 아래로
        }
      }
      // ⭐ 서버가 그 작업을 모르면(재시작 뒤 기억한 옛 id · 404) 브라우저는 다시 붙지 않고 **닫는다**.
      //    이걸 "연결 오류" 로만 두면 jobId 가 영영 남아 첫 질문이 무시된다 — 추천 질문을 두 번 눌러야
      //    했던 원인(사용자 2026-09-11). 끝난 것으로 표시해 화면이 jobId 를 비우게 한다.
      if (source.readyState === EventSource.CLOSED) {
        setState((was) => ({ ...was, error: was.done ? was.error : "이전 작업을 서버가 잊었다(재시작) — 다시 물어본다", done: true }));
        return;
      }
      // 연결 오류 — EventSource 가 스스로 다시 붙는다. 닫지 않는다.
    };
    source.addEventListener("progress", onProgress as EventListener);
    source.addEventListener("result", onResult as EventListener);
    source.addEventListener("error", onError as EventListener);
    return () => source.close();
  }, [jobId]);
  return state;
}
