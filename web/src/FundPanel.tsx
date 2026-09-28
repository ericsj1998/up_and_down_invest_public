/**
 * 리밸런싱 펀드 패널 (T61) — RUN 위에 뜨는 능동형 인덱스.
 *
 * RUN 과 다른 물건이다: 여러 종목을 묶어 하나의 펀드로 굴린다. 여기서 펀드를 만들고
 * (바스켓·시작자본·레버리지), 현황(잔고·TWR·종목별)을 보고, 수동 틱·입출금을 누른다.
 *
 * ⚠️ 자동 4h 루프는 testnet 검증 뒤에 켠다 — 지금은 "지금 리밸런싱" 버튼으로 수동.
 */
import { Fragment, useEffect, useState } from "react";

import * as api from "./api";
import type { FundLeg, FundRules, FundStatus, MarketInfo, MemberFrame } from "./api";
import { useMe } from "./Gate";
import { bookInGroup, groupOfName, marketTradeAllowed, useMarketGroup } from "./shell/marketGroup";
import { when } from "./shell/MarketHours";
import { CARD_WIDTH, FundMembers, MEMBER_FRAME_SPEC } from "./FundMembers";
import { unrealizedPct } from "./fundMembers";
import { FundOverview } from "./FundOverview";
import {
  groupedOrder,
  groupPnl,
  hourGuard,
  legGroups,
  positionRank,
  previewShort,
  previewText,
  type SymbolGroupView,
  type Tick,
} from "./fundLayout";
import { ErrorCard, SelectField } from "./ui";

/**
 * 펀드 종목 → 다리 조합 묶음 — 표 · 히트맵 · 차트 카드가 같은 순서를 쓴다 (사용자 2026-09-27).
 *
 * 묶음 안 순서 = 지금 상태(`positionRank` · 진입 가능 → 이득 보유 → 이득 실현 → 손해 보유 → 손해 실현 → 현금) → 거래대금.
 */
function groupsOf(f: FundStatus, ticks: Record<string, Tick>): SymbolGroupView[] {
  return legGroups(f.legs ?? [], Object.keys(f.per_symbol), ticks, (sym) =>
    positionRank(f.per_symbol[sym]),
  );
}

/** 남은 ms → "0:23". */
function clockText(ms: number): string {
  const sec = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

/**
 * 종목 표 줄 — 묶음 머리 줄과 종목 줄을 잇는다. 묶음이 하나면 머리 줄 없이.
 *
 * `onlyHeld` 면 **보유 · 진입 가능 종목만** — 표를 접어 둬도 포지션이 잡힌 종목과 깜빡이는 종목은 같은 표로
 * 보인다(사용자 2026-09-27). 그런 종목이 없는 묶음은 머리 줄도 뺀다.
 */
function tableRows(
  groups: SymbolGroupView[],
  f: FundStatus,
  onlyHeld = false,
): ({ group: SymbolGroupView } | { sym: string; v: FundLeg })[] {
  return groups.flatMap((g) => {
    const rows = g.symbols.flatMap((sym) => {
      const v = f.per_symbol[sym];
      return v && (!onlyHeld || v.holding === true || !!v.preview) ? [{ sym, v }] : [];
    });
    if (rows.length === 0) return [];
    return [...(groups.length > 1 ? [{ group: g }] : []), ...rows];
  });
}

/** 접힌 표에도 보일 종목 수 — 보유 + 진입 가능. */
function heldCount(f: FundStatus): number {
  return Object.values(f.per_symbol).filter((v) => v.holding === true || !!v.preview).length;
}

/** 상세보기 시간축 단추 순서. */
const MEMBER_FRAMES: MemberFrame[] = ["1h", "4h", "1d"];
const PREF_TF = "fund.members.timeframe";
const PREF_WIDTH = "fund.members.width";

/** 보는 사람 편의 값 읽기 — 저장소가 막힌 브라우저(사생활 창 등)에서도 화면은 기본값으로 뜬다. */
function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 못 적어도 이번 화면에서는 그대로 쓴다 — 다음에 기본값으로 뜰 뿐이다.
  }
}

// ⭐ 기본 바스켓·전략은 서버가 준다 (/rebalancer/defaults · SSoT = config/baskets.yml).
//    화면 상수로 두면 반드시 낡는다 — "코어4" 상수가 실제 라이브 6종과 어긋났던 게
//    이 파생의 이유다 (T63 ② · §0).

function pct(twr: string): string {
  const v = Number(twr) * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 다음 리밸런싱까지 남은 시간 — "2시간 34분" 꼴. */
function countdown(iso?: string | null): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "곧";
  const mins = Math.floor(ms / 60000);
  const h = Math.floor(mins / 60);
  return h > 0 ? `${h}시간 ${mins % 60}분` : `${mins}분`;
}

/** 손익 색: 양수 초록 · 음수 빨강 · **0 은 무채색** — 0 을 초록으로 칠하면 "수익 중"
 * 으로 읽힌다 (사용자 신고 2026-08-25). */
function pnlColor(v?: string): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "inherit";
  // 손익 색은 토큰 하나(--gain/--loss)에서 온다 — 화면 전체가 같은 색이어야 한다 (T220 UX 점검).
  return n > 0 ? "var(--gain)" : "var(--loss)";
}

/** 가격 표기 — 소수점 벽("77598.400000000000000000")을 자르되 알트의 유효 소수는 남긴다. */
function fmtPrice(v?: string | null): string {
  const n = Number(v);
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { maximumFractionDigits: 6 })
    : "—";
}

/** 소수점 벽 정리 — "150.000000000000000000" → "150". */
function fmt(v?: string | null): string {
  const n = Number(v);
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : "—";
}

/** 손익(실현·미실현)을 그 종목 몫 평가액 대비 %로 — " (+8.5%)". 기준 없으면 빈 문자열.

손익은 항상 금액+% 병기다 (사용자 확정 2026-08-26: "이건 기본이야"). */
function pnlPct(value?: string, equity?: string): string {
  const v = Number(value);
  const e = Number(equity);
  if (!Number.isFinite(v) || !Number.isFinite(e) || e === 0) return "";
  const p = (v / e) * 100;
  return ` (${p >= 0 ? "+" : ""}${p.toFixed(1)}%)`;
}

/**
 * 입출금 메모 기본값 — "2026-09-07 13:05 KST 입금 · 거래소에 넣은 돈을 펀드 잔고에 반영" (사용자 2026-09-07).
 * 시각은 한국 시간으로 적는다(표시는 KST · 서버 저장은 UTC — 규칙 #7). 사람이 고쳐 써도 된다.
 */
function noteFor(kind: "in" | "out"): string {
  const stamp = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
    .format(new Date())
    .replace("T", " ");
  return kind === "in"
    ? `${stamp} KST 입금 · 거래소에 넣은 돈을 펀드 잔고에 반영`
    : `${stamp} KST 출금 · 펀드 잔고에서 빼 거래소로 회수`;
}

/** 펀드 전체 미실현 합 — 종목별 거래소 미실현을 더한다 (보유 중인 것만 값이 있다). */
function fundUnreal(f: FundStatus): number {
  return Object.values(f.per_symbol).reduce((sum, v) => {
    const u = Number(v.unrealized);
    return sum + (Number.isFinite(u) ? u : 0);
  }, 0);
}

export function FundPanel({
  openRun,
}: {
  /** 판을 연다 — 종목 카드의 접힘 모서리가 쓴다. 없으면 모서리를 안 그린다. */
  openRun?: (run: string, name?: string) => void;
} = {}) {
  const [funds, setFunds] = useState<FundStatus[]>([]);
  const [cash, setCash] = useState("200");
  // 🔴 레버리지 입력은 제거됐다 (사용자 확정 2026-09-02) — 배율은 매매법 선언이
  //    유일한 출처다. 화면 입력은 "측정된 배율"을 손으로 덮는 통로였다.
  const [label, setLabel] = useState("리밸런싱 펀드");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState("");
  // 상세보기 차트 시간축 · 칸 너비 — 보는 사람 편의라 이 브라우저에만 기억한다(사용자 2026-09-27).
  const [memberTf, setMemberTfState] = useState<MemberFrame>(() => {
    const saved = readPref(PREF_TF);
    return MEMBER_FRAMES.includes(saved as MemberFrame) ? (saved as MemberFrame) : "1d";
  });
  const [memberWidth, setMemberWidthState] = useState<number>(() => {
    const saved = Number(readPref(PREF_WIDTH));
    return saved >= CARD_WIDTH.min && saved <= CARD_WIDTH.max ? saved : CARD_WIDTH.initial;
  });
  const setMemberTf = (tf: MemberFrame) => {
    setMemberTfState(tf);
    writePref(PREF_TF, tf);
  };
  const setMemberWidth = (px: number) => {
    setMemberWidthState(px);
    writePref(PREF_WIDTH, String(px));
  };
  const [editRows, setEditRows] = useState<
    { symbol: string; weight: string }[]
  >([]);
  // 구성 편집 안에서 고르는 전략 — 행의 select 로 바로 바꾸던 것을 없앴다 (사용자 2026-09-07).
  const [editPlaybook, setEditPlaybook] = useState("");
  // 🔴 돈이 움직이는 일은 팝업(prompt/confirm)이 아니라 화면 안 입력칸에서 (사용자 2026-09-07).
  const [pending, setPending] = useState<{
    id: string;
    kind: "in" | "out" | "drop" | "resync";
  } | null>(null);
  const [flowAmount, setFlowAmount] = useState("");
  const [flowNote, setFlowNote] = useState("");
  const [books, setBooks] = useState<
    {
      id: string;
      label: string;
      leverage?: number | null;
      backtest_note?: string;
      groups?: ("coin" | "stock")[];
      fund_rules?: FundRules | null;
    }[]
  >([]);
  const [playbook, setPlaybook] = useState("");
  const [market, setMarket] = useState("GATE");
  /** 장 마감 중 만들기 — 확인 패널에 띄울 다음 개장 (null 이면 패널 없음). */
  const [closedAsk, setClosedAsk] = useState<{ nextOpen: string | null } | null>(
    null,
  );
  // ⭐ T261 — 펀드 하나의 상세(종목마다 일봉 + 상태). 한 번에 하나만 편다.
  const [detail, setDetail] = useState<string | null>(null);
  // ⭐ 40줄 종목 표는 접어 둔다 — 대부분 "현금 · — · 0" 이라 늘 펼칠 까닭이 없다 (사용자 2026-09-27).
  const [tableOpen, setTableOpen] = useState<string | null>(null);
  // 종목 순위(거래대금 · 24시간 등락) — 히트맵 색과 묶음 안 순서. 거래소별로 1분마다.
  const [ticks, setTicks] = useState<Record<string, Record<string, Tick>>>({});
  const fundMarkets = [...new Set(funds.map((f) => f.market || "GATE"))].join(",");
  useEffect(() => {
    let alive = true;
    const pull = () => {
      for (const market of fundMarkets.split(",").filter(Boolean)) {
        api
          .ranking(market)
          .then((r) => {
            if (!alive) return;
            const map: Record<string, Tick> = {};
            for (const row of r.rows) map[row.symbol] = { change: row.change ?? null, turnover: row.turnover ?? null };
            setTicks((old) => ({ ...old, [market]: map }));
          })
          .catch(() => undefined); // 시세를 못 읽으면 히트맵이 무채색 · 순서는 이름순 — 화면이 그 사실을 적는다
      }
    };
    pull();
    const timer = setInterval(pull, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [fundMarkets]);
  const [members, setMembers] = useState<{ symbol: string; weight: string }[]>(
    [],
  );
  const [missing, setMissing] = useState<string[]>([]);
  const [markets, setMarkets] = useState<string[]>([]);
  // ⭐ T245 — 고른 시장 묶음의 거래소·매매법만.
  const [group] = useMarketGroup();
  const [infos, setInfos] = useState<MarketInfo[]>([]);
  useEffect(() => {
    api
      .exchangeMarkets()
      .then((r) => setInfos(r.all ?? []))
      .catch(() => setInfos([]));
  }, []);
  const groupMarkets = markets.filter((m) => groupOfName(infos, m) === group);
  // ⭐ T242 — 이 묶음에서 거래할 권한이 없으면 만들기 단추를 잠근다(서버가 403 으로 다시 막는다).
  const me = useMe();
  const mayTradeHere = marketTradeAllowed(me.who, group);
  const groupBooks = books.filter((b) => bookInGroup(b, group));
  useEffect(() => {
    if (groupMarkets.length && !groupMarkets.includes(market)) setMarket(groupMarkets[0] ?? market);
  }, [groupMarkets.join(","), market]);
  // 🔴 **묶음을 바꾸면 매매법도 그 묶음 것으로** (2026-09-11 실측). 코인에서 주식으로 옮겨도
  //    `playbook` 은 코인 것으로 남았는데, `<select>` 는 목록에 없는 값을 **첫 항목처럼**
  //    그린다 — 화면은 주식 매매법을 고른 것처럼 보이고 근거 카드와 서버로 가는 값은 코인
  //    것이었다(레버리지 6x · BN 6.58년). 고른 것과 보내는 것이 갈리면 사람은 알 길이 없다.
  useEffect(() => {
    if (!groupBooks.length) return;
    if (groupBooks.some((b) => b.id === playbook)) return;
    setPlaybook(groupBooks[0]?.id ?? "");
  }, [groupBooks.map((b) => b.id).join(","), playbook]);

  const refresh = () =>
    api
      .fundList()
      .then((r) => setFunds(r.funds))
      .catch(() => {});

  useEffect(() => {
    refresh();
    api
      .playbooks()
      .then((r) => {
        setBooks(
          r.playbooks.map((b) => ({
            id: b.id,
            label: b.label,
            leverage: b.leverage,
            backtest_note: b.backtest_note,
            groups: b.groups,
            fund_rules: b.fund_rules,
          })),
        );
        const live = r.live_markets ?? [];
        setMarkets(live);
        // 기본 GATE 가 목록에 없으면(로컬 데모 = BINANCE) 첫 거래소로 — 안 그러면 만들기가 400 이다.
        setMarket((prev) =>
          live.length && !live.includes(prev) ? (live[0] ?? prev) : prev,
        );
      })
      .catch(() => {});
    const t = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    // 거래소를 바꾸면 기본 바스켓도 다시 받는다 — testnet 에 없는 종목이 다르다.
    // 매매법을 바꿔도 다시 받는다 — 매매법별 바스켓(측정된 우주)이 있으면 그것으로 (2026-09-17).
    api
      .fundDefaults(market, playbook)
      .then((d) => {
        setMembers(d.members);
        setMissing(d.missing);
        setPlaybook((prev) => prev || d.playbook);
      })
      .catch(() => {});
  }, [market, playbook]);

  // 선택한 전략 — 선언 배율·기준 백테스트 표기 전용 (서버가 같은 선언을 읽는다).
  const selBook = books.find((b) => b.id === playbook) ?? null;

  // 🔴 **장 밖이면 먼저 말한다** (사용자 2026-09-11: *"장이 닫혀 있으면 예약이 걸리는 건가?
  //    그걸 사용자한테 물어봐야 할 것 같은데"*). 주문 창은 장 밖이면 409 로 막지만(예약 주문
  //    없음), 펀드는 만들어 두고 개장을 기다리는 것이 쓸모 있다 — 대신 **예약이 아니라 대기**
  //    라는 것을 만들기 전에 분명히 한다. 그냥 만들면 "만들었는데 아무 일도 안 일어난다" 가 된다.
  const create = async (confirmed = false) => {
    setBusy("create");
    setErr("");
    try {
      if (!confirmed && group !== "coin") {
        const status = await api.marketStatus(market).catch(() => null);
        if (status && !status.always_open && status.state === "closed") {
          setClosedAsk({ nextOpen: status.next_open });
          return;
        }
      }
      setClosedAsk(null);
      await api.fundCreate({
        label,
        total_cash: cash,
        playbook,
        market,
        members,
      });
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const runTick = async (id: string) => {
    setBusy(id);
    setErr("");
    try {
      await api.fundTick(id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  // ⭐ 정각 가드 (사용자 2026-09-27) — 매시 정각 ± 30초에 누르면 안내 · 타이머를 띄우고 구간이 끝나면 돈다.
  //    봉 마감 진입이 그 사이에 크기를 정하고 체결된다(`fundLayout.hourGuard`).
  const [deferred, setDeferred] = useState<{ id: string; until: number } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    if (!deferred) return;
    const timer = window.setInterval(() => setClock(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [deferred]);
  useEffect(() => {
    if (deferred && clock >= deferred.until) {
      const id = deferred.id;
      setDeferred(null);
      void runTick(id);
    }
    // runTick 은 매 렌더 새로 만들어진다 — 구간 끝을 넘는 순간 한 번만 부른다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clock, deferred]);
  const tick = (id: string) => {
    const now = Date.now();
    const guard = hourGuard(now);
    setClock(now);
    if (guard) {
      setDeferred({ id, until: guard.until });
      return;
    }
    void runTick(id);
  };

  const drop = async (id: string) => {
    setBusy(id);
    setErr("");
    try {
      await api.fundDrop(id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const resync = async (id: string) => {
    setBusy(id);
    setErr("");
    try {
      await api.fundResync(id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const startEdit = (f: FundStatus) => {
    setEditing(f.fund_id);
    setEditPlaybook(f.playbook);
    setPending(null);
    setEditRows(
      f.symbols.map((s) => ({
        symbol: s,
        weight: f.per_symbol[s]?.weight || "1",
      })),
    );
  };

  const applyEdit = async (f: FundStatus) => {
    const id = f.fund_id;
    setBusy(id);
    setErr("");
    try {
      if (editPlaybook && editPlaybook !== f.playbook) {
        await api.fundChangePlaybook(id, editPlaybook);
      }
      await api.fundEditBasket(
        id,
        editRows.filter((r) => r.symbol.trim() !== ""),
      );
      setEditing("");
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  /**
   * 입금·출금 — 부호는 **단추가** 정하고, 금액은 **화면 안 입력칸**에서 받는다 (사용자 2026-09-07:
   * 팝업 입력은 위험하다). 서버 경로는 하나(`/deposit` · 양수 입금 · 음수 출금).
   *
   * ⛔ 거래소 이체가 아니다. 원장의 잔고와 예산 배분만 바뀐다 — 돈은 사람이 옮긴다.
   */
  /** 입출금 패널을 연다 — 금액은 비우고 메모는 한국 시각이 적힌 기본 문구로 채운다. */
  const openFlow = (f: FundStatus, kind: "in" | "out") => {
    setPending({ id: f.fund_id, kind });
    setFlowAmount("");
    setFlowNote(noteFor(kind));
  };

  const submitFlow = async (f: FundStatus, kind: "in" | "out") => {
    const value = Number(flowAmount.trim());
    const balance = Number(f.balance);
    if (!Number.isFinite(value) || value <= 0) {
      setErr("금액은 0 보다 큰 숫자여야 한다");
      return;
    }
    if (kind === "out" && Number.isFinite(balance) && value > balance) {
      setErr(`출금 ${value} 이 잔고 ${balance.toFixed(2)} 를 넘는다`);
      return;
    }
    setBusy(f.fund_id);
    setErr("");
    try {
      await api.fundDeposit(
        f.fund_id,
        kind === "in" ? String(value) : String(-value),
        flowNote,
      );
      setPending(null);
      setFlowAmount("");
      setFlowNote("");
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  return (
    <div
      className="card wide"
      style={{ borderLeft: "3px solid var(--cyan-edge)" }}
    >
      <h3 style={{ margin: "0 0 4px" }}>리밸런싱 펀드 (능동형 인덱스)</h3>
      <p className="card-hint" style={{ marginTop: 0 }}>
        여러 종목을 하나로 묶어 굴린다. RUN 과 다른 물건 — 매 주기 예산을 다시
        나눠 리밸런싱. 입금은 성과(TWR)가 아니라 잔고만 올린다.
      </p>

      {/* ⭐ 펀드는 시장 하나에 속한다(`market`) — 지금 보는 묶음(코인/주식)의 펀드만 그린다 (사용자 2026-09-10). */}
      {funds.filter((f) => !f.market || groupOfName(infos, f.market) === group).length === 0 && (
        <p className="empty">아직 펀드가 없다 — 아래에서 만든다</p>
      )}

      {funds.filter((f) => !f.market || groupOfName(infos, f.market) === group).map((f) => (
        <div key={f.fund_id} className="card" style={{ marginBottom: 8 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>
              {f.market === "BINANCE" ? (
                <span style={{ color: "#d29922", marginRight: 6 }}>BN</span>
              ) : (
                <span className="faint" style={{ marginRight: 6 }}>
                  GT
                </span>
              )}
              {f.label}
            </strong>
            <span>
              잔고 <b>{fmt(f.balance)}</b> · TWR{" "}
              {/* 손익 색은 화면 전체가 한 값(.gain/.loss)이어야 한다 — 인라인 초록·빨강을 걷었다 (T220 UX 점검). */}
              <b className={Number(f.twr_return) >= 0 ? "gain" : "loss"}>
                {pct(f.twr_return)}
              </b>{" "}
              · 미실현{" "}
              <b className={fundUnreal(f) >= 0 ? "gain" : "loss"}>
                {fundUnreal(f).toLocaleString(undefined, {
                  maximumFractionDigits: 2,
                })}
                {Number(f.balance) > 0
                  ? ` (${fundUnreal(f) >= 0 ? "+" : ""}${((fundUnreal(f) / Number(f.balance)) * 100).toFixed(1)}%)`
                  : ""}
              </b>{" "}
              {/* 🔴 **펀드 전체 낙폭** (사용자 요구 2026-08-30). RUN 별 낙폭은 판마다의
                  것이라 백테스트 MDD(포트폴리오 곡선)와 비교가 안 된다 — 펀드 층에서
                  재야 같은 자다. 기준은 **TWR 지수**라 입출금이 낙폭을 왜곡하지 않는다
                  (입금이 낙폭을 지우고 출금이 없는 낙폭을 만드는 일이 없다). */}
              · 낙폭{" "}
              <b
                className={Number(f.drawdown_pct ?? 0) > 0 ? "loss" : undefined}
              >
                {Number(f.drawdown_pct ?? 0).toFixed(1)}%
              </b>
              <span className="faint">
                {" "}
                (최대 {Number(f.max_drawdown_pct ?? 0).toFixed(1)}%)
              </span>{" "}
              · 레버 {f.leverage}x · {f.symbols.length}종 · 전략 {f.playbook} ·
              다음 리밸런싱 <b>{countdown(f.next_tick)}</b>
              {/* 자동 앵커(T285) — 총자본이 거래소 계좌에 맞춰졌나. 못 맞추면 이유를 그대로 보여 준다. */}
              {f.anchor ? (
                f.anchor.skipped ? (
                  <span className="faint" title={f.anchor.skipped}>
                    {" "}
                    · 앵커 없음({f.anchor.skipped})
                  </span>
                ) : (
                  <span className="faint">
                    {" "}
                    · 앵커 {f.anchor.mode === "account" ? "계좌" : "계좌−유휴"}
                    {f.anchor.at ? ` ${new Date(f.anchor.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
                  </span>
                )
              ) : null}
            </span>
          </div>
          {/* 🔴 **거래소와 갈린 종목이 있으면 위 잔고·TWR 은 미확정이다** (2026-09-01
              사용자 신고). 원장이 거래소보다 앞서 손익을 찍어 두면(예: 안 채워진
              메이커 청산·고아 포지션) 그 값이 여기 헤드라인에 섞인다. 값을 지우지는
              않되(원장이 SSoT) 무엇이 미확정인지 이름을 대 준다 — 콘솔에서 되받거나
              정리하면 사라진다. */}
          {f.mismatch && f.mismatch.length > 0 ? (
            <p className="notice bad" style={{ margin: "6px 0" }}>
              🔴 {f.mismatch.join(", ")} — 거래소와 원장이 갈렸다. 위 잔고·TWR
              은 아직 거래소가 확인하지 않은 손익을 담고 있다.{" "}
              <button
                className="btn small danger"
                onClick={() => setPending({ id: f.fund_id, kind: "resync" })}
                disabled={busy === f.fund_id}
                title="갈린 세션의 원장을 지금 거래소 상태에 앵커한다 — 리셋 아님(계정 안 건드림)"
                style={{ marginLeft: 4 }}
              >
                {busy === f.fund_id ? "재정렬 중…" : "거래소로 원장 재정렬"}
              </button>
            </p>
          ) : null}
          <FundOverview
            f={f}
            groups={groupsOf(f, ticks[f.market || "GATE"] ?? {})}
            ticks={ticks[f.market || "GATE"] ?? {}}
            {...(openRun ? { openRun } : {})}
          />
          {tableOpen === f.fund_id || heldCount(f) > 0 ? (
          <>
          {tableOpen !== f.fund_id ? (
            <div className="text-xs faint" style={{ marginTop: 8 }}>
              보유 중 · 진입 가능(깜빡임) 종목 — 40종 전체는 아래 "종목 표"
            </div>
          ) : null}
          <table
            className="fund-table"
            style={{
              width: "100%",
              fontSize: 13,
              marginTop: 6,
              // 🔴 고정 레이아웃 + 명시 폭 — 내용 따라 열이 늘어나면 카드 두 장을 위아래로
              //    놓았을 때 같은 열이 서로 어긋난다 (사용자 신고 2026-08-25).
              tableLayout: "fixed",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <thead style={{ opacity: 0.6 }}>
              <tr>
                <td className="col-sym" style={{ width: "13%" }}>종목</td>
                <td className="opt" style={{ width: "6%", textAlign: "right" }}>비중</td>
                <td
                  className="opt"
                  style={{ width: "14%", textAlign: "right" }}
                  title="이 종목에 배정된 예산 + 그 종목의 손익 — 원장의 몫이다. 입금하면 주문 없이도 는다"
                >
                  몫 (예산+손익)
                </td>
                <td
                  className="opt"
                  style={{ width: "12%", textAlign: "right" }}
                  title="거래소가 이 종목 포지션에 실제로 잡고 있는 증거금 — 주문이 들어간 만큼만 는다"
                >
                  포지션 증거금
                </td>
                <td className="col-pnl" style={{ width: "16%", textAlign: "right" }}>미실현</td>
                <td className="col-pnl" style={{ width: "18%", textAlign: "right" }}>실현손익</td>
                <td className="col-pos" style={{ width: "21%", paddingLeft: 16 }}>포지션</td>
              </tr>
            </thead>
            <tbody>
              {tableRows(
                groupsOf(f, ticks[f.market || "GATE"] ?? {}),
                f,
                tableOpen !== f.fund_id,
              ).map((item) =>
                "group" in item ? (
                  <tr key={`g-${item.group.key}`} className="fund-group-row">
                    <td colSpan={7}>
                      {item.group.label} · {item.group.symbols.length}종
                      {(() => {
                        // 묶음 손익 — 이 묶음 줄들의 실현 + 미실현 · 그 줄들 몫 합 대비 %(줄마다의 % 와 같은 자).
                        const g = groupPnl(item.group.symbols, f.per_symbol);
                        return (
                          <span style={{ marginLeft: 8, color: pnlColor(String(g.amount)) }}>
                            {g.amount > 0 ? "+" : ""}
                            {fmt(String(g.amount))} USDT
                            {g.pct === null ? "" : ` (${g.pct > 0 ? "+" : ""}${g.pct.toFixed(2)}%)`}
                          </span>
                        );
                      })()}
                    </td>
                  </tr>
                ) : (
                  ((sym: string, v: FundLeg) => (
                <Fragment key={sym}>
                <tr
                  className={v.preview ? "entry-soon" : undefined}
                  title={v.preview ? previewText(v.preview) : undefined}
                >
                  <td>{sym}</td>
                  <td className="opt" style={{ textAlign: "right" }}>{v.weight}</td>
                  <td className="opt" style={{ textAlign: "right" }}>{fmt(v.equity)}</td>
                  <td className="opt" style={{ textAlign: "right" }}>
                    {v.holding && Number(v.margin) > 0 ? fmt(v.margin) : "—"}
                  </td>
                  <td
                    style={{
                      textAlign: "right",
                      color: pnlColor(v.unrealized),
                    }}
                  >
                    {v.holding
                      ? fmt(v.unrealized) + pnlPct(v.unrealized, v.equity)
                      : "—"}
                  </td>
                  {/* ⚠️ 거래소와 갈린 종목(포지션 갈림 reconciled · 회계 갈림
                      accounting_ok)은 원장 실현손익이 **허구**다. 거래소 실측으로 귀속된
                      진짜 값(verified_realized)이 있으면 **그것을** 그린다 — 원장 허구(+21)
                      대신 실측(-4.22)을 보여주고, 색을 죽여 미확정임을 표시 (2026-09-01). */}
                  {(() => {
                    const diverged =
                      v.reconciled === false || v.accounting_ok === false;
                    const shown =
                      diverged && v.verified_realized != null
                        ? v.verified_realized
                        : v.realized;
                    return (
                      <td
                        style={{
                          textAlign: "right",
                          color: diverged ? "#8b949e" : pnlColor(shown),
                        }}
                        title={
                          diverged
                            ? v.verified_realized != null
                              ? `거래소 실측 실현손익 ${fmt(v.verified_realized)} (원장 허구 ${fmt(v.realized)} 대신) — 미확정·동결 격리`
                              : "거래소와 원장이 갈렸다 — 미확정이라 동결 격리됐다"
                            : undefined
                        }
                      >
                        {fmt(shown)}
                        {Number(shown) !== 0 ? pnlPct(shown, v.equity) : ""}
                        {diverged
                          ? v.verified_realized != null
                            ? " ⚠︎실측"
                            : " ⚠︎"
                          : ""}
                      </td>
                    );
                  })()}
                  <td
                    className="col-pos"
                    style={{
                      paddingLeft: 16,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {v.position
                      ? v.shares && v.shares.length > 1
                        ? `${v.position.side} · 몫 ${v.shares.length}`
                        : `${v.position.side} @ ${fmtPrice(v.position.entry)}`
                      : v.holding
                        ? "보유"
                        : v.preview
                          ? previewShort(v.preview)
                          : "현금"}
                  </td>
                </tr>
                {/* ⭐ T320 — 한 종목을 다리 여럿이 나눠 쓰면 몫마다 한 줄(D3 ①): 다리 · 몫 증거금 ·
                    몫 미실현(그 몫 증거금 대비 %) · 진입가 · 계약. 종목 줄의 미실현 = 몫 줄 합. */}
                {v.shares && v.shares.length > 1
                  ? v.shares.map((sh) => {
                      const sp = unrealizedPct(sh.unrealized, v.equity);
                      return (
                        <tr key={`${sym}:${sh.leg}`} className="share-row text-sm">
                          <td className="faint" style={{ paddingLeft: 12 }}>
                            └ {sh.name}
                          </td>
                          <td className="opt" />
                          <td className="opt" />
                          <td className="opt faint" style={{ textAlign: "right" }}>
                            {sh.margin ? fmt(sh.margin) : "—"}
                          </td>
                          <td
                            style={{ textAlign: "right", color: pnlColor(sh.unrealized ?? undefined) }}
                          >
                            {sh.unrealized === null || sh.unrealized === undefined
                              ? "—"
                              : fmt(sh.unrealized) +
                                (sp === null ? "" : ` (${sp >= 0 ? "+" : ""}${sp.toFixed(1)}%)`)}
                          </td>
                          <td />
                          <td className="col-pos faint" style={{ paddingLeft: 16 }}>
                            {`${sh.side} @ ${fmtPrice(sh.entry)} · ${sh.contracts}계약`}
                          </td>
                        </tr>
                      );
                    })
                  : null}
                </Fragment>
                  ))(item.sym, item.v)
                ),
              )}
            </tbody>
          </table>
          </>
          ) : null}
          <div
            className="row"
            style={{ marginTop: 6, gap: 12, alignItems: "center", flexWrap: "wrap" }}
          >
            <button
              className="btn small"
              onClick={() => setTableOpen(tableOpen === f.fund_id ? null : f.fund_id)}
              title="종목마다 비중 · 몫 · 증거금 · 손익 · 포지션 — 매매법 묶음 · 거래대금 순"
            >
              {tableOpen === f.fund_id ? "종목 표 접기" : `종목 표 (${f.symbols.length}종)`}
            </button>
            <button
              className="btn small"
              onClick={() => setDetail(detail === f.fund_id ? null : f.fund_id)}
              title="종목마다 마감 봉 차트와 몫·포지션·등락을 한눈에"
            >
              {detail === f.fund_id ? "상세 접기" : "상세보기"}
            </button>
            {/* ⭐ 사용자 2026-09-27: 시간축을 카드 전부 한 번에 · 칸 크기 가로 슬라이더 — "상세 접기" 오른쪽. */}
            {detail === f.fund_id ? (
              <>
                <span className="row" style={{ gap: 4 }} role="group" aria-label="차트 시간축">
                  {MEMBER_FRAMES.map((tf) => (
                    <button
                      key={tf}
                      type="button"
                      className={`btn small${memberTf === tf ? " picked" : ""}`}
                      aria-pressed={memberTf === tf}
                      onClick={() => setMemberTf(tf)}
                    >
                      {MEMBER_FRAME_SPEC[tf].label}
                    </button>
                  ))}
                </span>
                <label className="row text-sm faint" style={{ gap: 6 }}>
                  칸 크기
                  <input
                    type="range"
                    min={CARD_WIDTH.min}
                    max={CARD_WIDTH.max}
                    step={CARD_WIDTH.step}
                    value={memberWidth}
                    onChange={(e) => setMemberWidth(Number(e.target.value))}
                    aria-label="차트 칸 크기"
                    style={{ width: 160 }}
                  />
                </label>
              </>
            ) : null}
          </div>
          {detail === f.fund_id ? (
            <FundMembers
              fundId={f.fund_id}
              order={groupedOrder(groupsOf(f, ticks[f.market || "GATE"] ?? {}))}
              timeframe={memberTf}
              cardWidth={memberWidth}
              previews={Object.fromEntries(
                Object.entries(f.per_symbol).map(([sym, v]) => [sym, v.preview ?? null]),
              )}
              {...(openRun ? { openRun } : {})}
            />
          ) : null}

          {editing === f.fund_id ? (
            <div
              className="card"
              style={{ marginTop: 6, background: "rgba(110,168,254,0.06)" }}
            >
              <p className="card-hint" style={{ marginTop: 0 }}>
                구성 편집 — 종목/비중을 바꾸고 적용. 새 종목은 세션이 뜨고, 뺀
                종목은 청산된다. 전략을 바꾸면 무중단으로 갈아탄다 — 열린
                포지션은 새 전략이 이어받는다.
              </p>
              <SelectField
                label="전략"
                value={editPlaybook}
                onChange={setEditPlaybook}
                options={[
                  // 🔴 도는 전략이 목록(listed)에 없으면 끼운다 — 없으면 React 가 첫 option 을
                  //    골라 화면이 거짓말한다 (2026-09-01 · 규칙 #8).
                  ...(books.some((b) => b.id === f.playbook)
                    ? []
                    : [
                        {
                          value: f.playbook,
                          label: `${f.playbook} (목록에 없음)`,
                        },
                      ]),
                  ...books.map((b) => ({ value: b.id, label: b.label })),
                ]}
              />
              {editRows.map((r, i) => (
                <div key={i} className="row" style={{ marginBottom: 4 }}>
                  <input
                    value={r.symbol}
                    placeholder="BTC_USDT"
                    onChange={(e) => {
                      const next = [...editRows];
                      next[i] = { symbol: e.target.value, weight: r.weight };
                      setEditRows(next);
                    }}
                    style={{ flex: 2 }}
                  />
                  <input
                    value={r.weight}
                    placeholder="비중"
                    onChange={(e) => {
                      const next = [...editRows];
                      next[i] = { symbol: r.symbol, weight: e.target.value };
                      setEditRows(next);
                    }}
                    style={{ flex: 1 }}
                  />
                  <button
                    className="btn small"
                    onClick={() =>
                      setEditRows(editRows.filter((_, j) => j !== i))
                    }
                  >
                    빼기
                  </button>
                </div>
              ))}
              <div className="row" style={{ marginTop: 4 }}>
                <button
                  className="btn small"
                  onClick={() =>
                    setEditRows([...editRows, { symbol: "", weight: "1" }])
                  }
                >
                  + 종목
                </button>
                <button
                  className="btn small primary"
                  onClick={() => applyEdit(f)}
                  disabled={busy === f.fund_id}
                >
                  {busy === f.fund_id ? "적용 중…" : "적용"}
                </button>
                <button className="btn small" onClick={() => setEditing("")}>
                  취소
                </button>
              </div>
            </div>
          ) : (
            <>
              {deferred?.id === f.fund_id ? (
                <div className="notice" style={{ marginTop: 6 }} role="status">
                  ⏳ 매시 정각 앞뒤 30초는 봉 마감 진입이 크기를 정하고 체결되는 시간이라 리밸런싱을 미룹니다 —{" "}
                  {deferred.until - clock > 30_000 ? (
                    <>
                      정각까지 <b>{clockText(deferred.until - 30_000 - clock)}</b> · 정각 30초 뒤 자동으로 리밸런싱
                    </>
                  ) : (
                    <>
                      리밸런싱까지 <b>{clockText(deferred.until - clock)}</b>
                    </>
                  )}
                  <button
                    type="button"
                    className="btn small"
                    style={{ marginLeft: 8 }}
                    onClick={() => setDeferred(null)}
                  >
                    취소
                  </button>
                </div>
              ) : null}
              <div className="row" style={{ marginTop: 6 }}>
                <button
                  className="btn small"
                  onClick={() => tick(f.fund_id)}
                  disabled={busy === f.fund_id || deferred?.id === f.fund_id}
                >
                  {busy === f.fund_id ? "…" : deferred?.id === f.fund_id ? "대기 중" : "지금 리밸런싱"}
                </button>
                <button
                  className="btn small primary"
                  onClick={() => openFlow(f, "in")}
                  disabled={busy === f.fund_id}
                >
                  입금
                </button>
                <button
                  className="btn small"
                  onClick={() => openFlow(f, "out")}
                  disabled={busy === f.fund_id}
                >
                  출금
                </button>
                <button
                  className="btn small"
                  onClick={() => startEdit(f)}
                  disabled={busy === f.fund_id}
                >
                  구성 편집
                </button>
                <span className="card-hint" style={{ margin: 0 }}>
                  전략:{" "}
                  {books.find((b) => b.id === f.playbook)?.label ?? f.playbook}
                  {" · 바꾸려면 구성 편집"}
                </span>
                <button
                  className="btn small danger"
                  onClick={() => setPending({ id: f.fund_id, kind: "drop" })}
                  disabled={busy === f.fund_id}
                  style={{ marginLeft: "auto" }}
                >
                  삭제
                </button>
              </div>
              {pending?.id === f.fund_id ? (
                <div
                  className="card"
                  style={{ marginTop: 6, background: "rgba(110,168,254,0.06)" }}
                >
                  {pending.kind === "in" || pending.kind === "out" ? (
                    <>
                      <p className="card-hint" style={{ marginTop: 0 }}>
                        {pending.kind === "in"
                          ? "입금 — 거래소에 실제로 넣은 만큼 적는다. 잔고와 종목별 예산만 늘고 성과(TWR)는 안 오른다. 주문은 나가지 않는다."
                          : `출금 — 지금 잔고 ${fmt(f.balance)} USDT. 열린 포지션은 그대로고 다음 진입부터 예산이 준다. 거래소 이체는 따로 한다.`}
                      </p>
                      <div className="row" style={{ alignItems: "flex-end" }}>
                        <label className="field">
                          금액 (USDT)
                          <input
                            type="number"
                            min="0"
                            step="any"
                            value={flowAmount}
                            onChange={(e) => setFlowAmount(e.target.value)}
                            placeholder="0"
                            autoFocus
                          />
                        </label>
                        <label className="field" style={{ flex: 1 }}>
                          메모 (선택)
                          <input
                            value={flowNote}
                            onChange={(e) => setFlowNote(e.target.value)}
                            placeholder="예: 9/7 거래소 입금"
                          />
                        </label>
                        <button
                          className={
                            pending.kind === "in"
                              ? "btn small primary"
                              : "btn small danger"
                          }
                          onClick={() =>
                            submitFlow(f, pending.kind === "in" ? "in" : "out")
                          }
                          disabled={
                            busy === f.fund_id || flowAmount.trim() === ""
                          }
                        >
                          {busy === f.fund_id
                            ? "…"
                            : pending.kind === "in"
                              ? `${fmt(flowAmount || "0")} 입금 확인`
                              : `${fmt(flowAmount || "0")} 출금 확인`}
                        </button>
                        <button
                          className="btn small"
                          onClick={() => {
                            setPending(null);
                            setFlowAmount("");
                            setFlowNote("");
                          }}
                        >
                          취소
                        </button>
                      </div>
                    </>
                  ) : pending.kind === "drop" ? (
                    <div className="row">
                      <span
                        className="card-hint"
                        style={{ margin: 0, flex: 1 }}
                      >
                        펀드를 접는다 — 소유한 세션이 모두 청산·정리되고 되돌릴
                        수 없다.
                      </span>
                      <button
                        className="btn small danger"
                        onClick={() => {
                          setPending(null);
                          drop(f.fund_id);
                        }}
                        disabled={busy === f.fund_id}
                      >
                        정말 삭제
                      </button>
                      <button
                        className="btn small"
                        onClick={() => setPending(null)}
                      >
                        취소
                      </button>
                    </div>
                  ) : (
                    <div className="row">
                      <span
                        className="card-hint"
                        style={{ margin: 0, flex: 1 }}
                      >
                        갈린 세션의 원장을 지금 거래소 상태에 앵커한다 — 복구
                        불가한 과거는 버리고 지금부터 정확히 추적한다. 리셋이
                        아니다(거래소 계정·돈·포지션은 안 건드림).
                      </span>
                      <button
                        className="btn small danger"
                        onClick={() => {
                          setPending(null);
                          resync(f.fund_id);
                        }}
                        disabled={busy === f.fund_id}
                      >
                        재정렬 실행
                      </button>
                      <button
                        className="btn small"
                        onClick={() => setPending(null)}
                      >
                        취소
                      </button>
                    </div>
                  )}
                </div>
              ) : null}
            </>
          )}
        </div>
      ))}

      <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
        <label className="field">
          이름
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="field">
          시작 자본
          <input value={cash} onChange={(e) => setCash(e.target.value)} />
        </label>
        <SelectField
          label="거래소"
          value={market}
          onChange={setMarket}
          options={groupMarkets.map((m) => ({ value: m }))}
        />
        <SelectField
          label="전략"
          value={playbook}
          onChange={setPlaybook}
          options={[
            ...(books.length === 0 && playbook ? [{ value: playbook }] : []),
            ...groupBooks.map((b) => ({ value: b.id, label: b.label })),
          ]}
        />
        <button
          className="btn primary"
          onClick={() => create()}
          disabled={busy === "create" || !mayTradeHere}
          title={mayTradeHere ? undefined : "이 시장에서 거래할 권한이 없다 — 관리자가 준다"}
        >
          {mayTradeHere ? (busy === "create" ? "만드는 중…" : "펀드 만들기") : "🔒 거래 권한 없음"}
        </button>
      </div>
      {closedAsk && (
        <div className="book-card" role="alert">
          <span className="card-name">지금은 장 마감이다</span>
          <p className="muted">
            펀드를 만들면 종목마다 RUN 이 뜨지만, 판정은
            {closedAsk.nextOpen ? ` 다음 개장(${when(closedAsk.nextOpen)})` : " 다음 개장"}
            부터 시작한다. <b>예약 주문이 아니라 대기다</b> — 그때까지 주문은 나가지 않고, 개장하면
            매매법이 스스로 자리를 찾는다.
          </p>
          <div className="row">
            <button
              className="btn primary"
              onClick={() => create(true)}
              disabled={busy === "create"}
            >
              {busy === "create" ? "만드는 중…" : "그래도 만든다"}
            </button>
            <button className="btn" onClick={() => setClosedAsk(null)}>
              취소
            </button>
          </div>
        </div>
      )}
      {/* 레버리지 입력칸을 없앤 대신 고르는 근거를 카드로 보여 준다 (사용자 2026-09-03).
          칩은 flex-wrap 이라 확대해도 자연스럽게 줄바꿈된다. 값은 플레이북 선언이 출처. */}
      {selBook && (
        <div className="book-card">
          <span className="card-name">선택한 매매법 — 측정 근거</span>
          <div className="book-chips">
            <span className="book-chip strong">
              레버리지{" "}
              {selBook.leverage != null ? `${selBook.leverage}x` : "선언 없음"}
            </span>
            {/* 펀드 규칙 선언(T279 P3·V2) — 자리·총 명목 상한·연속 손절 정지. 값은 서버가 플레이북에서 읽는다. */}
            {selBook.fund_rules?.weight_mode === "slots" && (
              <span className="book-chip strong">
                {`자리 ${selBook.fund_rules.slots}`}
                {selBook.fund_rules.notional_cap != null
                  ? ` · 총 명목 ${selBook.fund_rules.notional_cap}x`
                  : ""}
                {selBook.fund_rules.halt_after_stops > 0
                  ? ` · 같은 날 연속 손절 ${selBook.fund_rules.halt_after_stops} 이면 정지`
                  : ""}
                {/* T286 — 화면에 입력칸이 없는 규칙이다. 여기가 사람이 볼 수 있는 유일한 자리다. */}
                {selBook.fund_rules.notional_fit ? " · 상한에 걸리면 줄여서 진입" : ""}
              </span>
            )}
            {/* 🔴 낙폭 브레이크는 **자리 배분과 무관한** 펀드 층 장치라 위 칩 밖에 있다.
                안에 두면 비중 배분 매매법이 브레이크를 선언해도 화면에 한 글자도 안 나온다. */}
            {selBook.fund_rules?.drawdown_brake && (
              <span className="book-chip strong">
                {`낙폭 ${(
                  Number(selBook.fund_rules.drawdown_brake.at) * 100
                ).toFixed(1).replace(/\.0$/, "")}% 아래면 신규 x${
                  selBook.fund_rules.drawdown_brake.scale
                }`}
              </span>
            )}
            {(selBook.backtest_note ?? "")
              .split(" · ")
              .filter(Boolean)
              .map((part) => (
                <span key={part} className="book-chip">
                  {part}
                </span>
              ))}
          </div>
        </div>
      )}
      <p className="card-hint">
        기본 바스켓{" "}
        {members.length > 0
          ? members
              .map((m) => `${m.symbol.replace("_USDT", "")} ${m.weight}`)
              .join(" · ")
          : "(서버에서 불러오는 중…)"}
        {missing.length > 0 &&
          ` — testnet 제외: ${missing.map((s) => s.replace("_USDT", "")).join("·")} (실계좌에선 포함)`}
        . 구성의 SSoT 는 config/baskets.yml 이다. 각 종목에 비중대로 세션이
        뜬다. ⚠️ 같은 종목 RUN 이 돌고 있으면 먼저 종료해야 한다.
      </p>
      {err ? (
        <ErrorCard
          message={err}
          title="펀드 작업 실패"
          onClose={() => setErr("")}
        />
      ) : null}
    </div>
  );
}
