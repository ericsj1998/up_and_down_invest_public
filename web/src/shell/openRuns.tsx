/**
 * 열린 판 목록 — 셸 상태 (T220 · App.tsx 에서 옮겨 왔다).
 *
 * 🔴 **연 RUN 은 사이드바의 한 절이다** (옛 화면에선 탭 리본). 고정 항목 하나로 두면 RUN 을 바꿀 때마다
 *    앞엣것을 잃고, 나란히 비교할 수가 없다 (사용자 요구 2026-08-19).
 *
 * ⚠️ **연 목록을 기억한다** (localStorage · 키는 옛 화면과 같다 — 재편 전에 열어 둔 판이 그대로 남는다).
 *
 * 🔴 **같은 값이면 상태를 안 건드린다.** 판 화면이 걸음마다 이름을 다시 알려 주는데, 매번 새 객체를
 *    만들면 사이드바가 계속 다시 그려진다. 중복 검사는 갱신 함수 **안**에서 한다 — StrictMode 가
 *    효과를 두 번 돌리면 바깥의 `includes` 는 갱신 전 값을 본다 (2026-08-19 실측).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

const SLOT = "open-runs";
const NAMES = "open-run-names";

function remembered(): string[] {
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(SLOT) ?? "[]");
    if (!Array.isArray(kept)) return [];
    // ⚠️ 읽을 때도 중복을 턴다 — 옛 버그가 겹쳐 저장한 적이 있고, key 가 겹치면 클릭이 안 먹는다.
    return [...new Set(kept.filter((item) => typeof item === "string"))];
  } catch {
    // ⛔ 저장소를 못 써도 화면은 돌아야 한다 — 사생활 모드·용량 초과가 있다.
    return [];
  }
}

function rememberedNames(): Record<string, string> {
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(NAMES) ?? "{}");
    return kept && typeof kept === "object" ? (kept as Record<string, string>) : {};
  } catch {
    return {};
  }
}

export interface OpenRuns {
  /** 열린 판 id 들 — 연 순서. */
  opened: string[];
  /** 판을 목록에 넣는다(이미 있으면 그대로). 이름을 알면 같이 기억한다. */
  open: (run: string, name?: string) => void;
  /** 목록에서 뺀다 — RUN 은 계속 돈다. */
  close: (run: string) => void;
  /** 판이 어느 종목인지 알아냈다 — 사이드바 이름에 쓴다. */
  remember: (run: string, name: string) => void;
  /** 사람이 읽는 이름. 못 읽으면 **id 그대로** — 지어내지 않는다. */
  label: (run: string) => string;
}

const Ctx = createContext<OpenRuns | null>(null);

export function OpenRunsProvider({ children }: { children: ReactNode }) {
  const [opened, setOpened] = useState<string[]>(remembered);
  const [names, setNames] = useState<Record<string, string>>(rememberedNames);

  useEffect(() => {
    try {
      localStorage.setItem(SLOT, JSON.stringify(opened));
    } catch {
      // 기억만 못 한다. 이번 화면은 정상으로 뜬다.
    }
  }, [opened]);

  useEffect(() => {
    try {
      localStorage.setItem(NAMES, JSON.stringify(names));
    } catch {
      // 기억만 못 한다.
    }
  }, [names]);

  const remember = useCallback((run: string, name: string) => {
    setNames((was) => (was[run] === name ? was : { ...was, [run]: name }));
  }, []);

  const open = useCallback(
    (run: string, name?: string) => {
      setOpened((was) => (was.includes(run) ? was : [...was, run]));
      if (name) remember(run, name);
    },
    [remember],
  );

  const close = useCallback((run: string) => {
    setOpened((was) => was.filter((item) => item !== run));
  }, []);

  const label = useCallback((run: string) => (names[run] ? `${names[run]} RUN` : run), [names]);

  const value = useMemo<OpenRuns>(
    () => ({ opened, open, close, remember, label }),
    [opened, open, close, remember, label],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOpenRuns(): OpenRuns {
  const ctx = useContext(Ctx);
  if (!ctx) {
    // ⛔ 조용히 빈 목록을 돌려주지 않는다 — 프로바이더를 빠뜨린 것은 코드 결함이다.
    throw new Error("useOpenRuns 는 OpenRunsProvider 안에서만 쓴다");
  }
  return ctx;
}
