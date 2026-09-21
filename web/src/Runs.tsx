/**
 * 판 목록과 RUN 띄우기 — **콘솔의 일이다** (사용자 확정 2026-08-19).
 *
 * 🔴 **볼 수 있는 판만 낸다.** 예전 목록은 저널만 남은 죽은 RUN까지 섞어서, 여섯 줄 중
 * 넷이 눌러도 아무것도 안 나오는 항목이었다 — 사용자 지적: *"지금 실제로 볼 수 있는
 * 판만 출력되게 해줘야지;;;; 이러면 헷갈리잖아."*
 *
 * ⚠️ **숨기는 것이 아니라 갈라 놓는다.** 죽은 RUN도 아래에 접어서 남긴다 — 목록에서
 * 통째로 사라지면 *"내가 띄웠던 판이 어디 갔나"* 에 답할 수 없다 (절대 규칙 #8).
 */

import { Fragment, useEffect, useMemo, useState } from "react";
import { exchangeMarkets, type MarketInfo } from "./api";
import { orderAlive } from "./runsOrder";
import { useMe } from "./Gate";
import {
  bookInGroup,
  capsOfName,
  groupOfName,
  marketTradeAllowed,
  useMarketGroup,
} from "./shell/marketGroup";
import {
  dropSession,
  health as fetchHealth,
  playbooks,
  setAuto,
  startLive,
  symbols,
  type Book,
  type Choice,
  type Health,
  type Summary,
} from "./api";
import {
  ErrorCard,
  Fact,
  Fold,
  SelectField,
  aliveness,
  frameSeconds,
  num,
  pct,
  when,
} from "./ui";

/** 고를 수 있는 종목 — Gate 무기한만. 업비트는 조회 전용(키 없음)이라 주문이 없다. */
// 🔴 **목록을 여기 박지 않는다** (사용자 신고 2026-08-20: *"왜 종목을 추가했는데
//    여기에 안 뜨지? 설마 하드코딩으로 관리하는 건가"*). 맞았다 — `costs.yml` 과
//    `TRACKED` 에 넣었는데 고르개는 옛 다섯 개 그대로였다.
//
// ⇒ 서버(`/exchange/symbols`)가 단일 출처다. 종목을 늘리면 화면은 저절로 따라온다.

type Props = {
  rows: Summary[];
  /** 판을 연다 — 주소를 바꾸는 것은 셸의 일이다. */
  open: (run: string, name?: string) => void;
  /** 거래소가 말하는 `available` — 콘솔이 이미 안다 (RUN 이 0개여도). */
  available?: string;
  /** 목록을 다시 당긴다 (동작 직후). */
  refresh: () => void;
};

/**
 * 한 RUN 이 **제대로 도는가** — 칩 하나로 답한다.
 *
 * 🔴 세 상태를 가른다: 모른다 / 이상 있다 / 돈다. `—` 를 정상으로 보이게 두면 이
 * 프로젝트가 하루에 네 번 한 실수를 반복한다 (못 읽은 것이 "0" 으로 보였다).
 */
function state(beat: Health | null | undefined, startedAt: string) {
  if (beat === undefined) return <span className="chip">확인 중</span>;
  if (beat === null) {
    return (
      <span
        className="chip loss"
        title="건강 조회가 안 온다 — 러너가 사라졌을 수 있다"
      >
        모른다
      </span>
    );
  }
  const bad = (beat.findings ?? []).filter((item) => item.level === "error");
  if (bad.length) {
    return (
      <span
        className="chip loss"
        title={bad.map((item) => item.detail).join(" · ")}
      >
        이상 {bad.length}건
      </span>
    );
  }
  const interval = frameSeconds(beat.entry ?? "15m");
  const age = beat.frame_ages?.[beat.entry ?? "15m"];
  const untilNext = beat.next_judge_at
    ? (new Date(beat.next_judge_at).getTime() - Date.now()) / 1000
    : undefined;
  // ⚠️ 카드와 **같은 판단**을 쓴다 — 따로 계산하면 목록과 상세가 다른 말을 한다.
  const look = aliveness(beat.steps, beat.running, age, interval, untilNext);
  void startedAt;
  return (
    <span
      className={`chip ${look.tone === "loss" ? "loss" : "gain"}`}
      title={look.why}
    >
      {look.label}
    </span>
  );
}

/**
 * 지갑을 **누가 들고 있나** — 선착순의 현재 상태.
 *
 * 🔴 **선착순은 규칙이지 버그가 아니다.** 다만 말하지 않으면 뒤의 RUN 이 주문을 못 내는
 * 것을 전략 문제로 오해한다 (§1-0s 관측 규약).
 *
 * ⚠️ **격리 마진에는 '증거금 지갑' 이 없다.** RUN 을 띄울 때 하는 것은 검증뿐이고,
 * 실제 증거금은 **체결되는 순간** 거래소가 available 에서 뗀다 — 그래서 *"예산"* 과
 * *"실제로 잡힌 돈"* 은 다른 값이고, 잡은 RUN 만 우선권을 갖는다.
 */
function Purse({
  rows,
  beats,
  available,
}: {
  rows: Summary[];
  beats: Record<string, Health | null>;
  /**
   * 콘솔이 거래소에 직접 물어 아는 `available` (T21).
   *
   * 🔴 **RUN 이 0개여도 잔액은 있다.** 예전에는 RUN 의 건강 신호에서만 읽어서, 판이
   * 하나도 없으면 *"모른다"* 가 떴다 — 정작 같은 화면 위 카드에는 905.44 가 찍혀
   * 있는데도. 판을 띄우기 **전에** 보는 값인데 판이 있어야 보이는 모순이었다.
   */
  available?: string;
}) {
  // ⭐ **지갑은 거래소마다 하나다** (사용자 지적 2026-08-26). 예전 "계정은 하나뿐"
  //   가정은 바이낸스 판이 생기면서 낡았다 — 아무 RUN 에서나 읽으면 GT 판의 잔액이
  //   BN 예산 옆에 붙는다. 시장별로 나눠 각자의 available·예산·선착순을 본다.
  const wallets = [...new Set(rows.map((row) => row.market || "GATE"))]
    .sort()
    .map((mkt) => {
      const mine = rows.filter((row) => (row.market || "GATE") === mkt);
      const free =
        mine
          .map((row) => beats[row.session_id]?.exchange?.available)
          .find((value) => value !== undefined && value !== "") ??
        // ⭐ 콘솔이 직접 아는 잔액은 Gate 콘솔 것이다 — 판이 0개일 때의 폴백.
        (mkt === "GATE" ? available : undefined);
      const holders = mine
        .map((row) => ({
          run: row.session_id,
          symbol: row.symbol,
          locked: Number(beats[row.session_id]?.exchange?.position_margin ?? 0),
        }))
        .filter((item) => item.locked > 0)
        .sort((one, two) => two.locked - one.locked);
      const budget = mine.reduce(
        (sum, row) => sum + (row.margin_budget ?? 0),
        0,
      );
      const spare = free === undefined || free === "" ? null : Number(free);
      return { mkt, spare, budget, holders };
    });

  return (
    <div className="card wide">
      {wallets.map(({ mkt, spare, budget, holders }) => (
        <div key={mkt} style={{ marginTop: 8 }}>
          <div className="run-facts">
            <Fact
              name={`${mkt} 쓸 수 있는 돈`}
              value={spare === null ? "모른다" : num(spare, 2)}
              note="거래소 available · 이미 잡힌 증거금은 빠져 있다"
              bad={spare === null}
            />
            <Fact
              name="예산 합계"
              value={num(budget, 2)}
              note="이 거래소 RUN 들이 선언한 증거금의 합 — 지갑을 넘을 수 있다"
              bad={
                spare !== null &&
                budget > spare + holders.reduce((s, h) => s + h.locked, 0)
              }
            />
            <Fact
              name="지금 잡은 RUN"
              value={holders.length ? `${holders.length}개` : "없다"}
              note={
                holders.length
                  ? "포지션이 열려 있는 동안만 증거금이 존재한다"
                  : "아무도 안 잡았다 — 먼저 진입하는 RUN 이 먹는다"
              }
            />
          </div>
          {holders.length ? (
            <ul className="purse">
              {holders.map((item, index) => (
                <li key={item.run}>
                  <span className="chip live">{index + 1}순위</span>{" "}
                  <span className="mono">{item.symbol}</span>{" "}
                  <span className="mono faint">{item.run}</span> · 잡은 돈{" "}
                  <b>{num(item.locked, 2)}</b>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
      <span className="card-hint">
        먼저 진입한 RUN 이 증거금을 먹는다 — 뒤의 RUN 은 예산이 있어도 못
        들어간다.
        <b> 버그가 아니라 규칙이다.</b> 예산을 늘리려면 다른 RUN 을 닫거나
        거래소에 돈을 더 넣는다.
      </span>
    </div>
  );
}

export function Runs({ rows, open, refresh, available }: Props) {
  const [books, setBooks] = useState<Book[]>([]);
  const [picks, setPicks] = useState<Choice[]>([]);
  // ⭐ 기본 선택은 /playbooks 의 recommended 가 정한다 (T63 ②) — 리터럴은 반드시 낡는다.
  const [book, setBook] = useState("");
  const [market, setMarket] = useState("GATE");
  // ⭐ 거래소 목록은 서버 파생 (T63 §2c) — 새 거래소는 어댑터가 계약을 지키면 자동으로 뜬다.
  const [markets, setMarkets] = useState<string[]>([]);
  // ⭐ T245 — 새 판의 시장 후보는 고른 묶음(코인/주식)의 것만. 묶음은 서버가 말한다.
  const [group] = useMarketGroup();
  const [infos, setInfos] = useState<MarketInfo[]>([]);
  useEffect(() => {
    exchangeMarkets()
      .then((r) => setInfos(r.all ?? []))
      .catch(() => setInfos([]));
  }, []);
  const groupMarkets = useMemo(
    () => markets.filter((m) => groupOfName(infos, m) === group),
    [markets, infos, group],
  );
  useEffect(() => {
    if (groupMarkets.length && !groupMarkets.includes(market)) setMarket(groupMarkets[0] ?? market);
  }, [groupMarkets, market]);
  // ⭐ T242 — 이 묶음에서 거래할 권한. 없으면 시작 칸 위에 🔒 를 띄운다(서버가 403 으로 다시 막는다).
  const me = useMe();
  const mayTradeHere = marketTradeAllowed(me.who, group);
  // ⭐ 매매법 후보도 묶음 안의 것만 — 주식 묶음에서 코인 세트를 고르면 서버가 거절할 뿐이다.
  const groupBooks = useMemo(() => books.filter((b) => bookInGroup(b, group)), [books, group]);
  useEffect(() => {
    if (groupBooks.length && !groupBooks.some((b) => b.id === book)) {
      const pick = groupBooks.find((b) => b.recommended) ?? groupBooks[0];
      if (pick) setBook(pick.id);
    }
  }, [groupBooks, book]);
  // ⭐ 판 목록도 묶음으로 — 옛 판(market 없음)은 GATE 로 본다.
  const shown = useMemo(
    () => rows.filter((row) => groupOfName(infos, row.market || "GATE") === group),
    [rows, infos, group],
  );
  const showLeverage = shown.some((row) => capsOfName(infos, row.market || "GATE").leverage);
  const [symbol, setSymbol] = useState("");
  const [margin, setMargin] = useState("300");
  const [leverage, setLeverage] = useState("3");
  // 🧹 금고(실현 비율·수익선·한계)는 은퇴했다 (사용자 확정 2026-08-26) — 재레버(0.8.0)가
  //    "이익이 일하게 한다"를 측정으로 채택하면서 수익을 떼는 장치와 정면 충돌하고,
  //    라이브 펀드 경로는 원래부터 금고 없이 뜬다. 서버 필드는 동결(기본 off)로 남는다.
  // ⭐ T22 브레이커(MDD 제한) — 고점 대비 낙폭. 기본은 **안 걺** (사용자 확정 2026-08-26):
  //    전략 자체가 MDD 를 재서 채택되므로(0.8.1 백테스트 MDD 47%), 이건 서브 옵션이다.
  const [drawdownStop, setDrawdownStop] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  // 🔴 삭제는 포지션까지 시장가로 닫는다 — 되돌릴 수 없으므로 어느 판을 지우려는지
  //    들고 한 번 더 묻는다.
  const [killing, setKilling] = useState("");
  // ⭐ 행 액션 통합 (사용자 요구 2026-08-26) — 배율/중지/삭제는 편집 확장행으로.
  const [editRow, setEditRow] = useState("");
  const [popupNote, setPopupNote] = useState("");

  useEffect(() => {
    playbooks()
      .then((body) => {
        setBooks(body.playbooks);
        setMarkets(body.live_markets ?? []);
        const pick =
          body.playbooks.find((b) => b.recommended) ?? body.playbooks[0];
        if (pick) setBook((prev) => prev || pick.id);
      })
      .catch(() => undefined);
    // 🔴 종목 목록도 서버가 준다 — 화면에 박아 두면 추가해도 안 뜬다.
    symbols()
      .then((body) => {
        setPicks(body.rows);
        // ⭐ 첫 항목을 고른 상태로 시작한다. 안 하면 빈 값으로 띄우기를 누르게 된다.
        const first = body.rows[0];
        if (first) setSymbol((now) => now || first.symbol);
      })
      .catch(() => undefined);
  }, []);

  // 🔴 **배율 기본값은 매매법이 정한다** (2026-08-30). 리터럴 "3" 이 박혀 있어서
  //    6x 로 측정한 1.3.0 을 골라도 3 이 떴다 — 문서와 화면이 다른 값을 말하면
  //    사람이 손으로 고치다 틀린다. 선언이 없는 매매법은 지금 값을 그대로 둔다.
  useEffect(() => {
    const found = books.find((b) => b.id === book);
    if (found?.leverage != null) setLeverage(String(found.leverage));
  }, [book, books]);

  // 🔴 **도는 RUN만 열 수 있다.** `stored` 는 저널만 남은 것이고 러너가 없어서, 눌러도
  //    상태·건강이 안 온다 — 목록에 두면 눌러 보고 나서야 안다.
  // 🔴 **"돈다" 는 루프가 안 죽었다는 뜻일 뿐이다** (사용자 요구 2026-08-19: *"제대로
  //    돌고있는지 체크해주는 상태도 추가해줘"*). 웹소켓이 조용히 끊겨도 태스크는
  //    멀쩡히 대기하고, 실제로 그런 사고가 있었다 — 예외도 로그도 없이 40분이었다.
  //
  // ⇒ 건강을 따로 물어 **판정이 흐르는가**로 답한다. 이 목록은 열기 전에 보는 곳이므로
  //    "눌러 봐야 아는" 상태가 없어야 한다.
  const [beats, setBeats] = useState<Record<string, Health | null>>({});

  useEffect(() => {
    let alive = true;
    const pull = () => {
      const running = rows.filter((row) => row.running && !row.stored);
      Promise.allSettled(
        running.map((row) => fetchHealth(row.session_id)),
      ).then((found) => {
        if (!alive) return;
        const next: Record<string, Health | null> = {};
        running.forEach((row, index) => {
          const one = found[index];
          // ⚠️ 404 는 정상이다 — 러너가 막 사라진 순간일 수 있다. 그때는 null 이고,
          //    화면은 "모른다" 라고 말한다 (0 이나 정상으로 치지 않는다).
          next[row.session_id] = one?.status === "fulfilled" ? one.value : null;
        });
        setBeats(next);
      });
    };
    pull();
    const timer = setInterval(pull, 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // ⚠️ id 목록이 바뀔 때만 다시 건다 — rows 는 매 폴링마다 새 배열이라 그대로 쓰면
    //    타이머가 끝없이 다시 걸린다.
  }, [rows.map((row) => row.session_id).join(",")]);

  // ⭐ 포지션이 잡힌 판부터 · 그 안에서 이득 높은 순 (규칙과 근거는 `runsOrder.ts` 에 있다).
  const alive = orderAlive(
    shown.filter((row) => row.running && !row.stored),
    beats,
  );
  const dead = shown.filter((row) => !(row.running && !row.stored));

  const act = (name: string, run: () => Promise<unknown>) => {
    setBusy(name);
    setError("");
    run()
      .then(() => refresh())
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(""));
  };

  return (
    <>
      {/* 🔴 **지갑은 선착순이다** (다중 RUN ㄷ · 사용자 요구 2026-08-19: *"지갑 선착
          우선순위를 누가 가지고 있는지 표시해줬으면"*).

          먼저 진입한 RUN 이 증거금을 먹고 뒤의 RUN 은 예산이 있어도 못 들어간다. 그것을
          버그가 아니라 **규칙으로** 받아들이기로 했으므로, 화면이 *"지금 누가 들고
          있는가"* 를 말해야 한다 — 안 말하면 사람은 주문이 작아진 것을 전략 문제로
          오해한다. */}
      {/* ⭐ 접을 수 있다 (사용자 요구 2026-08-26) — 상세는 필요할 때만 편다. */}
      <Fold
        name="지갑 — 거래소마다 하나 · 선착순이다"
        summary="펼치면 거래소별 잔액·예산·선착순"
        keep="runs-purse"
        initialShut
      >
        <Purse rows={alive} beats={beats} available={available} />
      </Fold>

      <h2 className="section-title">도는 RUN {alive.length}개</h2>
      {error ? <ErrorCard message={error} /> : null}
      {popupNote ? <p className="notice bad">{popupNote}</p> : null}
      <div className="table-wrap">
        {alive.length ? (
          <table>
            <thead>
              <tr>
                <th>종목</th>
                <th>매매법</th>
                <th>상태</th>
                <th className="num">증거금</th>
                {showLeverage ? <th>배율</th> : null}
                {/* 🔴 **1.2.0 인지 1.3.0 인지 가르는 유일한 열** (2026-08-30).
                    두 버전의 번들 구성원이 같아서 매매법 이름으로는 구별이 안 된다.
                    β = 손절을 청산거리의 몇 % 안쪽으로 당기나 · 하한 = 그보다
                    가까운 손절이면 그 자리는 안 간다. 옛 판은 여기가 비어 있다. */}
                <th>안전장치</th>
                <th className="num">손익</th>
                {/* ⭐ 미실현도 같이 (사용자 지적 2026-08-26) — 펀드 패널은 보여 주는데
                    RUN 목록만 실현 손익뿐이라 보유 중 판이 전부 0% 로 보였다. */}
                <th className="num">미실현</th>
                {/* 🔴 **낙폭을 목록에 올린다** (사용자 2026-08-30: *"라이브에서 MDD 는
                    어디서 출력돼?"*). RUN 상세에만 있으면 판마다 열어 봐야 하는데,
                    백테스트를 MDD 로 판정해 놓고 라이브에서는 한눈에 못 보면
                    그 둘을 비교할 수가 없다. 값은 원장이 T22 부터 재고 있었다. */}
                <th className="num">낙폭 (최대)</th>
                <th className="num">매매</th>
                {/* ⭐ **열기 단추 바로 위다** (사용자 요구 2026-08-19). 제목 옆에 두면
                    무엇을 여는 단추인지 한 칸 떨어져 보인다 — 같은 열에 있어야 아래
                    단추들의 "전부" 라는 것이 눈에 그대로 든다. */}
                <th className="head-act">
                  {/* 🧹 "모두 열기"(내부 탭 전부)는 삭제 (사용자 확정 2026-09-03) —
                      새 탭 모두 열기만 남는다. */}
                  {alive.length > 1 ? (
                    <button
                      type="button"
                      className="btn small"
                      style={{ marginLeft: 4 }}
                      onClick={() => {
                        // ⭐ 브라우저 새 탭으로 전부 (사용자 요구 2026-08-26). 크롬
                        //   팝업 차단은 한 클릭에 1개만 허용한다 — 막힌 개수를 세서
                        //   화면이 직접 말하고, 허용 뒤 재클릭은 이름 있는 타깃이라
                        //   중복 탭 없이 전부 열린다.
                        let opened = 0;
                        for (const row of alive) {
                          const tab = window.open(
                            `/paper/${row.session_id}`,
                            `run-${row.session_id}`,
                          );
                          if (tab) opened += 1;
                        }
                        setPopupNote(
                          opened < alive.length
                            ? `팝업 차단으로 ${alive.length - opened}개가 안 열렸다 — ` +
                                '주소창 오른쪽 팝업 아이콘에서 "항상 허용"을 누르고 다시 누르면 전부 열린다'
                            : "",
                        );
                      }}
                      title="크롬 새 탭으로 전부 연다 — 팝업이 막히면 주소창에서 이 사이트의 팝업을 허용한다"
                    >
                      새 탭 모두 열기
                    </button>
                  ) : null}
                </th>
              </tr>
            </thead>
            <tbody>
              {alive.map((row) => (
                <Fragment key={row.session_id}>
                  <tr>
                    {/* RUN id 열 제거 (사용자 확정 2026-08-26) — 죽은 판 표에는 남긴다 */}
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
                      {row.symbol}
                    </td>
                    <td>{row.playbook}</td>
                    {/* 🔴 **눌러 봐야 아는 상태가 없어야 한다.** "돈다" 는 루프가 안
                      죽었다는 뜻일 뿐이고, 웹소켓이 조용히 끊겨도 태스크는 대기한다. */}
                    <td>{state(beats[row.session_id], row.started_at)}</td>
                    <td className="num">{num(row.margin_budget ?? null, 2)}</td>
                    {/* 🧹 금고 열은 은퇴와 함께 제거 (사용자 지적 2026-08-26) —
                      값이 생길 경로 자체가 닫혔다. */}
                    {/* ⭐ 값만 보여 준다 — 바꾸는 위젯은 편집 확장행에 (사용자 요구
                      2026-08-26: 행 액션 통합). */}
                    {showLeverage ? <td className="num">{row.leverage}x</td> : null}
                    {/* 🔴 안전장치 — 값이 없으면 **옛 설정으로 도는 판**이다 (1.2.0 이하). */}
                    <td>
                      {row.stop_cap_ratio == null &&
                      row.stop_min_pct == null ? (
                        <span className="faint">— 옛 설정</span>
                      ) : (
                        <span>
                          β{num(row.stop_cap_ratio ?? null, 2)}
                          <span className="faint">
                            {" "}
                            · 하한 {((row.stop_min_pct ?? 0) * 100).toFixed(2)}%
                          </span>
                        </span>
                      )}
                    </td>
                    <td
                      className={`num ${
                        row.return_pct === 0
                          ? ""
                          : row.return_pct > 0
                            ? "gain"
                            : "loss"
                      }`}
                    >
                      {/* ⭐ 손익은 금액+% 병기 (사용자 확정 2026-08-26: "이건 기본이야").
                        절대액 = seed x return_pct — 손익률의 기준이 seed 라 정확하다. */}
                      {(() => {
                        const amount = (row.seed_cash * row.return_pct) / 100;
                        return `${amount > 0 ? "+" : ""}${num(amount, 2)} (${pct(row.return_pct)})`;
                      })()}
                    </td>
                    <td className="num">
                      {(() => {
                        const raw =
                          beats[row.session_id]?.exchange?.position?.[
                            "unrealised_pnl"
                          ];
                        if (raw === undefined || raw === "")
                          return <span className="faint">—</span>;
                        const value = Number(raw);
                        const base = row.margin_budget ?? 0;
                        const tone =
                          value > 0 ? "gain" : value < 0 ? "loss" : "";
                        return (
                          <span className={tone}>
                            {value > 0 ? "+" : ""}
                            {num(value, 2)}
                            {base > 0
                              ? ` (${value >= 0 ? "+" : ""}${((value / base) * 100).toFixed(1)}%)`
                              : ""}
                          </span>
                        );
                      })()}
                    </td>
                    {/* 🔴 낙폭 — 지금 / (최대). 기준은 **이 판이 번 돈**(증거금+누적손익)
                      이다. 지갑을 더하면 판 수만큼 희석돼 백테스트 낙폭과 비교가
                      안 된다 (ledger.py T22). 브레이커가 걸렸으면 빨갛게. */}
                    <td className="num">
                      {(() => {
                        const now = row.drawdown_pct;
                        const worst = row.max_drawdown_pct;
                        if (now === undefined && worst === undefined)
                          return <span className="faint">—</span>;
                        const tone = row.tripped_at
                          ? "loss"
                          : (now ?? 0) > 0
                            ? "loss"
                            : "";
                        return (
                          <span className={tone}>
                            {num(now ?? null, 1)}%
                            <span className="faint">
                              {" "}
                              ({num(worst ?? null, 1)}%)
                            </span>
                          </span>
                        );
                      })()}
                    </td>
                    <td className="num">
                      {row.closed}/{row.trades}
                    </td>
                    {/* ⚠️ 머리칸과 **같은 정렬**이어야 한다 — 어긋나면 "모두 열기" 가
                      아래 단추들과 다른 열처럼 보인다 (사용자 지적 2026-08-19). */}
                    <td className="act">
                      {/* ⭐ 행 액션 통합 (사용자 요구 2026-08-26) — 배율/중지/삭제는
                        편집 확장행으로. 파괴적 단추가 목록에서 사라져 실수 자리도
                        같이 사라진다. */}
                      <button
                        className="btn small"
                        onClick={() => {
                          setKilling("");
                          setEditRow(
                            editRow === row.session_id ? "" : row.session_id,
                          );
                        }}
                      >
                        {editRow === row.session_id ? "닫기" : "편집"}
                      </button>
                      {/* ⭐ `<a href>` 다 — 가운데 클릭으로 **새 창에 띄운다**. 그것이
                        판을 나란히 보는 방법이고, 사용자가 요구한 것이다. */}
                      <a
                        className="btn small primary"
                        href={`/paper/${row.session_id}`}
                        onClick={(event) => {
                          if (event.metaKey || event.ctrlKey || event.shiftKey)
                            return;
                          event.preventDefault();
                          // ⭐ 종목명을 함께 넘긴다 — 셸이 목록을 폴링하면 화면이
                          //    다시 그려져 차트가 깜빡인다.
                          open(row.session_id, row.symbol);
                        }}
                      >
                        열기
                      </a>
                    </td>
                  </tr>
                  {editRow === row.session_id ? (
                    <tr>
                      {/* ⚠️ 열 수는 **11** 이다 (9 였다). 짧으면 펼친 편집행이 표
                        오른쪽 끝까지 못 닿고, 고정된 액션 열 자리에 구멍이 보인다. */}
                      <td
                        colSpan={11}
                        style={{ background: "rgba(110,168,254,0.05)" }}
                      >
                        <div
                          className="row"
                          style={{ alignItems: "center", gap: 12 }}
                        >
                          {/* 🔴 배율 다이얼은 뺐다 (T219 결정 2026-09-05) — 펀드가 배율을 정한다. 값만 보인다. */}
                          {capsOfName(infos, row.market || "GATE").leverage ? (
                            <span className="chip" title="배율은 펀드(플레이북 선언)가 정한다">
                              배율 {Number(row.leverage)}x
                            </span>
                          ) : null}
                          {/* ⚠️ 중지는 일시정지가 아니다 — 새 진입만 막고, 든 포지션의
                            손절·반익은 계속 관리된다. */}
                          <button
                            className="btn small"
                            disabled={busy !== ""}
                            title={
                              row.auto === false
                                ? "새 진입을 다시 받는다"
                                : "새 진입만 멈춘다 — 든 포지션은 계속 관리된다"
                            }
                            onClick={() =>
                              act(`auto-${row.session_id}`, () =>
                                setAuto(row.session_id, row.auto === false),
                              )
                            }
                          >
                            {busy === `auto-${row.session_id}`
                              ? "…"
                              : row.auto === false
                                ? "재개"
                                : "중지"}
                          </button>
                          {/* 🔴 삭제는 포지션까지 닫는다 — 되돌릴 수 없어 한 번 더 묻는다. */}
                          {killing === row.session_id ? (
                            <>
                              <button
                                className="btn small danger"
                                disabled={busy !== ""}
                                title="포지션을 시장가로 닫고 판을 지운다"
                                onClick={() =>
                                  act(`drop-${row.session_id}`, () =>
                                    dropSession(row.session_id).finally(() => {
                                      setKilling("");
                                      setEditRow("");
                                    }),
                                  )
                                }
                              >
                                {busy === `drop-${row.session_id}`
                                  ? "…"
                                  : "정말 삭제"}
                              </button>
                              <button
                                className="btn small"
                                disabled={busy !== ""}
                                onClick={() => setKilling("")}
                              >
                                취소
                              </button>
                            </>
                          ) : (
                            <button
                              className="btn small danger"
                              disabled={busy !== ""}
                              onClick={() => setKilling(row.session_id)}
                            >
                              삭제
                            </button>
                          )}
                          <span className="card-hint">
                            뜬 시각 {when(row.started_at)}
                          </span>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">도는 RUN이 없다 — 아래에서 띄운다</p>
        )}
      </div>

      <Fold
        name="RUN 띄우기"
        summary={`${alive.length}개 돌고 있다`}
        keep="console-start"
        initialShut={alive.length > 0}
      >
        {mayTradeHere ? null : (
          <p className="notice" role="status">
            🔒 이 시장에서 거래할 권한이 없다 — 관리자가 시장 권한(거래)을 주면 판을 띄울 수 있다.
          </p>
        )}
        <div className="card wide">
          <div className="row">
            <label className="field">
              매매법
              <select value={book} onChange={(e) => setBook(e.target.value)}>
                {/* ⭐ 세트 (T32 ⑥ · 사용자 설계): 한 판에 플레이북 여럿 — 횡보는 박스,
                    상승은 눌림목/돌파. 국면 게이트가 봉마다 고른다. 값의 + 는 서버가
                    가른다. Gate 무기한은 종목당 포지션 하나라 판 둘로는 못 나눈다. */}
                {/* ⭐ 최신 전략만 보인다 (사용자 확정 2026-08-23). 옛 세트·실험 플레이북은
                    playbooks.yml 의 listed 로 숨겼다 — 백테스트는 id 로 여전히 부른다.
                    박스가 첫 자리여야 한다 — 집행 플래그(지정가 다리)는 첫 플레이북 것을 쓴다. */}
                {/* 🔵 권장 세트는 이제 playbooks.yml 의 recommended 가 정한다 (2026-08-24) —
                    하드코딩하던 0.2 문자열을 설정으로 옮겼다. 새 메인을 확정하면 yaml 한 줄이면 바뀐다. */}
                <optgroup label="라이브 세트 (3배 권장)">
                  {groupBooks
                    .filter((item) => item.recommended)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label} · 방아쇠 {item.trigger ?? item.timeframe}
                      </option>
                    ))}
                </optgroup>
                {groupBooks.some((item) => !item.recommended) && (
                  <optgroup label="구성 요소 단독">
                    {groupBooks
                      .filter((item) => !item.recommended)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label} · 방아쇠 {item.trigger ?? item.timeframe}
                        </option>
                      ))}
                  </optgroup>
                )}
                {/* 🧹 옛 0.2 조합 세트는 2026-08-25 대청소로 archive/ 이관 — 선택지에서 제거 */}
              </select>
            </label>
            <SelectField
              label="거래소"
              value={market}
              onChange={setMarket}
              options={groupMarkets.map((m) => ({ value: m }))}
            />
            <label className="field">
              종목
              <select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              >
                {picks.map((item) => (
                  <option key={item.symbol} value={item.symbol}>
                    {/* ⚠️ 못 띄우는 것도 남긴다 — 빼면 "왜 없지" 에 답을 못 한다. */}
                    {item.tradable ? item.label : `${item.label} (눈금 없음)`}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              포지션 증거금
              <input
                value={margin}
                onChange={(e) => setMargin(e.target.value)}
              />
            </label>
            <label className="field">
              레버리지
              <input
                value={leverage}
                onChange={(e) => setLeverage(e.target.value)}
              />
            </label>
            <label className="field">
              브레이커 (MDD 제한 · % · 비우면 안 걺)
              <input
                placeholder="예: 31%"
                value={drawdownStop}
                onChange={(e) => setDrawdownStop(e.target.value)}
              />
            </label>
            <button
              className="btn primary"
              disabled={busy !== ""}
              onClick={() =>
                act("start", () =>
                  startLive({
                    playbook: book,
                    symbol,
                    market,
                    margin,
                    leverage: Number(leverage) || 3,
                    // ⚠️ 빈 문자열 그대로 보낸다 — 서버가 "안 걸었다" 로 읽는다.
                    //    (금고 필드들은 안 보낸다 = 서버 기본 off — 은퇴 2026-08-26)
                    drawdown_stop: drawdownStop.trim(),
                  }),
                )
              }
            >
              {busy === "start" ? "띄우는 중…" : "RUN 띄우기"}
            </button>
          </div>
          <table className="mini">
            <thead>
              <tr>
                <th>칸</th>
                <th>뜻</th>
                <th>단위</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>포지션 증거금</td>
                <td>이 판이 굴리는 돈 — 예산이자 사이징 기준</td>
                <td>USDT</td>
              </tr>
              <tr>
                <td>브레이커 (MDD 제한)</td>
                <td>
                  고점 대비 낙폭이 이 %에 닿으면 새 진입을 멈추고 사람을 부른다.
                  전략이 이미 MDD 를 재서 채택되므로 기본은 안 걺
                </td>
                <td>
                  <b>%</b>
                </td>
              </tr>
            </tbody>
          </table>

          <span className="card-hint">
            🔴 <b>한 종목에 한 RUN</b> — Gate 는 종목당 포지션이 하나라, 둘이
            같은 종목을 돌면 포지션이 합쳐져 <b>양쪽 성적이 다 틀린다</b>.
            서버가 409 로 막는다.
            <br />
            ⚠️ <b>지갑은 선착순</b> — 먼저 진입한 RUN 이 증거금을 먹는다. 판들의
            예산 합이 계좌 총액을 넘으면 시작을 거부한다.
            <br />
            ⚠️ <b>목록에 있다고 다 띄울 수 있는 것은 아니다</b> — 호가 눈금이
            선언된 종목만 된다 (아래 종목 순위의 `판` 열).
          </span>
        </div>
      </Fold>

      {/* ⚠️ **숨기지 않고 갈라 놓는다.** 통째로 사라지면 "내가 띄웠던 판이 어디 갔나" 에
          답할 수 없다 — 다만 눌러도 아무것도 안 나오므로 위 목록에는 안 둔다. */}
      {dead.length ? (
        <Fold
          name="죽은 RUN"
          summary={`${dead.length}개 — 러너가 없다`}
          keep="console-dead"
          initialShut
        >
          <p className="card-hint">
            저널·DB 에만 남아 있고 <b>러너가 없다</b> — 눌러도 상태·건강이 안
            온다. 거래소에 포지션이 남아 있을 수 있으므로 지울 때 함께 닫힌다.
          </p>
          <div className="table-wrap">
            <table>
              <tbody>
                {dead.map((row) => (
                  <tr key={row.session_id}>
                    <td className="mono faint">{row.session_id}</td>
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
                      {row.symbol}
                    </td>
                    <td className="faint">{row.playbook}</td>
                    <td className="faint">{when(row.started_at)}</td>
                    <td>
                      <button
                        className="btn small danger"
                        disabled={busy !== ""}
                        onClick={() =>
                          act(`drop-${row.session_id}`, () =>
                            dropSession(row.session_id),
                          )
                        }
                      >
                        지우기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Fold>
      ) : null}
    </>
  );
}
