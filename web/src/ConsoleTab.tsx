/**
 * 거래소 콘솔 — **거래소가 실제로 들고 있는 것**을 보고 앱에서 정리한다.
 *
 * ⚠️ **여기는 원장이 아니다.** 실계좌 페이퍼 탭은 손익률을 곱해 나가는 **모형**이고
 * 이 탭은 **사실**이다. 둘이 갈리는 것이 가장 위험한 상태였으므로, 이 화면은 원장을
 * **한 번도 읽지 않는다** — 섞으면 무엇을 보고 있는지 잃는다.
 *
 * 🔴 RUN 이 목록에서 사라진 뒤 남은 포지션도 여기서 닫을 수 있다. 그런 상황이 실제로
 * 났고, 그때는 거래소 웹에 로그인하는 수밖에 없었다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMe } from "./Gate";
import {
  adoptOrphan,
  cancelOrder,
  cancelStop,
  closePosition,
  consoleBalances,
  dropSession,
  exchange,
  exchangeMarkets,
  modeCookie,
  switchMode,
  syncReconcile,
  type Exchange,
  type MarketInfo,
  type Reconcile,
} from "./api";
import { Escape } from "./Escape";
import { History } from "./History";
import { Faucet } from "./Faucet";
import { Leftovers } from "./Leftovers";
import { Ranking } from "./Ranking";
import { MacroPanel } from "./MacroPanel";
import { ValueRanking } from "./ValueRanking";
import { StockOrder } from "./StockOrder";
import { FundPanel } from "./FundPanel";
import { Runs } from "./Runs";
import { Sound } from "./Sound";
import { useLive } from "./useLive";
import {
  Card,
  confirmedNaked,
  Disconnected,
  Fold,
  num,
  runOf,
  runTag,
  UNKNOWN_RUN,
  useFold,
  whenSec,
} from "./ui";
import { positionsSummary } from "./consoleSummary";
import { connection } from "./api";
import { BrokerMark } from "./shell/BrokerMark";
import { MarketHours } from "./shell/MarketHours";
import {
  brokerOfName,
  capsOfName,
  pickMarkets,
  useMarketGroup,
} from "./shell/marketGroup";

/** ⚠️ 거래소를 두드리는 일이라 느긋하게. 정리하려고 여는 화면이지 초를 보는 곳이 아니다. */
// 2026-09-05: 4초는 1 GB 서버에서 CPU 를 다 먹었다(요청마다 거래소 왕복). 10초면 충분하다.
const POLL_MS = 10_000;

/**
 * 무방비를 **진짜로 치기까지** 기다리는 시간(ms) — 폴링 두 번.
 *
 * 🔴 체결로 포지션이 생긴 뒤 러너가 조건부를 걸기까지 몇 초가 걸린다. 그 사이를
 * 무방비로 읽으면 **진입할 때마다** 경보음이 울린다 (사용자 신고 2026-08-20).
 *
 * ⭐ 폴링 주기에 묶어 둔다 — 따로 상수를 박으면 주기를 바꿀 때 한쪽만 남는다.
 */
const NAKED_GRACE_MS = POLL_MS * 2;

/** Gate 조건부의 만료를 사람 말로. */
function expiry(seconds: string): string {
  const parsed = Number(seconds);
  if (!Number.isFinite(parsed) || parsed <= 0) return "만료 없음";
  return `${Math.round(parsed / 3600)}시간 뒤 만료`;
}

/**
 * 거래소 대조 한 줄 — **정상일 때도 뜬다**.
 *
 * 🔴 갈린 것이 있을 때만 배너를 띄우면 *정상* 과 *루프가 죽었다* 가 화면에서
 * 구별되지 않는다. 안전장치가 건강할 때 안 보이면 믿을 근거가 없다.
 *
 * ⚠️ `stale`(마지막 대조가 주기의 3배를 넘었다)은 그 자체가 경보다 — 옛 결과를
 * 현재처럼 보여 주는 것이 이 기능의 최악 실패다.
 */
function ReconcileLine({ now }: { now: Reconcile | null }) {
  if (!now) {
    return (
      <p className="notice bad">
        🔴 거래소 대조 상태를 못 읽었다 — 서버가 옛 판이거나 목록 조회가
        실패했다.
      </p>
    );
  }
  const ago = now.at
    ? Math.round((Date.now() - Date.parse(now.at)) / 1000)
    : null;
  if (now.stale) {
    return (
      <p className="notice bad">
        🔴 거래소 대조가 멈췄다 — 마지막{" "}
        {ago === null ? "기록 없음" : `${ago}초 전`} (주기 {now.period_s}초).
        원장과 거래소가 갈려도 **지금은 아무도 안 보고 있다**.
      </p>
    );
  }
  if (now.findings > 0) {
    return (
      <p className="notice bad">
        🔴 거래소 대조: {now.findings}건 갈렸다
        {now.blocked.length > 0
          ? ` · 신규 진입 보류 ${now.blocked.join(", ")}`
          : ""}{" "}
        (위 배너 참고)
      </p>
    );
  }
  // ⭐ 맞을 때는 **한 줄 작은 글자**다 — 배너는 갈렸을 때의 것이다 (UX 점검 2026-09-05). 정상 상태가 늘 큰 상자로
  //    떠 있으면 눈이 거기 붙고, 정작 갈렸을 때의 배너가 평소와 구별되지 않는다.
  return (
    <p className="faint" style={{ margin: 0, fontSize: 12 }} role="status">
      ✅ 거래소 대조 맞음 · {ago}초 전 · {now.period_s}초마다
    </p>
  );
}

type Props = {
  /** 판을 연다 — 주소를 바꾸는 것은 셸의 일이다. */
  openRun: (run: string, name?: string) => void;
};

/**
 * 권한을 사람이 쓰는 말로 — `WhoBar` 와 **같은 말**을 쓴다.
 *
 * @param role 서버가 준 등급.
 * @returns 화면에 적을 말.
 *
 * ⚠️ 두 곳이 다른 말을 쓰면 같은 상태가 다른 것처럼 보인다. 말이 갈리는 것은
 * 값이 갈리는 것만큼 나쁘다.
 */
export function roleName(role: string | undefined): string {
  if (role === "admin") return "관리자";
  if (role === "trader") return "거래 허용";
  if (role === "viewer") return "승인됨 · 읽기";
  return "승인 대기 · 읽기";
}

export function ConsoleTab({ openRun }: Props) {
  // 🔴 **셸에서 내려받지 않고 여기서 직접 묻는다.** `useMe` 는 훅이고 60초마다
  //    스스로 다시 읽는다 — 프롭으로 받으면 이 탭이 셸의 갱신 주기에 묶인다.
  const me = useMe();
  // 🔴 **여기가 홈이다** (사용자 확정 2026-08-19). 거래소가 말하는 사실이 첫 화면이고,
  //    판은 여기서 골라 들어간다.
  const board = useLive(true);
  // 알림 소리 설정은 접힌 채 아래에 — 소리는 접혀 있어도 울려야 하므로 Fold(언마운트) 대신 hidden 으로 가린다.
  const [soundOpen, toggleSound] = useFold("console-sound", true);
  // ⭐ 거래소마다 상태를 따로 받는다 (사용자 요구 2026-08-26 — BN 도 Gate 동급 콘솔).
  //   body = 상단 계좌 절이 보는 거래소, bodies = 범위 박스(전체 병합)의 재료.
  const [bodies, setBodies] = useState<Record<string, Exchange | null>>({});
  // ⭐ 거래소별 잔액 (사용자 요구 2026-08-26) — 이 콘솔의 나머지는 Gate 전용이라
  //   바이낸스 돈이 상단에 안 보였다. 서버가 QuoteAdapter 계약에서 파생해 준다.
  const [purses, setPurses] = useState<
    Record<
      string,
      {
        available: string;
        position_margin: string;
        broker?: string;
        margin_mode?: string;
        testnet?: string;
        error?: string;
        today_pnl?: string;
      }
    >
  >({});
  // ⭐ 상단 계좌 절의 거래소 선택 (사용자 요구 2026-08-26) — UUID·잔액·포지션이
  //   전부 Gate 것이었다. GATE 는 기존 상세(주문 콘솔 연동), 나머지는 요약 카드.
  const [mkt, setMkt] = useState("전체");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [link, setLink] = useState({ ok: true, silentFor: 0 });
  // 🔴 청산은 되돌릴 수 없다. 한 번 더 묻는다.
  const [confirming, setConfirming] = useState(false);
  // 🔴 고아 닫기도 되돌릴 수 없다 — **인라인 2단계**로 묻는다. `window.confirm` 은
  //    브라우저가 "추가 대화상자 차단" 을 걸면 조용히 false 라 클릭이 먹통이 된다
  //    (실측 2026-09-01 — POST 가 아예 안 나갔다). 어느 고아를 닫으려는지 판 id 로 잡는다.
  const [confirmClose, setConfirmClose] = useState("");
  // 🔴 **어느 판의 주문인가** (T18 ⑤). 판을 여럿 돌리면 콘솔 이력이 한 줄기로 섞인다.
  const [pick, setPick] = useState("전체");
  // ⭐ 지난 판 칩은 기본 숨김 (사용자 지적 2026-08-26) — 32개가 인라인으로 늘어서
  //   지금 판을 찾는 눈을 막았다. 단추를 눌러야 나온다.
  const [showPast, setShowPast] = useState(false);
  // ⭐ 종목별로 **처음 무방비로 본 시각**. 상태가 아니라 ref 다 — 이 값이 바뀐다고
  //    화면을 다시 그릴 이유가 없고, 다시 그리면 그 렌더가 또 시각을 흔든다.
  const seenNaked = useRef<Map<string, number>>(new Map());

  // 🔴 **문자열 키로 메모한다** (2026-09-05 실계좌 첫날 사고). `purses` 객체를 deps 로 두면
  //    폴링 응답마다 새 객체 → 새 marketList 배열 → 새 `pull` → `useEffect([pull])` 가 즉시 다시
  //    pull() 하고 타이머를 다시 건다. 결과: 응답이 올 때마다 또 요청 — 초당 3~5회 폭주
  //    (5분에 balances 797 · state 736, 그중 118 이 브라우저 중단 499). 화면은 "—" 만 보였고
  //    1 GB 서버 CPU 를 다 먹었다. 내용이 같으면 참조도 같아야 한다.
  // 🔴 **거래소 목록은 서버가 선언한다** (2026-09-06). 전에는 "GATE" 를 박아 두고 잔액 응답의 키를 더했다 —
  //    Gate 키가 없는 API(로컬 실계좌 모드)에서 콘솔이 GATE 상태를 묻다 503 을 띄웠다. 빈 목록이면 "연결 없음".
  // ⭐ 아는 거래소 전부 + 연결 여부. 칩은 전부 그리고(사람이 고른다), 폴링은 연결된 것만 돈다 (2026-09-06).
  const [markets, setMarkets] = useState<MarketInfo[] | null>(null);
  useEffect(() => {
    let alive = true;
    exchangeMarkets()
      .then(
        (r) =>
          alive &&
          setMarkets(
            r.all ??
              r.markets.map((name) => ({ name, ready: true, scoped: true })),
          ),
      )
      .catch(() => alive && setMarkets([]));
    return () => {
      alive = false;
    };
  }, []);
  // ⭐ T245 — 고른 시장 묶음(코인/주식)의 것만 본다. 칩·폴링·카드 전부 여기서 갈린다. 묶음이 무엇인지는 서버가 말한다.
  const [group] = useMarketGroup();
  const allMarkets = useMemo(
    () => pickMarkets(markets ?? [], group),
    [markets, group],
  );
  useEffect(() => setMkt("전체"), [group]);
  const marketKey = allMarkets
    .filter((m) => m.ready && m.scoped)
    .map((m) => m.name)
    .join(",");
  const marketList = useMemo(
    () => (marketKey ? marketKey.split(",") : []),
    [marketKey],
  );
  const isReady = (name: string) => marketList.includes(name);
  const noExchange = markets !== null && marketList.length === 0;
  // 어느 키가 없는지는 **모드**가 정한다 — 실계좌 모드면 실계좌 키, 데모면 테스트넷 키 (사용자 2026-09-06).
  const liveSide = modeCookie() === "live";
  const keyWord = liveSide ? "실계좌 API 키" : "테스트넷 API 키";
  const envHint = (name: string) =>
    liveSide
      ? `${name}_API_KEY / _SECRET — 서버의 .env.live 에 사람이 넣고 LIVE_ORDERS 를 켠다 (로컬에는 두지 않는다)`
      : `${name}_TESTNET_API_KEY / _SECRET — .env.dev(로컬) 또는 .env.demo(서버)에 넣고 API 를 다시 띄운다`;
  // ⭐ 거래소는 목록에 있는데 **키가 없다** (사용자 요구 2026-09-06: "테스트넷 API 키 설정이 필요합니다" 로 표시).
  //    서버는 "…_TESTNET_API_KEY / _SECRET 가 없다" 503 을 낸다 — 그 거래소 카드를 문구로 바꾸고 붉은 배너는 띄우지 않는다.
  const [keyless, setKeyless] = useState<Record<string, boolean>>({});
  const KEYLESS = /TESTNET_API_KEY/;
  // 거래소가 하나면 "전체" 는 그 거래소와 같다 — 칩을 숨기고 그 거래소 카드를 바로 그린다.
  // 연결된 거래소가 하나면 "전체" 는 그것이다. 사람이 연결 안 된 거래소를 골랐으면 그 이름이 그대로 남아 아래에서 키 카드가 뜬다.
  const effMkt: string =
    mkt === "전체" && marketList.length === 1 ? (marketList[0] ?? mkt) : mkt;

  const pull = useCallback(() => {
    for (const name of marketList) {
      exchange("BTC_USDT", name)
        .then((next) => {
          setBodies((prev) => ({ ...prev, [name]: next }));
          setError("");
        })
        .catch((exc: unknown) => {
          if (KEYLESS.test(String(exc))) {
            setKeyless((prev) =>
              prev[name] ? prev : { ...prev, [name]: true },
            );
            return;
          }
          // 첫 거래소 실패는 화면 전체의 문제다 — 그때만 에러 배너.
          if (name === marketList[0]) setError(String(exc));
          // "답했다" 는 사실은 남긴다 (값은 null) — 알림 소리가 이 거래소를 영원히 기다리지 않게.
          setBodies((prev) =>
            name in prev ? prev : { ...prev, [name]: null },
          );
        })
        .finally(() => setLink(connection()));
    }
    consoleBalances()
      .then((r) => setPurses(r.balances))
      .catch(() => {});
  }, [marketList]);

  useEffect(() => {
    pull();
    const timer = setInterval(pull, POLL_MS);
    return () => clearInterval(timer);
  }, [pull]);

  // 🔴 **액션 결과는 폴링과 다른 칸에 쓴다** (2026-09-03 사고). 되받기 실패 사유를
  //    `error` 에 썼더니 직후 pull() 성공이 `setError("")` 로 밀리초 만에 지워서,
  //    사람 눈에는 "버튼이 아무 일도 안 한다" 로 보였다. actNote 는 폴링이 못 지운다.
  // ⭐ 톤: bad=실패 · warn=처리중(⏳) · info=완료(✅) — 청산처럼 느린 액션은 진행이
  //    안 보이면 사람이 또 누른다 (사용자 2026-09-03).
  const [actNote, setActNote] = useState<{
    tone: "bad" | "warn" | "info";
    text: string;
  } | null>(null);
  const act = (name: string, run: () => Promise<unknown>) => {
    setBusy(name);
    setActNote(null);
    run()
      .then(() => pull())
      .catch((exc: unknown) => setActNote({ tone: "bad", text: String(exc) }))
      .finally(() => {
        setBusy("");
        setConfirming(false);
      });
  };

  // 🔴 `effMkt` 다 — 거래소가 하나면 mkt 는 "전체" 로 남는데 bodies["전체"] 는 없다. 그 한 글자
  //    차이로 실계좌 첫날 계정·잔액·자산 카드가 전부 "—" 였다 (2026-09-05).
  const body = bodies[effMkt] ?? null;
  // ⭐ 범위 박스(포지션·미결·조건부·이력)의 거래소 선택 — 전체 = 병합.
  const [boxMkt, setBoxMkt] = useState("전체");
  const scopedOf = useCallback(
    <T,>(
      sel: (one: Exchange) => T[] | undefined,
    ): (T & { market: string })[] => {
      const names = boxMkt === "전체" ? marketList : [boxMkt];
      return names.flatMap((name) => {
        const one = bodies[name];
        return (one ? (sel(one) ?? []) : []).map((row) => ({
          ...row,
          market: name,
        }));
      });
    },
    [bodies, boxMkt, marketList],
  );
  const boxPositions = useMemo(
    () => scopedOf((one) => one.positions),
    [scopedOf],
  );
  // ⭐ 청산가 열 — 표에 보이는 포지션의 시장 중 배율이 있는 곳이 하나라도 있으면 (능력표 · T245).
  const liqHere = boxPositions.some(
    (row) => capsOfName(allMarkets, String(row.market ?? "")).leverage,
  );
  const boxOrders = useMemo(() => scopedOf((one) => one.orders), [scopedOf]);
  const boxStops = useMemo(() => scopedOf((one) => one.stops), [scopedOf]);
  const boxHistory = useMemo(() => scopedOf((one) => one.history), [scopedOf]);
  // 계획·판 조인표는 병합 — 매매 id 가 전역 유일이라 안전하다.
  const boxPlans = useMemo(
    () =>
      Object.assign(
        {},
        ...marketList.map((name) => bodies[name]?.plans ?? {}),
      ) as Exchange["plans"],
    [bodies, marketList],
  );
  const boxRuns = useMemo(
    () =>
      Object.assign(
        {},
        ...marketList.map((name) => bodies[name]?.runs ?? {}),
      ) as Exchange["runs"],
    [bodies, marketList],
  );

  const history = boxHistory;
  // ⚠️ RUN 미상도 **목록에 남긴다** — 거르개에서 빼면 우리가 못 읽은 주문이 화면에서
  //    통째로 사라지고, 그것이 가장 알아야 할 것이다 (절대 규칙 #8).
  const runs = [...new Set(history.map((row) => runOf(row.text || "")))].sort(
    (a, b) =>
      a === UNKNOWN_RUN ? 1 : b === UNKNOWN_RUN ? -1 : a.localeCompare(b),
  );
  // 🔴 **거래소 이력은 지울 수 없다** (사용자 요구 2026-08-19: *"체결 이력 다 삭제해달라
  //    했는데 여전히 남아있는데?"*). 우리 DB 는 비웠지만 Gate 는 자기 장부를 계속 든다.
  //
  //    ⇒ **지우는 대신 접는다.** 지금 도는 판의 것만 기본으로 보여 주고, 지난 판은
  //      칩을 누르면 다시 나온다 — 사실을 감추지 않으면서 시야만 정리한다.
  //
  // ⚠️ **RUN 미상은 접지 않는다.** 우리가 못 읽은 주문이야말로 가장 알아야 할 것이고,
  //    손으로 닫은 것·거래소가 발동시킨 조건부가 여기 들어온다 (절대 규칙 #8).
  const alive = new Set(
    board.rows.filter((row) => row.live).map((row) => runTag(row.session_id)),
  );
  const past = runs.filter((run) => run !== UNKNOWN_RUN && !alive.has(run));
  const shown =
    pick === "전체"
      ? history.filter((row) => !past.includes(runOf(row.text || "")))
      : history.filter((row) => runOf(row.text || "") === pick);

  // ⭐ 전체 뷰의 합산 재료 (사용자 요구 2026-08-26: "총 자산 2개를 더하는거지").
  const agg = useMemo(() => {
    const per: Record<
      string,
      { avail: number; margin: number; unreal: number; count: number }
    > = {};
    let avail = 0;
    let margin = 0;
    let unreal = 0;
    let count = 0;
    for (const name of marketList) {
      const one = bodies[name];
      if (!one) continue;
      const rows = one.positions ?? [];
      const a = Number(one.balance.available) || 0;
      const m = rows.reduce((sum, row) => sum + (Number(row.margin) || 0), 0);
      const u = rows.reduce(
        (sum, row) => sum + (Number(row.unrealised_pnl) || 0),
        0,
      );
      per[name] = { avail: a, margin: m, unreal: u, count: rows.length };
      avail += a;
      margin += m;
      unreal += u;
      count += rows.length;
    }
    return { avail, margin, unreal, count, per };
  }, [bodies, marketList]);
  const perLine = (
    read: (one: {
      avail: number;
      margin: number;
      unreal: number;
      count: number;
    }) => number,
    digits = 2,
  ) =>
    marketList
      .map((name) => {
        const one = agg.per[name];
        return `${name} ${one ? num(read(one), digits) : "—"}`;
      })
      .join(" · ");

  // ⭐ 오늘 손익 카드 (사용자 요구 2026-08-26) — 금액+% 병기 원칙.
  const todayOf = (name: string): number | null => {
    const raw = purses[name]?.today_pnl;
    return raw === undefined || raw === "" ? null : Number(raw);
  };
  const todayCard = (names: string[], base: number | null) => {
    const parts = names.map((name) => ({ name, value: todayOf(name) }));
    if (parts.every((one) => one.value === null)) return null;
    const total = parts.reduce((sum, one) => sum + (one.value ?? 0), 0);
    const pctText =
      base && base > 0
        ? ` (${total >= 0 ? "+" : ""}${((total / base) * 100).toFixed(2)}%)`
        : "";
    return (
      <Card
        name="오늘 손익 (KST 00시~ · 실현)"
        value={`${total > 0 ? "+" : ""}${num(total, 2)} USDT${pctText}`}
        tone={total > 0 ? "gain" : total < 0 ? "loss" : undefined}
        hint={
          names.length > 1
            ? parts
                .map(
                  (one) =>
                    `${one.name} ${one.value === null ? "—" : num(one.value, 2)}`,
                )
                .join(" · ") + " · 수수료·펀딩 포함, 미실현 제외"
            : "수수료·펀딩 포함 순실현 — 미실현은 위 카드가 따로 든다"
        }
      />
    );
  };

  const held = body?.position ?? {};
  const open = Object.keys(held).length > 0;
  const size = Number(held["size"] ?? 0);
  // 🔴 **상단 카드는 계좌 전체다** (사용자 신고 2026-09-06). `held` 는 고른 종목(기본 BTC)의 것이라
  //    6종 펀드에서 ETH 에만 포지션이 있으면 "없음 · — · —" 을 그렸다 — 아래 표와 모순. 종목 하나의
  //    값(`held`·`open`·`size`·`pnl`)은 안전 배너 등 종목 문맥에만 남긴다.
  const all = positionsSummary(body?.positions ?? []);
  const allHint = (tail: string) =>
    all.count === 0
      ? tail
      : `${all.lines.slice(0, 3).join(" · ")}${all.count > 3 ? ` · +${all.count - 3}` : ""}`;
  // 🔴 **가장 위험한 조합을 전 종목에서 찾는다** (사용자 신고 2026-08-20).
  //    예전에는 고른 종목(BTC)의 포지션과 조건부만 봤다 — 판을 여섯 종목에서 돌리면
  //    SPCX 가 무방비여도 이 배너가 조용하고, 반대로 BTC 만 보고 "조건부 0건" 을
  //    무방비로 읽는다. 둘 다 틀린 방향으로 위험하다.
  // 🔴 **무장 중인 것을 무방비로 외치지 않는다** (사용자 신고 2026-08-20: *"실제 손절
  //    주문이 발행되기 전에 바로 뜨네. 그래서 얼럿 알림이 먼저 와."*).
  //
  //    체결로 포지션이 생긴 뒤 러너가 조건부를 걸기까지 몇 초가 걸린다. 그 사이의
  //    폴링이 *"포지션은 있는데 조건부 0건"* 을 보고 배너와 **경보음**까지 냈다.
  //
  // ⚠️ 사실을 감추는 것이 아니다 — 두 폴링 뒤에도 그대로면 그대로 뜬다. 다만 매 진입마다
  //    경보가 울리면 사람이 소리를 꺼 버리고, 끄면 진짜일 때도 못 듣는다.
  const naked = useMemo(() => {
    // ⚠️ **`bodies` 가 바뀔 때만 잰다** — 매 렌더마다 재면 `Date.now()` 가 계속 흔들린다.
    // 🔴 안전 배너는 범위 선택과 무관하게 **모든 거래소**를 본다 (2026-08-26).
    const armed = new Set(
      marketList.flatMap((name) =>
        (bodies[name]?.stops ?? []).map((row) => `${name}:${row.symbol}`),
      ),
    );
    const bare = marketList.flatMap((name) =>
      (bodies[name]?.positions ?? [])
        .filter((row) => !armed.has(`${name}:${row.symbol}`))
        .map((row) => ({
          ...row,
          symbol: `${name === "BINANCE" ? "BN " : ""}${row.symbol}`,
        })),
    );
    const step = confirmedNaked(
      bare,
      seenNaked.current,
      Date.now(),
      NAKED_GRACE_MS,
    );
    seenNaked.current = step.seen;
    return step.sure;
  }, [bodies, marketList]);

  // 🔴 **총 자산 = 쓸 수 있는 돈 + 포지션에 잡힌 증거금** (사용자 신고 2026-08-20).
  //
  // ⚠️ 계좌 요약의 `account_position_margin` 을 쓰면 안 된다 — 격리 마진에서 Gate 는
  //    그 값을 **0 으로 준다** (실측: 계좌 0 / 포지션 435.99). 포지션 목록을 더해야
  //    사실이 나온다.
  //
  // ⚠️ 못 읽으면 null 이다. 0 으로 두면 "다 잃었다" 로 보이고, 그것이 이 카드가
  //    막으려는 바로 그 오해다.
  const locked = (body?.positions ?? []).reduce(
    (sum, row) => sum + Number(row.margin ?? 0),
    0,
  );
  // ⭐ 거래소가 총액을 말해 주면 그것이 사실이다 (대기 주문 증거금까지 포함 · 2026-09-05).
  //    못 말하는 어댑터에서만 available + 포지션 증거금으로 계산한다.
  const orderMargin = body?.balance.order_margin
    ? Number(body.balance.order_margin)
    : 0;
  const total = body
    ? body.balance.total
      ? Number(body.balance.total)
      : Number(body.balance.available) + locked + orderMargin
    : null;
  // 🔴 % 의 분모는 **계좌 총액** — 상단 카드는 계좌 단위고 "오늘 손익" 과 같은 자여야 한다 (사용자 확정 2026-09-06:
  //    증거금 대비 +7.3% 와 계좌 대비 +0.1% 가 같은 화면에 섞여 헷갈렸다). 총액을 못 읽으면 % 를 내지 않는다.
  const allPnlPct =
    total !== null && total > 0 ? (all.pnl / total) * 100 : null;
  const allPnlText =
    all.count === 0
      ? "—"
      : `${all.pnl > 0 ? "+" : ""}${num(all.pnl, 2)} USDT${allPnlPct === null ? "" : ` (${allPnlPct > 0 ? "+" : ""}${allPnlPct.toFixed(2)}%)`}`;

  return (
    <div className="page">
      {/* ⚠️ 설명 문단을 뺐다 (사용자 요구 2026-08-19: *"저기에 글씨가 있는게 가독성이
          별로네"*). 한 번 읽으면 아는 문장이고, 매번 화면 위를 차지한다. */}
      <div className="page-head">
        <h1>거래 콘솔</h1>
      </div>

      {/* 🔴 **문제 있는 원장 요약 + 수동 동기화** (사용자 요구 2026-09-01). 개별 배너
          위에 몇 개인지 세어 주고, 30초 주기를 안 기다리고 지금 대조하거나 고아를
          한꺼번에 되받는 단추를 둔다. 자동 대조·복구가 돌지만, 사람이 즉시 손댈 수
          있어야 한다. */}
      {(() => {
        const orphans = board.watch.filter(
          (row) =>
            row.code === "reconcile_orphan_position" &&
            !!row.market &&
            !!row.symbol,
        );
        const others = board.watch.length - orphans.length;
        if (board.watch.length === 0) return null;
        return (
          <div
            className="notice bad"
            style={{
              display: "flex",
              gap: 10,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <b>🔴 문제 있는 원장 {board.watch.length}개</b>
            <span className="faint">
              (고아 {orphans.length}
              {others > 0 ? ` · 죽은 판 ${others}` : ""})
            </span>
            <button
              className="btn"
              disabled={busy !== ""}
              onClick={() =>
                act("sync", () => syncReconcile().then(() => board.refresh()))
              }
            >
              {busy === "sync" ? "대조 중…" : "지금 동기화"}
            </button>
            {orphans.length > 0 ? (
              <button
                className="btn primary"
                disabled={busy !== ""}
                onClick={() =>
                  act("adopt-all", async () => {
                    const failed: string[] = [];
                    let done = 0;
                    for (const row of orphans) {
                      setActNote({
                        tone: "warn",
                        text: `⏳ 되받는 중 ${done + 1}/${orphans.length} — ${row.symbol}…`,
                      });
                      const r = await adoptOrphan(
                        row.market as string,
                        row.symbol as string,
                      );
                      if (r.adopted !== "yes")
                        failed.push(`${row.run}: ${r.reason}`);
                      else done += 1;
                    }
                    await board.refresh();
                    setActNote(
                      failed.length
                        ? {
                            tone: "bad",
                            text: `일부 되받기 실패 — ${failed.join(" / ")}`,
                          }
                        : {
                            tone: "info",
                            text: `✅ ${done}개 되받기 완료 — 이제 원장이 관리한다`,
                          },
                    );
                  })
                }
              >
                {busy === "adopt-all"
                  ? "되받는 중…"
                  : `고아 ${orphans.length}개 모두 되받기`}
              </button>
            ) : null}
          </div>
        );
      })()}
      {/* 🔴 **판이 죽으면 여기에 뜬다** (T20 ③). 2026-08-19 에 RUN 2개가 죽고 ETH 숏
          246계약이 관리자 없이 남았는데, 화면에는 **목록에서 사라진 것**으로만 보였다.
          목록 위에 두는 이유는 그것이 증상이기 때문이다 — 사라진 판은 목록이 못 말한다. */}
      {board.watch.map((row) => {
        // 🔴 고아 포지션(원장이 모르는 거래소 포지션)에는 **되받기** 단추를 준다.
        //    매매법을 바꾸느라 판을 지웠다 새로 만들면 자동 이어받기의 창을 놓치는데,
        //    매번 판을 지웠다 만드는 대신 여기서 되받는다 (2026-09-01). 죽은 판
        //    (no_runner·task_dead·steps_stall)에는 단추가 없다 — 되받을 러너가 없다.
        const orphan =
          row.code === "reconcile_orphan_position" &&
          !!row.market &&
          !!row.symbol;
        const tag = `adopt:${row.run}`;
        // ⭐ 되살리기가 왜 안 됐는지는 `revive` 에 있다 — 숨기면 "실패했다" 만 보고 겁만 먹는다
        //    (2026-09-11: 로컬 데모가 BINANCE 만 연결한 뒤 NASDAQ 페이퍼 판 셋이 이 문구로만 떴다).
        //    이 API 가 연결하지 않은 시장이면 거래소 포지션 경고 대신 그 사실을 말한다.
        const notConnected =
          !!row.revive && row.revive.includes("연결하지 않은 거래소");
        return (
          <p key={row.run} className="notice bad">
            🔴 {row.run} — {row.detail}
            {row.guard ? (
              <>
                <br />
                거래소 대조: {row.guard}
              </>
            ) : null}
            {row.revive ? (
              <>
                <br />
                되살리기: {row.revive}
              </>
            ) : null}
            <br />
            {notConnected
              ? "이 API 가 그 시장을 연결하지 않아 러너를 띄울 수 없다 — 시장을 연결(UPDOWN_MARKETS)하거나 판을 닫는다."
              : "거래소에 포지션이 남아 있으면 지금 아무도 관리하지 않는다. 아래 포지션·조건부 주문을 확인한다."}
            {/* ⭐ 연결 안 된 시장의 죽은 판은 목록에 없어 닫을 자리가 없었다 (사용자 2026-09-11 "도는 RUN 0개인데?").
                여기서 닫는다 — 되돌릴 수 없어 인라인 2단계. 거래소·페이퍼 포지션은 그 시장에 못 닿아 못 건드린다고 말한다. */}
            {notConnected ? (
              confirmClose === row.run ? (
                <>
                  {" "}
                  <span className="loss">
                    판 기록을 닫는다 — 시장에 닿지 못해 포지션은 건드리지
                    않는다. 되돌릴 수 없다.
                  </span>{" "}
                  <button
                    className="btn primary"
                    disabled={busy !== ""}
                    onClick={() => {
                      setConfirmClose("");
                      act(`drop:${row.run}`, async () => {
                        setActNote({
                          tone: "warn",
                          text: `⏳ ${row.run} 닫는 중…`,
                        });
                        await dropSession(row.run);
                        await board.refresh();
                        setActNote({
                          tone: "info",
                          text: `✅ ${row.run} 닫음 — 배너는 다음 감시에서 사라진다`,
                        });
                      });
                    }}
                  >
                    닫는다
                  </button>{" "}
                  <button
                    className="btn small"
                    onClick={() => setConfirmClose("")}
                  >
                    취소
                  </button>
                </>
              ) : (
                <>
                  {" "}
                  <button
                    className="btn small"
                    disabled={busy !== ""}
                    onClick={() => setConfirmClose(row.run)}
                  >
                    판 닫기
                  </button>
                </>
              )
            ) : null}
            {orphan ? (
              <>
                {" "}
                <button
                  className="btn primary"
                  disabled={busy !== ""}
                  onClick={() =>
                    act(tag, async () => {
                      setActNote({
                        tone: "warn",
                        text: `⏳ ${row.symbol} 되받는 중…`,
                      });
                      const r = await adoptOrphan(
                        row.market as string,
                        row.symbol as string,
                      );
                      await board.refresh();
                      setActNote(
                        r.adopted === "yes"
                          ? {
                              tone: "info",
                              text: `✅ ${row.symbol} 되받기 완료 — 이제 원장이 관리한다`,
                            }
                          : {
                              tone: "bad",
                              text: `이어받기 안 됨 — ${r.reason}`,
                            },
                      );
                    })
                  }
                >
                  {busy === tag ? "되받는 중…" : "원장으로 되받기"}
                </button>
                {/* 🔴 손절 없는 고아는 되받기가 **항상 거부**한다 (계획을 지어낼 수
                    없으므로 · adopt 가드). 그때 답은 닫는 것이다 — 되돌릴 수 없어 인라인
                    2단계로 확인받는다 (window.confirm 은 차단되면 먹통). */}
                {confirmClose === row.run ? (
                  <>
                    {" "}
                    <span className="loss">
                      {row.symbol} 시장가 전량 청산 — 되돌릴 수 없다.
                    </span>{" "}
                    <button
                      className="btn primary"
                      disabled={busy !== ""}
                      onClick={() => {
                        setConfirmClose("");
                        act(`close:${row.run}`, async () => {
                          // ⏳→✅ 진행을 말한다 (사용자 2026-09-03) — 주문 접수 뒤에도
                          //    배너는 다음 대조까지 남으므로, 동기화를 즉석에서 돌려
                          //    "끝났다"가 화면에 바로 보이게 한다.
                          setActNote({
                            tone: "warn",
                            text: `⏳ ${row.symbol} 시장가 청산 주문 보내는 중…`,
                          });
                          await closePosition(
                            row.symbol as string,
                            row.market as string,
                          );
                          setActNote({
                            tone: "warn",
                            text: `⏳ ${row.symbol} 주문 접수됨 — 거래소 대조 갱신 중…`,
                          });
                          await syncReconcile();
                          await board.refresh();
                          setActNote({
                            tone: "info",
                            text: `✅ ${row.symbol} 청산 완료 — 배너가 남아 있으면 다음 대조에서 풀린다`,
                          });
                        });
                      }}
                    >
                      {busy === `close:${row.run}` ? "닫는 중…" : "정말 닫는다"}
                    </button>{" "}
                    <button
                      className="btn"
                      disabled={busy !== ""}
                      onClick={() => setConfirmClose("")}
                    >
                      취소
                    </button>
                  </>
                ) : (
                  <>
                    {" "}
                    <button
                      className="btn"
                      disabled={busy !== ""}
                      onClick={() => setConfirmClose(row.run)}
                    >
                      닫기 (시장가 전량)
                    </button>
                  </>
                )}
              </>
            ) : null}
          </p>
        );
      })}

      {/* 🔴 **거래소 대조는 건강할 때도 보여야 한다** (2026-08-30).
          갈린 것이 있을 때만 배너를 띄우면, 정상일 때와 **루프가 죽었을 때가
          화면에서 똑같다** — 사람이 이 안전장치를 믿을 근거가 없다.
          그래서 한 줄을 늘 띄우고, 멈췄으면 그것 자체를 경보로 만든다. */}
      <ReconcileLine now={board.reconcile} />

      {/* 🧹 "같은 방향 동시 노출" 경고는 은퇴 (사용자 확정 2026-08-26) — 추세필터는
          설계상 같은 방향을 한꺼번에 잡는 전략이고(롱 6판이 정상 상태), 바스켓 상관은
          백테스트 MDD 에 이미 들어 있다. 박스권 시절(T24 ③)의 유물이었다. */}

      {!link.ok ? <Disconnected silentFor={link.silentFor} /> : null}
      {error ? <p className="notice bad">{error}</p> : null}
      {actNote ? (
        <p
          className={`notice${actNote.tone === "info" ? "" : ` ${actNote.tone}`}`}
        >
          {actNote.text}
        </p>
      ) : null}

      {/* 🔴 **나가는 길** (사용자 요구 2026-08-20: *"말랐을 때, 어떻게 대처해야
          하는지도 있어야 하는 거 아닌가?"*). 시장가로 못 나가는 포지션에만 뜬다 —
          나갈 수 있으면 아래 전량 청산이 답이라 선택지를 늘리지 않는다.

          ⛔ 자동으로 안 던진다. 얼마에 거느냐가 곧 손실 결정이다 (실측: 던졌으면
          -420, 표시가 아래에서 기다렸더니 -74). */}
      {(body?.positions ?? []).map((row) => (
        <Escape
          key={`${effMkt}:${row.symbol}`}
          symbol={row.symbol}
          market={effMkt}
          onDone={pull}
        />
      ))}

      {/* 🔴 **판을 안 띄운 종목은 볼 사람이 없다** (사용자 요구 2026-08-21).
          XRP 조건부가 판이 지워진 뒤로 남아 있었는데 화면 어디에도 나올 자리가
          없었다 — 거래소를 직접 찔러서야 찾았다. */}
      {group === "coin" ? <Leftovers /> : null}

      {/* 🔴 가장 위험한 조합 — 포지션은 있는데 손절이 없다. */}
      {naked.length ? (
        <p className="notice bad">
          <b>
            손절 없는 포지션 {naked.length}건 —{" "}
            {naked.map((row) => row.symbol).join(" · ")} 이 무방비다.
          </b>{" "}
          조건부는 24시간에 조용히 사라진다.
        </p>
      ) : null}

      {/* ⭐ 계좌 절 거래소 선택 (사용자 요구 2026-08-26). 목록은 서버 파생 —
          거래소가 늘면 칩도 자동으로 는다. */}
      {/* 거래소가 하나면 칩도 "전체" 합산도 뜻이 없다 — 그 거래소 카드만 그린다 (2026-09-05 · 배포는 Gate 만). */}
      {allMarkets.length > 1 && (
        <div className="row" style={{ marginBottom: 8 }}>
          {[
            ...(marketList.length > 1 ? ["전체"] : []),
            ...allMarkets.map((m) => m.name),
          ].map((name) => {
            const ready = name === "전체" || isReady(name);
            return (
              <button
                key={name}
                type="button"
                className={`chip${mkt === name ? " live" : ""}`}
                style={ready ? undefined : { opacity: 0.7 }}
                title={
                  ready
                    ? undefined
                    : `${name} — ${keyWord} 설정이 필요하다. 눌러서 무엇이 필요한지 본다`
                }
                onClick={() => setMkt(name)}
              >
                <span className="inline-flex items-center gap-1.5">
                  {name}
                  {name === "전체" ? null : (
                    <BrokerMark broker={brokerOfName(allMarkets, name)} />
                  )}
                  {ready ? "" : " · 연결 필요"}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {/* T245 — 고른 시장의 머리: 시장 이름 + 브로커 마크(토스 로고 · GATE · BINANCE). "이 돈이 어느 계좌에 있나". */}
      {effMkt !== "전체" ? (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-blue-gray-700 dark:text-blue-gray-200">
          <span>{effMkt}</span>
          <BrokerMark broker={brokerOfName(allMarkets, effMkt)} size="md" />
          {/* 장 시간 — 24시간 장(코인)은 아무것도 안 그린다. */}
          <MarketHours
            market={effMkt}
            alwaysOpen={capsOfName(allMarkets, effMkt).alwaysOpen}
          />
        </div>
      ) : null}
      {group === "stock" ? <MacroPanel /> : null}
      <div className="cards">
        {effMkt !== "전체" && (keyless[effMkt] || !isReady(effMkt)) ? (
          <Card
            name={`${effMkt} 거래소`}
            value={`${keyWord} 설정이 필요합니다`}
            hint={`${envHint(effMkt)} — 키가 없으면 조회도 주문도 못 한다`}
          />
        ) : noExchange ? (
          <Card
            name="거래소 연결"
            value={`${keyWord} 설정이 필요합니다`}
            hint={
              liveSide ? (
                <>
                  이 API 에는 실계좌 키가 없다 — 화면·원장·백테스트 리포트만
                  본다. 위 칩에서 거래소를 고르면 무엇이 필요한지 보인다.{" "}
                  <button
                    type="button"
                    className="underline"
                    onClick={() => switchMode("demo")}
                  >
                    Demo Trading 으로 →
                  </button>
                </>
              ) : (
                "이 데모 API 에는 테스트넷 키가 없다 — 위 칩에서 거래소를 고르면 무엇이 필요한지 보인다"
              )
            }
          />
        ) : effMkt === "전체" ? (
          <>
            {/* 🔴 **로그인한 구글 계정** (사용자 요구 2026-08-30). 자리만 잡아 두고
                *"로컬 단일 사용자"* 라고 적어 두었는데, 로그인은 이미 붙어 있었다 —
                화면만 그 사실을 모르고 있었다.

                ⚠️ **누구로 보고 있는지가 이 콘솔의 전제다.** 승인 전 계정은 페이퍼와
                리포트만 열리고 실거래 콘솔은 안 보인다 — 무엇이 안 보이는지 물으려면
                먼저 자기가 누구인지 보여야 한다.

                ⛔ 못 읽었을 때 *"로컬 단일 사용자"* 로 떨어지지 않는다. 그것은 **틀린
                말**이고, 틀린 말은 없는 말보다 나쁘다 (규칙 #8). */}
            <Card
              name="유저"
              value={
                me.who?.email ?? (me.loading ? "확인 중…" : "로그인 안 됨")
              }
              hint={
                me.who?.signed_in
                  ? `${roleName(me.who.role)}${me.who.may_trade ? "" : " · 거래 불가"}`
                  : "구글 로그인이 필요하다 — 오른쪽 위에서 들어간다"
              }
            />
            {/* 전 거래소 합도 같은 순서 — 오늘 손익은 두 번째 (거래소별 카드와 같은 자리). */}
            {todayCard(marketList, agg.avail + agg.margin)}
            <Card
              name="계좌 총액 (전 거래소)"
              value={`${num(agg.avail + agg.margin)} USDT`}
              hint={perLine((one) => one.avail + one.margin)}
            />
            <Card
              name="쓸 수 있는 돈 (합)"
              value={`${num(agg.avail)} USDT`}
              hint={perLine((one) => one.avail)}
            />
            <Card
              name="포지션"
              value={`${agg.count}개`}
              hint={perLine((one) => one.count, 0)}
            />
            <Card
              name="잡혀 있는 증거금 (합)"
              value={num(agg.margin)}
              hint={perLine((one) => one.margin)}
            />
            <Card
              name="미실현 손익 (합)"
              value={`${num(agg.unreal, 2)} USDT`}
              tone={
                agg.unreal > 0 ? "gain" : agg.unreal < 0 ? "loss" : undefined
              }
              hint={perLine((one) => one.unreal)}
            />
          </>
        ) : (
          <>
            {/* 🔴 **거래소별 카드는 한 벌이다** (사용자 지적 2026-09-06: *"설마 다 따로 노나"* — GATE 와 그 외가
                따로 적혀 있어 오늘 손익 카드 자리를 GATE 만 옮기는 실수가 났다). 거래소 차이는 값과 힌트로만
                갈리고, 순서·구성은 여기 한 곳이다. GATE 에만 있는 것(UUID · 대기 증거금 · 화이트리스트)은
                서버가 그 값을 줄 때만 카드가 생긴다 — 없는 값을 "없음" 으로 꾸미지 않는다 (규칙 #8). */}
            <Card
              name={effMkt === "GATE" ? "계정 UUID" : "계정"}
              value={body?.account.user_id || "—"}
              hint={
                body
                  ? `${body.account.testnet === "true" ? "testnet · 페이크머니" : body.account.testnet === "false" ? "실계좌 · 진짜 돈" : "—"} · ${body.account.margin_mode || (body.account.error ? "계정 상세 못 읽음 (키에 Account 권한?)" : "—")}`
                  : "읽는 중…"
              }
            />
            {/* ⭐ 오늘 손익은 계정 바로 옆 (사용자 2026-09-06) — 매일 보는 숫자가 앞에, 한 번 맞추면 안 보는
                화이트리스트는 맨 뒤에. */}
            {todayCard([effMkt], total)}
            <Card
              name={`${effMkt} 잔액 (available)`}
              value={body ? `${num(body.balance.available)} USDT` : "—"}
              hint={body?.balance.broker ?? "—"}
            />
            {/* 🔴 **총 자산을 잔액 바로 옆에 둔다** (사용자 신고 2026-08-20: *"쓸 수
                있는 돈이 371.83 이라는데 이렇게 손실을 많이 봤을리가 없는데?"*).
                잃은 것이 아니라 포지션에 잡혀 있었다 — available 만 보여 주면 옮긴
                것을 잃은 것으로 읽는다. */}
            <Card
              name="계좌 총액"
              value={total === null ? "—" : `${num(total)} USDT`}
              /* 테스트 자금 안내는 **Gate 테스트넷일 때만** — 실계좌 화면에 "테스트 자금 받기" 가 뜨면
                 어느 돈을 보고 있는지 헷갈린다 (사용자 지적 2026-09-05). 총액을 거래소가 말하면 그 출처를,
                 아니면 계산식을 적는다. */
              hint={
                effMkt === "GATE" && body?.account.testnet === "true" ? (
                  <Faucet />
                ) : body?.balance.total ? (
                  (body.balance.broker ?? "—")
                ) : (
                  "available + 포지션 증거금 합"
                )
              }
            />
            {/* ⭐ **대기 주문이 잡은 증거금** (사용자 요청 2026-09-05). available 이 줄어든 이유가
                여기 있다 — 잃은 돈이 아니라 지정가가 체결될 때 쓰려고 떼어 둔 돈이다. 어댑터가
                그 값을 줄 때만(지금은 Gate). */}
            {effMkt === "GATE" || body?.balance.order_margin !== undefined ? (
              <Card
                name="주문 대기 증거금"
                value={
                  body?.balance.order_margin
                    ? `${num(body.balance.order_margin)} USDT`
                    : "—"
                }
                hint={
                  orderMargin > 0
                    ? "대기 지정가가 잡아 둔 돈 · 체결되면 포지션 증거금으로, 취소되면 잔액으로"
                    : "대기 지정가 없음"
                }
              />
            ) : null}
            <Card
              name="포지션"
              value={
                all.count
                  ? `${all.count}종목 · ${num(all.contracts, 0)} 계약`
                  : "없음"
              }
              hint={allHint("열린 포지션이 없다 — 전 종목")}
            />
            <Card
              name="잡혀 있는 증거금 (합)"
              value={all.count ? `${num(all.margin, 2)} USDT` : "—"}
              /* ⚠️ 계좌 요약은 격리 마진을 0 으로 준다 (실측: 계좌 0 / 포지션 500.85).
                 둘 다 보여 줘야 다음에 안 헷갈린다. 부분합이면 그 사실을 말한다 (규칙 #8). */
              hint={`계좌 요약 ${num(body?.balance.account_position_margin)}${all.unpriced ? ` · ${all.unpriced}건 못 읽음` : ""}`}
            />
            <Card
              name="미실현 손익 (합)"
              value={allPnlText}
              tone={all.count ? (all.pnl >= 0 ? "gain" : "loss") : undefined}
              hint={
                all.count
                  ? "계좌 총액 대비 % (오늘 손익과 같은 자) · 종목별은 아래 포지션 표"
                  : "포지션 없음"
              }
            />
            {effMkt === "GATE" || body?.account.ip_whitelist ? (
              <Card
                name="키 IP 화이트리스트"
                value={body?.account.ip_whitelist || "없음"}
                hint="401 이 나면 여기부터 본다"
              />
            ) : null}
          </>
        )}
        {/* 성과 리포트 보내기 카드는 **리포트 대시보드 한 곳**으로 모았다 (UX 점검 2026-09-05 · 세 곳에 있던 외부 전송
            입구를 하나로). 판 고르기·미리보기·확인 단계가 거기 있다. */}
      </div>

      {open ? (
        <div className="row">
          {confirming ? (
            <>
              <span className="loss">
                {size} 계약을 시장가로 전량 닫는다 — 되돌릴 수 없다
              </span>
              <button
                className="btn primary"
                disabled={busy !== ""}
                onClick={() =>
                  act("close", () => closePosition(body?.symbol, effMkt))
                }
              >
                {busy === "close" ? "닫는 중…" : "정말 닫는다"}
              </button>
              <button className="btn" onClick={() => setConfirming(false)}>
                취소
              </button>
            </>
          ) : (
            <button className="btn danger" onClick={() => setConfirming(true)}>
              포지션 전량 청산
            </button>
          )}
        </div>
      ) : null}

      {/* ⭐ **순서가 뜻이다** (사용자 요구 2026-08-20). 위는 **거래소가 말하는 사실**
          이고, 여기부터는 **우리가 정하는 것**이다 — 돈이 얼마 있는지 보고 나서 한도를
          걸고, 그 다음에 판을 띄운다. */}

      {/* 🧹 금고 카드(전역 재충전 한도)와 저금통 카드는 은퇴 (사용자 확정 2026-08-26) —
          재레버(0.8.0)와 정면 충돌하는 개념이고 라이브 펀드 경로는 금고 없이 뜬다.
          서버 API(/walkforward/vault)와 원장 필드는 동결(기본 off)로 남아 있다. */}

      {/* ⭐ **소리는 체결 이력을 먹는다** (사용자 요구 2026-08-20). 화면을 안 보고
          있을 때 매매가 났다는 것을 알려 주는 것이 목적이라, 원장이 아니라 **거래소가
          말한 체결**이 울려야 한다. */}
      {/* ⭐ 무방비 포지션은 **경보음**이다 — 위 배너와 같은 판정을 쓴다. 따로 계산하면
          귀와 눈이 다른 말을 한다. */}
      {/* 🔴 **`body ?? null` 을 그대로 넘긴다** (사용자 신고 2026-08-20). 위의
          `history` 는 화면 그리기용이라 응답 전에도 `[]` 인데, 소리에 그것을 주면
          "비어 있다" 로 심고 곧 도착하는 이력 전체가 새것이 되어 다 울린다. */}
      {/* ⭐ **순위가 도는 RUN 바로 위다** (사용자 요구 2026-08-20). 판 화면에도 같은
          표가 있었는데, 거기는 *"이 판이 어떻게 하고 있나"* 를 보는 곳이라 자리가
          아니었다 — 판을 여럿 열면 같은 표가 화면마다 반복되고 왕복도 그만큼 늘었다. */}
      {group === "coin" ? <Ranking /> : null}
      {/* ⭐ 주식은 순위 대신 **저평가 후보** (T244) — 재무 대비 싼 순. 시장은 **재무 출처가 있는** 시장 전부
          (서버 `fundamentals` 깃발 · NASDAQ · NYSE 를 카드 안 칩으로 고른다 · KRX 만 있으면 카드 대신 한 줄).
          첫 시장만 넘기던 때는 NYSE 종목이 적재돼 있어도 안 보였다 (2026-09-10). */}
      {group === "stock"
        ? (() => {
            const withFacts = allMarkets
              .filter((m) => m.fundamentals)
              .map((m) => m.name);
            return withFacts.length ? (
              <ValueRanking markets={withFacts} />
            ) : (
              <p className="faint text-xs">
                저평가 후보는 재무 출처가 있는 시장(미국주식 · EDGAR)에서만 뜬다
                — 이 묶음엔 아직 없다.
              </p>
            );
          })()
        : null}
      {/* ⭐ 주식 주문 창(T250) — 카드의 "주문" 단추가 여기로 종목을 채운다. 팝업 없음. */}
      {group === "stock" && allMarkets.length ? (
        <StockOrder markets={allMarkets} who={me.who ?? null} />
      ) : null}
      {/* ⭐ AI 차트 분석 주문(T273)은 사이드바의 제 화면(`/chart-order`)으로 옮겼다 (사용자 2026-09-11). 주식 계획은
          거기서 "이 계획으로 주문" → 콘솔의 주식 주문 창이 집어 간다(`stashStockOrder`). */}

      {/* 🔴 **판은 여기서 연다.** 판 화면을 홈으로 두면 판이 없을 때 빈 화면이 뜨고,
          여럿일 때 화면이 임의로 하나를 고른다. */}
      {/* ⭐ 잔액을 내려준다 — 이 화면은 이미 거래소에 물어 안다. RUN 의 건강
          신호에서만 읽으면 **판이 0개일 때 "모른다"** 가 뜬다 (사용자 신고). */}
      {/* 🔵 리밸런싱 펀드 (T61) — RUN 위에 뜨는 별도 물건 (능동형 인덱스). */}
      <FundPanel />

      {/* 소리 설정은 **접힌 채 펀드 아래** (UX 점검 2026-09-05) — 설정이지 관측값이 아니라 계좌 카드 위에 있을 이유가 없다.
          🔴 소리 자체는 접혀 있어도 울린다: 컴포넌트가 이력을 심는 것은 마운트 때이고, Fold 는 접히면 언마운트하므로
          여기서는 늘 마운트하고 **화면만** 접는다 (아래 hidden). */}
      <section className="fold">
        <button
          type="button"
          className="fold-head"
          onClick={toggleSound}
          aria-expanded={soundOpen}
        >
          <span className="fold-mark">{soundOpen ? "▾" : "▸"}</span>
          <span className="card-name">알림 소리</span>
          {soundOpen ? null : (
            <span className="faint">
              체결·손절·경보 소리와 야간 음소거 설정
            </span>
          )}
        </button>
      </section>
      <div hidden={!soundOpen}>
        {/* 🔴 **모든 거래소가 한 번은 답한 뒤에야** 이력을 넘긴다 (사용자 신고 2026-09-06 "새로고침하면 알림").
            `flatMap` 은 응답 전에도 `[]` 라 Sound 가 그것을 "심었다" 고 믿고, 곧 오는 첫 응답의 이력이 전부
            새것이 되어 울렸다. 응답 전은 `null` — Sound 의 약속("null 은 아직 안 왔다")을 지킨다. 키 없는
            거래소는 답이 없으므로 답한 것으로 친다. `scope` 는 모드·거래소가 바뀌면 기억을 비우게 한다. */}
        <Sound
          history={
            marketList.length > 0 &&
            marketList.every((name) => name in bodies || keyless[name])
              ? marketList.flatMap((name) => bodies[name]?.history ?? [])
              : null
          }
          scope={`${modeCookie()}:${marketKey}`}
          alarm={naked.length > 0}
        />
      </div>

      <Runs
        rows={board.rows}
        open={openRun}
        refresh={board.refresh}
        available={body?.balance.available}
      />

      {/* ⭐ 범위 칩 (사용자 요구 2026-08-26) — 아래 네 박스(포지션·미결·조건부·이력)가
          한 범위를 공유한다. 전체 = 두 거래소 병합, 줄마다 GT/BN 표식. */}
      <div className="row" style={{ marginBottom: 4 }}>
        {marketList.length > 1 && <span className="card-name">범위</span>}
        {marketList.length > 1 &&
          ["전체", ...marketList].map((name) => (
            <button
              key={name}
              type="button"
              className={`chip${boxMkt === name ? " live" : ""}`}
              onClick={() => setBoxMkt(name)}
            >
              {name}
            </button>
          ))}
      </div>

      {/* ── 체결 이력 (접을 수 있다 · 사용자 요청 2026-08-24) ─── */}
      <Fold
        name="체결 이력"
        summary={`${shown.length}건`}
        keep="console-history"
      >
        {/* 🧹 설명 문구 둘은 제거 (사용자 지적 2026-08-26: 제목에 붙어 지저분) —
          "접었다"는 사실은 "지난 판 N개…" 단추가 스스로 말한다. */}
        {/* 🔴 **어느 판의 주문인가** (T18 ⑤ · 사용자 요구 2026-08-19). 판을 여럿 돌리면
          콘솔의 이력이 한 줄기로 섞여, 어느 판이 무엇을 냈는지 알 수 없다. */}
        {runs.length > 1 ? (
          <div className="row" style={{ marginBottom: "12px" }}>
            <span className="card-name">판</span>
            {[
              "전체",
              ...runs.filter((tag) => !past.includes(tag)),
              ...(showPast ? past : []),
            ].map((tag) => (
              <button
                key={tag}
                type="button"
                className={`chip${pick === tag ? " live" : ""}`}
                onClick={() => setPick(tag)}
                // ⚠️ 지난 판임을 이름으로 말한다 — 색만으로는 왜 비어 보이는지 모른다.
                title={
                  past.includes(tag)
                    ? "지난 판 — 지금은 안 도는 판이다"
                    : undefined
                }
              >
                {past.includes(tag) ? `${tag} (지난 판)` : tag}
              </button>
            ))}
            {past.length ? (
              <button
                type="button"
                className="chip"
                onClick={() => {
                  // 접을 때 지난 판이 선택돼 있으면 전체로 돌린다 — 숨은 칩이
                  // 거르개를 쥐고 있으면 목록이 왜 비었는지 알 수 없다.
                  if (showPast && past.includes(pick)) setPick("전체");
                  setShowPast(!showPast);
                }}
                title="지금은 안 도는 판들의 이력 칩을 펼친다/접는다"
              >
                {showPast ? "지난 판 접기" : `지난 판 ${past.length}개…`}
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="table-wrap">
          <History
            rows={shown}
            plans={boxPlans}
            runs={boxRuns}
            positions={boxPositions}
          />
        </div>
      </Fold>

      {/* ── 열려 있는 포지션 (접을 수 있다) ──────────────
          🔴 **모든 종목이다** (사용자 신고 2026-08-20). 위 카드는 고른 종목(BTC) 것이라,
          판을 여섯 종목에서 돌리면 "포지션 없음" 으로 보인다 — 실제로는 SPCX 에 1408
          계약이 열려 있었다. */}
      <Fold
        name="열려 있는 포지션"
        summary={`${boxPositions.length}건`}
        keep="console-positions"
      >
        <div className="table-wrap">
          {boxPositions.length ? (
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th className="num">계약</th>
                  <th className="num">진입</th>
                  <th className="num">표시가</th>
                  <th className="num">미실현</th>
                  <th className="num">증거금</th>
                  {liqHere ? <th className="num">청산가</th> : null}
                </tr>
              </thead>
              <tbody>
                {boxPositions.map((row) => {
                  const pnl = Number(row.unrealised_pnl ?? "0");
                  return (
                    <tr key={`${row.market}:${row.symbol}`}>
                      <td className="mono">
                        <BrokerMark
                          broker={brokerOfName(
                            allMarkets,
                            String(row.market ?? ""),
                          )}
                        />{" "}
                        {row.symbol}
                      </td>
                      <td className="num">{row.size}</td>
                      <td className="num">{num(row.entry_price, 2)}</td>
                      <td className="num">{num(row.mark_price, 2)}</td>
                      <td className={pnl < 0 ? "num loss" : "num gain"}>
                        {num(row.unrealised_pnl, 2)}
                      </td>
                      <td className="num">{num(row.margin, 2)}</td>
                      {/* 🔴 청산가가 있어야 배율의 뜻이 읽힌다 — 20배면 5% 다. 배율 없는 시장(주식)은 열 자체가 없다. */}
                      {liqHere ? (
                        <td className="num loss">{num(row.liq_price, 2)}</td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="empty">열려 있는 포지션이 없다 (전 종목)</p>
          )}
        </div>
      </Fold>

      {/* ── 미결 주문 (접을 수 있다) ────────────────────── */}
      <Fold
        name="미결 주문"
        summary={`${boxOrders.length}건`}
        keep="console-orders"
      >
        <div className="table-wrap">
          {boxOrders.length ? (
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th>주문</th>
                  <th>수량</th>
                  <th className="num">지정가</th>
                  <th>멱등키</th>
                  <th>시각</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {boxOrders.map((row) => (
                  <tr key={`${row.market}:${row.id}`}>
                    <td className="mono">
                      {row.market === "BINANCE" ? (
                        <span style={{ color: "#d29922", marginRight: 4 }}>
                          BN
                        </span>
                      ) : (
                        <span className="faint" style={{ marginRight: 4 }}>
                          GT
                        </span>
                      )}
                      {row.symbol ?? "—"}
                    </td>
                    <td className="mono faint">{row.id.slice(-8)}</td>
                    <td>
                      {row.size}{" "}
                      <span className="faint">(남음 {row.left})</span>
                    </td>
                    <td className="num">{num(row.price, 1)}</td>
                    <td className="mono faint">{row.text || "—"}</td>
                    <td className="faint">{whenSec(row.create_time)}</td>
                    <td>
                      <button
                        className="btn small"
                        disabled={busy !== ""}
                        onClick={() =>
                          act(row.id, () =>
                            cancelOrder(row.id, row.symbol, row.market),
                          )
                        }
                      >
                        {busy === row.id ? "…" : "취소"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">미결 주문이 없다</p>
          )}
        </div>
      </Fold>

      {/* ── 조건부 주문 (접을 수 있다) ──────────────────── */}
      <Fold
        name="조건부 주문 (손절)"
        summary={`${boxStops.length}건`}
        keep="console-stops"
      >
        <div className="table-wrap">
          {boxStops.length ? (
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th>주문</th>
                  <th className="num">발동가</th>
                  <th>만료</th>
                  <th>시각</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {boxStops.map((row) => (
                  <tr key={`${row.market}:${row.id}`}>
                    {/* 🔴 **어느 계약을 지키는가.** 이것이 없어서 BTC 만 보고 "조건부
                      0건" = 무방비로 읽었다 — 화면이 낼 수 있는 가장 나쁜 거짓말이다. */}
                    <td className="mono">
                      {row.market === "BINANCE" ? (
                        <span style={{ color: "#d29922", marginRight: 4 }}>
                          BN
                        </span>
                      ) : (
                        <span className="faint" style={{ marginRight: 4 }}>
                          GT
                        </span>
                      )}
                      {row.symbol ?? "—"}
                    </td>
                    <td className="mono faint">{row.id.slice(-8)}</td>
                    <td className="num">{num(row.trigger_price, 2)}</td>
                    {/* 🔴 만료를 띄운다 — 조건부는 조용히 사라지고, 걸었다는 기억은
                      만료를 모른다. */}
                    <td className="loss">{expiry(row.expiration)}</td>
                    <td className="faint">{whenSec(row.create_time)}</td>
                    <td>
                      <button
                        className="btn small"
                        disabled={busy !== ""}
                        onClick={() =>
                          act(row.id, () =>
                            cancelStop(row.id, row.symbol, row.market),
                          )
                        }
                        title={
                          open
                            ? "포지션을 들고 있다 — 거두면 손절 없는 상태가 된다"
                            : "조건부 주문을 거둔다"
                        }
                      >
                        {busy === row.id ? "…" : "취소"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">조건부 주문이 없다</p>
          )}
        </div>
      </Fold>
    </div>
  );
}
