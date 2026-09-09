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
      // 연결 오류 — EventSource 가 스스로 다시 붙는다. 닫지 않는다.
    };
    source.addEventListener("progress", onProgress as EventListener);
    source.addEventListener("result", onResult as EventListener);
    source.addEventListener("error", onError as EventListener);
    return () => source.close();
  }, [jobId]);
  return state;
}
