/**
 * 실계좌 페이퍼 — Gate testnet 페이크머니로 **실제 주문이 나가는** 판.
 *
 * 🔴 **원장(모형)과 계좌(사실)를 나란히 놓는 것이 이 화면의 일이다.** 둘이 갈리는 것이
 * 이 프로젝트에서 가장 위험한 상태였고, 멀리 떼어 놓으면 눈이 그 차이를 못 잡는다.
 *
 * ⚠️ 이 탭은 **성적을 재는 곳이 아니다** — 돌긴 도는지, 주문이 실제로 나가는지를 본다.
 * 성적은 T14·T15 가 끝난 뒤에야 뜻을 갖는다.
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { dropSession, probe as runProbe, type Probe } from "./api";
import { Live } from "./Live";
import { Chart, type MarkTone } from "./Chart";
import type { TradeMark } from "./chart/trades";
import { IndicatorPanel } from "./chart/IndicatorPanel";
import { readStance } from "./adx";
import { useForming } from "./useForming";
import { useLive } from "./useLive";
import {
  Card,
  Disconnected,
  Fact,
  Fold,
  Findings,
  SkippedBars,
  ago,
  aliveness,
  frameSeconds,
  num,
  pct,
  when,
} from "./ui";

/**
 * 안전장치 목록 — **발동이 0 이어도 줄은 남는다** (11번).
 *
 * 🔴 목록에서 빼면 *"그런 방어선이 있었나"* 를 아무도 못 묻는다. 0 인 채로 보이는
 * 것이 안 보이는 것보다 낫다.
 */
const GUARDS: [string, string][] = [
  ["panic_close", "손절을 3번 연속 못 걸면 시장가로 던진다"],
  ["geometry", "이익이 날 수 없는 계획의 주문을 막는다"],
  ["adopt", "판이 죽어도 거래소 포지션을 원장으로 되읽는다"],
  ["reconcile", "거래소가 먼저 닫으면 30초 안에 원장도 닫는다"],
  ["margin_share", "다른 판이 지갑을 쓰면 예산을 줄인다"],
  ["liquidation", "강제청산을 손절과 갈라 센다"],
];

/** 고를 수 있는 종목 — Gate 무기한만. 업비트는 조회 전용(키 없음)이라 주문이 없다. */

type Props = {
  /**
   * **볼 판** — 주소에서 온다 (`/paper/{run}`).
   *
   * 🔴 **화면이 고르지 않는다** (사용자 요구 2026-08-19: *"여러 창에 띄워두고 보는 게
   * 나을 것 같네"*). 화면 상태로 고르면 창마다 같은 판을 보게 되고, 판을 나란히 놓고
   * 비교할 방법이 없다.
   *
   * ⚠️ 비어 있으면 **아무것도 안 고른다.** 임의로 하나를 띄우면 주소와 화면이 다른
   * 것을 가리키고, 새로고침할 때마다 다른 판이 뜬다.
   */
  run: string;
  /** 콘솔로 돌아간다 — 판을 못 찾았을 때 갈 곳이다. */
  home: () => void;
  /**
   * **이 판이 어느 종목인지 알아냈다** — 셸이 탭 이름에 쓴다 (사용자 요구 2026-08-20).
   *
   * 🔴 탭 이름은 판을 **누를 때** 정해졌다. 그래서 주소로 바로 열거나 새로고침한 탭은
   * 이름을 못 받아 `live4b511b11` 로 남았다 — 같은 화면에 `SOL_USDT RUN` 과
   * `livee112d30d` 가 나란히 뜬 이유다.
   *
   * ⇒ 판을 읽고 나면 화면이 셸에 알려 준다. **누른 경로든 주소로 온 경로든 같아진다.**
   */
  named?: (run: string, name: string) => void;
};

export function PaperTab({ run, home, named }: Props) {
  const [frame, setFrame] = useState("15m");
  // ⭐ 사람이 축을 골랐는가 — 고르기 전까지만 판정 축으로 자동 정렬한다 (아래 effect).
  const pickedFrame = useRef(false);
  const live = useLive(true, frame, run);
  // 🔴 점검이 가리킨 봉을 1초 깜빡인다 — 지연 초만 적으면 어느 봉인지 알 수 없다.
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [probe, setProbe] = useState<Probe | null>(null);
  // 🔴 삭제는 포지션까지 닫는다 — 되돌릴 수 없으므로 한 번 더 묻는다.
  const [dropping, setDropping] = useState(false);
  // ⭐ 근거를 펼친 매매 id. 한 번에 하나만 펼친다 — 여러 줄이 동시에 열리면 표가
  //    길어져 정작 비교하려던 두 매매가 화면 밖으로 밀린다.
  const [why, setWhy] = useState<string | null>(null);
  /** 표에서 고른 매매 — 차트에서 그 상자만 진하게 + 테두리. 다시 누르면 푼다. */
  const [focusTrade, setFocusTrade] = useState<string | null>(null);

  const { state, health, current } = live;
  // ⚠️ **첫 프레임을 한 번만 꺼낸다.** 단언(!)은 컴파일러만 속이고 런타임에는
  //    undefined 가 그대로 들어간다 — 차트 안에서 터지면 화면이 통째로 사라진다
  //    (흰 화면 신고 2026-08-30).
  const first = state?.frames?.[0];
  // 🔴 **라이브는 켜고 끄는 스위치다** (사용자 요구 2026-08-30). 켜져 있는 동안에는
  //    줌·축을 바꿔도 오른쪽 끝이 최신 봉에서 안 벗어난다 — 그 고정은 `Chart` 가 한다.
  const [live_on, setLiveOn] = useState(true);

  // 🔴 **결론 한 줄** — 문 하나하나가 아니라 *"지금 어느 전략이 서 있나"*.
  //    서버가 판정 결과를 그대로 넘긴다 (`overlay.stance`) — 화면이 다시 판정하면
  //    자기 규칙을 갖게 되고, 그러면 판정과 갈릴 수 있다.
  const stance = useMemo(() => {
    const layer = state?.frames?.[0]?.layers?.find(
      (item) => item.flag === "overlay.stance",
    );
    return readStance((layer?.shapes ?? []) as Record<string, unknown>[]);
  }, [state]);

  // ⭐ **판을 읽고 나면 셸에 종목을 알려 준다** (사용자 요구 2026-08-20). 탭 이름이
  //    누를 때만 정해져서, 주소로 열거나 새로고침한 탭은 판 id 로 남아 있었다.
  const symbol = current?.symbol;
  useEffect(() => {
    if (symbol) named?.(run, symbol);
  }, [named, run, symbol]);
  // ⭐ 진행 중 봉 — 보고 있는 축으로, `/state` 보다 빠르게 따로 당긴다.
  const forming = useForming(current?.session_id, frame);
  const board = state?.dashboard;
  const account = health?.exchange;
  const position = account?.position ?? {};
  const open = Object.keys(position).length > 0;
  const pnl = Number(position["unrealised_pnl"] ?? 0);

  // 🔴 **계획선 출처가 둘이다** (사용자 지적 2026-08-18: *"가로선으로 진입가, 손절가
  //    등등 출력 안됨"*). 서버의 `position` 은 **보유 중일 때만** 온다 — 포지션이 없으면
  //    null 이라 선이 하나도 안 그려졌다.
  //
  //    ⇒ 없으면 **매매 로그의 열린 건**에서 만든다. 원장이 아는 계획은 거기 있다.
  //
  // ⚠️ 둘 다 없으면 안 그린다. 없는 선을 지어내는 것이 가장 나쁘다.
  const openTrade = state?.log.find((row) => row.exit === null) ?? null;
  const planBase =
    state?.position ??
    (openTrade
      ? {
          entry: openTrade.entry,
          stop: openTrade.stop,
          // 🔴 **네 선을 다 넘긴다** (사용자 지적 2026-08-18: *"왜 이전에 뜨던 1차
          //    익절, 익절 등이 안뜨지?"*). 여기서 둘만 넘기고 있어서, 러너가 없는
          //    동안에는 손절·진입만 그려졌다.
          first: openTrade.first,
          target: openTrade.target,
        }
      : null);
  // 🔴 **추세추종이면 목표선을 숨긴다** (사용자 지적 2026-08-24: *"익절선이 에베레스트"*).
  //    full_ride 는 진입+100R 자리표시자를 목표로 두고 트레일 손절로 나간다 — 그 목표를
  //    그리면 화면이 "29배 대박 대기" 처럼 거짓말한다. 플래그를 계획선에 실어 차트가 가른다.
  const planLines =
    planBase === null ? null : { ...planBase, full_ride: state?.full_ride ?? false };

  // 🔴 **매매가 일어난 순간을 차트에 넘긴다** (사용자 요구 2026-08-18: *"진입한
  //    시점이랑, 1차 익절 시점, 익절 시점, 손절 시점 등 세로선도 안보이네"*).
  //
  //    가로선(계획가)만으로는 *"언제"* 가 안 보인다 — 어느 봉에서 생긴 계획인지
  //    모르면 되짚을 수가 없다.
  //
  // ⚠️ **열린 건만이 아니라 로그 전체**를 넘긴다. 청산된 매매의 흔적이 사라지면
  //    화면이 "오늘 아무 일도 없었다" 로 보인다.
  // 🔴 **날 hex 를 넘기지 않는다** (사용자 감사 2026-08-30). 전에는 `#0f7b6c` 같은 값을
  //    그대로 넘겼는데, 다크 모드에서 토큰이 바뀌어도 **매매 마커만 밝은 테마 색으로
  //    남았다** — 차트의 다른 모든 것은 바뀌는데 이것만 안 바뀌었다.
  //
  // ⇒ **뜻**을 넘기고 색은 차트가 그릴 때마다 토큰에서 푼다.
  const marks = (state?.log ?? []).flatMap((row) => {
    const out: { at: string; label: string; tone: MarkTone }[] = [];
    if (row.opened_at) out.push({ at: row.opened_at, label: "진입", tone: "entry" });
    if (row.half_at) out.push({ at: row.half_at, label: "1차", tone: "gain" });
    if (row.closed_at) {
      // ⭐ **손절과 익절을 색으로 가른다** — 같은 색이면 결과를 눈으로 못 읽는다.
      const lost = (row.gain_pct ?? 0) < 0;
      out.push({
        at: row.closed_at,
        label: lost ? "손절" : "익절",
        tone: lost ? "loss" : "gain",
      });
    }
    return out;
  });

  /**
   * **끝난 매매를 상자로** — 점(marks)은 *"언제"* 만 말하고 *"어디서 어디까지"* 는 못 말한다
   * (사용자 요구 2026-09-21: *"지난 매매가 차트에 표시되게 — 손해는 붉은 박스, 진입가는 경계,
   * 익절가는 파란 박스"*).
   *
   * 색·구간 규칙은 `chart/trades.ts` 가 정한다 — 백테스트 차트가 이미 그 규칙으로 그리고 있었고
   * 라이브만 안 쓰고 있었다. 두 화면이 각자 색을 정하면 같은 매매가 달라 보인다.
   *
   * ⚠️ **열린 매매는 뺀다** — 그쪽은 `plan` 이 가로선으로 그린다. 넣으면 같은 값이 두 번 보인다.
   * ⚠️ `gain_pct` 가 없으면(옛 행·권한) 색은 가격 방향으로 정해진다 — `tradeZones` 안의 규칙이다.
   */
  const pastTrades = useMemo<TradeMark[]>(
    () =>
      (state?.log ?? [])
        .filter((row) => row.exit !== null && row.opened_at !== null && row.closed_at !== null)
        .map((row, index) => ({
          id: row.trade_id || `${row.opened_at}:${index}`,
          symbol: state?.symbol ?? "",
          side: row.direction === "숏" ? (-1 as const) : (1 as const),
          entry: Number(row.entry),
          exit: Number(row.exit),
          stop: Number(row.stop),
          openedTs: Math.floor(Date.parse(row.opened_at as string) / 1000),
          closedTs: Math.floor(Date.parse(row.closed_at as string) / 1000),
          pnl: row.gain_pct === null ? null : Number(row.gain_pct),
          reason: row.outcome,
        }))
        .filter((t) => Number.isFinite(t.entry) && Number.isFinite(t.exit) && t.openedTs > 0),
    [state?.log, state?.symbol],
  );

  // 로직이 도는가 — 셋을 하나로 합쳐 답한다.
  //
  // 🔴 **판정 축은 서버가 말한다** (사용자 지적 2026-08-18: *"15분 기준으로 매매로직이
  //    도는 걸로 알고 있거든"* — 맞다). 전에는 **차트에서 고른 축**을 넣어서, 10초봉을
  //    보는 동안 화면이 *"첫 10초 봉이 마감되면 돈다"* 고 적었다. 판정은 플레이북 진입
  //    축(15m)에서만 도는데 90배 짧은 주기를 말한 것이고, 12분밖에 안 된 정상 대기가
  //    **고장으로 읽혔다.**
  //
  // ⚠️ 서버가 아직 안 알려 주면 차트 축으로 **떨어지지 않는다** — 그것이 바로 그 거짓말
  //    이다. 모르면 `—` 로 두는 편이 낫다.
  const judgeFrame = health?.entry;
  const info = health?.run;
  const interval = frameSeconds(judgeFrame ?? "15m");
  // ⭐ 첫 화면 축 = 판정 축(◆) (사용자 요구 2026-08-26). 매매 좌표는 판정 축(4h)에
  //   그려지는데 15m 고정 시작이라 첫 화면이 좌표 없는 축을 보여줬다. 서버가 판정
  //   축을 알려주는 순간 한 번 맞추고, 사람이 고른 뒤에는 건드리지 않는다.
  useEffect(() => {
    if (!pickedFrame.current && judgeFrame && judgeFrame !== frame) {
      setFrame(judgeFrame);
    }
  }, [judgeFrame, frame]);
  const judgedAgo = state?.cursor
    ? (Date.now() - new Date(state.cursor).getTime()) / 1000
    : undefined;
  const untilNext = health?.next_judge_at
    ? (new Date(health.next_judge_at).getTime() - Date.now()) / 1000
    : undefined;
  // ⭐ 이 판이 산 시간 — 판정 축 봉을 **몇 개나 지켜봤는지**의 근거다.
  const upFor = current?.started_at
    ? (Date.now() - new Date(current.started_at).getTime()) / 1000
    : undefined;
  const alive = aliveness(
    health?.steps,
    health?.running,
    judgedAgo,
    interval,
    untilNext,
  );

  // 🔴 **접었을 때도 판을 읽을 수 있어야 한다** (사용자 요구 2026-08-19). 한 줄이
  //    너무 짧으면 접기가 곧 "안 보기" 가 되고, 그러면 아무도 안 접는다.
  //
  // ⚠️ 순서는 **돈 → 포지션 → 로직**이다. 판이 이상한지 가장 빨리 답하는 것이 돈이고,
  //    가장 자주 묻는 것도 그것이다.
  const summary = [
    account ? `잔액 ${num(account.available, 2)}` : "잔액 —",
    `증거금 ${num(board?.cash ?? null, 2)}`,
    `손익 ${pct(board?.return_pct ?? null)}`,
    open ? `보유중 ${position["size"]}계약` : "포지션 없음",
    // ⭐ 로직이 도는가 — 카드와 **같은 판단**을 쓴다. 따로 계산하면 접었을 때와 폈을
    //    때가 다른 말을 한다.
    alive.label,
  ].join(" · ");

  const act = (name: string, run: () => Promise<unknown>) => {
    setBusy(name);
    live.setError("");
    run()
      .then(() => live.refresh())
      .catch((exc: unknown) => live.setError(String(exc)))
      .finally(() => setBusy(""));
  };

  return (
    <div className="page">
      <div className="page-head">
        {/* 🔴 **설명이 아니라 이 RUN 의 정보다** (사용자 요구 2026-08-19). 설명은
            한 번 읽으면 아는 문장이고, 매번 화면 위를 차지하면서 *"내가 지금 무엇을
            보고 있나"* 에는 답하지 않는다. */}
        {/* ⭐ 제목이 **어느 RUN 인지** 말한다 — 탭이 여럿이라 화면만 보고는 못 가른다. */}
        {/* 🔴 **제목은 종목이다** (사용자 요구 2026-08-20). `live750543b2` 는 사람이
            못 읽는 문자열이라 *"내가 지금 무엇을 보고 있나"* 에 답하지 못한다 —
            판 id 는 바로 아래 칩에 그대로 있다.

            ⛔ 종목을 화면에 박지 않는다. 판이 말해 주는 값을 그대로 쓴다. */}
        <h1>{current ? `${current.symbol} 상세` : "RUN 상세"}</h1>
        {current ? (
          <div className="run-head">
            <span className="mono hl">{current.session_id}</span>
            <span className="mono">{current.symbol}</span>
            <span>{current.playbook}</span>
            {/* 🔴 배율 다이얼은 뺐다 (T219 결정 2026-09-05) — **펀드가 배율을 정한다.** 손으로 바꾸는 문은
                리스크를 늘리는 방향이고, 실계좌에서 그 문이 열려 있을 이유가 없다. 값은 그대로 보여 준다. */}
            <span className="chip" title="배율은 펀드(플레이북 선언)가 정한다">
              배율 {Number(current.leverage)}x
            </span>
          </div>
        ) : null}
        {/* 🔴 **축이 셋이고 눈금이 하나다** — 둘 다 화면에 없으면 무엇으로 판정하고
            어떤 격자에 가격을 적는지 알 수 없다. 2026-08-18 사고(원화 상수 눈금)를
            봉 단위로 파고들 때까지 몰랐던 이유가 그것이다. */}
        {info ? (
          <div className="run-facts">
            <Fact
              name="판정 축"
              value={info.judge_frame}
              note="구조물을 쌓는 봉"
            />
            <Fact
              name="방아쇠 축"
              value={info.trigger_frame}
              note="진입을 재는 봉"
            />
            <Fact
              name="진입가 축"
              value={info.price_frame}
              note="원장에 적는 종가"
            />
            <Fact
              name="호가 눈금"
              value={info.tick ?? "모른다"}
              note={
                info.tick
                  ? `가격의 ${info.tick_pct}% · 명세 ${info.spec_tick ?? "—"} x 배율 ${info.tick_ratio}`
                  : "선언이 없다 — 이 종목으로는 계획이 안 선다"
              }
              bad={!info.tick}
            />
            <Fact
              name="배율"
              value={`${Number(info.leverage)}x`}
              note="손익률에 그대로 곱한다"
            />
            <Fact
              name="증거금"
              value={
                info.margin_budget ? num(info.margin_budget, 2) : "지갑 전액"
              }
              note={`시드 ${num(info.seed_cash, 2)} · 모형 ${info.funding}`}
            />
            {/* 🔴 **주문을 내는 판인지 화면이 말한다.** 관찰 전용의 손익은 원장의
                모형이고 체결 실패도 슬리피지도 없다 — 실주문 판과 나란히 놓으면
                "안 낸 주문이 잘 됐다" 가 성적이 된다 (§1-0s). */}
            {info.observe_only ? (
              <Fact
                name="주문"
                value="관찰 전용 — 안 나간다"
                bad
                note="주문 경로에 계약이 없다. 손익은 원장의 모형이라 실주문 판과 못 비교한다"
              />
            ) : null}
            <Fact
              name="이어받기"
              value={info.resumed ? "이어받은 RUN" : "새로 뜬 RUN"}
              note={`뜬 시각 ${when(info.started_at)}`}
            />
          </div>
        ) : null}
      </div>

      {/* 🔴 **건너뛴 봉은 맨 위에서 말한다** (사용자 2026-09-21). 아래쪽 붉은 줄로 두었더니
          ETH 가 한 건을 놓친 것을 사람이 묻기 전까지 아무도 못 봤다. */}
      <SkippedBars
        run={run}
        count={health?.failures ?? 0}
        detail={health?.last_error}
      />
      {!live.link.ok ? <Disconnected silentFor={live.link.silentFor} /> : null}
      {live.error ? <p className="notice bad">{live.error}</p> : null}
      <Findings items={health?.findings ?? []} />

      {/* 🔴 **갓 뜬 판은 아직 아무것도 못 봤다** (사용자 지적 2026-08-18: *"이미 스마트
          박스에 한참 닿은 것 같은데 반응이 없어"*).

          라이브는 **판이 살아 있는 동안 마감된 봉만** 판정한다 — 시드로 받은 과거 봉은
          이미 판정된 것으로 친다(안 그러면 뜨자마자 유령 주문이 난다). 그래서 API 가
          재시작되면 판이 새로 뜨고, **꺼져 있던 사이의 진입 자리는 아무도 안 본다.**

          ⚠️ 화면이 이 말을 안 하면 "0 회" 가 **고장**으로만 읽힌다. 실제로는 대기다. */}
      {/* 🔴 **판들이 지갑을 나눠 쓴다** (다중 RUN ㄷ). 선착순은 규칙이지만 말하지
          않으면 주문이 작아진 것을 전략 문제로 오해한다. */}
      {/* 🔴 **묻지 않고 거둔 것은 반드시 보여야 한다** (2026-08-21). 주인 없는 조건부는
          답이 하나뿐이라 시작할 때 조용히 거두는데, 그러면 사람이 나중에 *"내 손절
          어디 갔지"* 라고 물을 수 있다 — 그 답이 화면에 있어야 조용한 실패가 아니다. */}
      {health?.swept?.length ? (
        <p className="notice">
          <b>시작할 때 잔재 {health.swept.length}건을 치웠다</b> —{" "}
          {health.swept.join(" · ")}.{" "}
          <span className="faint">
            지난 판이 남긴 조건부·익절이다. 남겨 두면 <b>이 판의 포지션을 닫는다</b>.
          </span>
        </p>
      ) : null}

      {health?.squeezed ? (
        <p className="notice">
          <b>다른 RUN 이 지갑을 쓰고 있다</b> — 예산{" "}
          {num(health.squeezed.budget, 2)} 중 지금 잡을 수 있는 돈은{" "}
          {num(health.squeezed.usable, 2)} 다.{" "}
          <span className="faint">
            먼저 진입한 RUN 이 지갑을 먹는다 — <b>선착순이 규칙이다</b>. 예산을
            늘리려면 다른 판을 지우거나 거래소에 돈을 더 넣는다.
          </span>
        </p>
      ) : null}


      {/* ── 계좌: 거래소가 말하는 사실 ─────────────────── */}
      {/* 🔴 **카드가 계속 늘어난다** (사용자 요구 2026-08-19: *"점점 뭐가 늘어나니까,
          상단 카드도 좀 접을 수 있게"*). 접으면 **한 줄 요약**만 남는다 — 완전히
          숨기면 볼 수 있었는데 안 본 상태가 되고, 그 상태는 화면에 표시가 없다. */}
      <Fold name="계좌 · 로직" summary={summary} keep="paper-cards">
        {/* 카드 14장을 두 묶음으로 — 계좌(거래소가 말하는 돈) · 로직·점검(판이 도는가) (UX 점검 2026-09-05). */}
        <h3 className="section-title">계좌</h3>
        <div className="cards">
          {/* 🔴 **"—" 은 0 이 아니라 "모른다" 다** (사용자 지적 2026-08-19: *"계좌 잔액
            (지갑) 가 안뜨네"*). 러너가 없으면 건강 조회가 404 라 값이 안 온다 — 그때
            빈 칸만 보이면 잔액이 0 인지 못 읽은 것인지 구별되지 않는다.

            ⚠️ 이 프로젝트가 같은 실수를 하루에 네 번 했다: 잔고를 못 읽은 것이
            "잔고 0" 으로, 미점검이 정상으로, 안 본 축이 멈춘 축으로 보였다. */}
          <Card
            name="계좌 잔액 (지갑)"
            value={
              account?.available ? `${num(account.available)} USDT` : "모른다"
            }
            tone={account?.available ? undefined : "loss"}
            hint={
              account?.available
                ? (account.broker ?? "—")
                : health
                  ? "거래소 조회가 실패했다 — 아래 이상 목록을 본다"
                  : "이 RUN 에 러너가 없다 — 죽은 RUN이거나 API 가 재시작 중이다"
            }
          />
          {/* 🔴 **예산과 실제 잡힌 돈은 다르다** (사용자 질문 2026-08-18: *"지금 증거금이
            안잡혀 있는데"*). 격리 마진에서 증거금은 **포지션이 열려 있는 동안만** 존재한다 —
            포지션이 없으면 0 이 맞다. 예산은 그와 무관하게 설정값이다.

            ⇒ 예산을 **큰 숫자로** 올리고, 잡힌 돈을 그 밑에 적는다. 반대로 두면
              "증거금이 안 잡혔다" 로 읽힌다. */}
          <Card
            name="굴리는 예산 (증거금)"
            value={
              current?.margin_budget
                ? `${num(current.margin_budget)} USDT`
                : "잔액 전액"
            }
            hint={
              open
                ? `지금 잡힌 돈 ${num(position["margin"])}`
                : "포지션이 열려야 실제로 잡힌다"
            }
          />
          <Card
            name="미실현 손익"
            value={open ? `${num(position["unrealised_pnl"], 4)} USDT` : "—"}
            tone={open ? (pnl >= 0 ? "gain" : "loss") : undefined}
            hint={
              open ? `청산가 ${num(position["liq_price"], 1)}` : "포지션 없음"
            }
          />
          <Card
            name="포지션"
            value={open ? `${position["size"]} 계약` : "없음"}
            hint={
              open
                ? `평단 ${num(position["entry_price"], 1)} · ${position["leverage"]}x`
                : "신호가 나면 열린다"
            }
          />
        </div>

        {/* ── 러너: 돌고 있는가 ───────────────────────────── */}
        <h3 className="section-title">로직 · 점검</h3>
        <div className="cards">
          {/* 🔴 **로직이 도는가** — 판정 횟수·스트림·봉 나이를 하나로 합쳐 답한다.
            셋이 흩어져 있으면 머리로 합쳐야 하고, 그러다 놓친다. */}
          <Card
            name={`매매 로직 (${judgeFrame ?? "—"})`}
            value={alive.label}
            tone={alive.tone}
            hint={alive.why}
          />
          <Card
            name="판정 · 주문"
            value={`${health?.steps ?? "—"}회 · ${health?.orders ?? "—"}건`}
            hint={
              // 🔴 **RUN 이 산 지 얼마나 됐는지가 "판정 0" 의 답이다** (사용자 지적
              //    2026-08-18: *"이미 스마트 박스에 한참 닿은 것 같은데 반응이 없어"*).
              //
              //    라이브는 **판이 살아 있는 동안 마감된 봉만** 판정한다 — 시드 봉은
              //    이미 판정된 것으로 친다. 그래서 API 가 재시작되면(=`src/` 를 고치면)
              //    판이 새로 뜨고, 꺼져 있던 사이의 진입 자리는 **아무도 안 본다.**
              //
              // ⇒ 산 시간을 안 적으면 "0 회" 가 고장으로만 읽힌다.
              upFor === undefined
                ? "판이 언제 떴는지 모른다"
                : `RUN 이 산 지 ${ago(upFor).replace(
                    " 전",
                    "",
                  )} · 그 전 봉은 시드라 판정하지 않는다`
            }
          />
          <Card
            name="스트림"
            value={health?.running ? "붙어 있다" : "끊겼다"}
            tone={health?.running ? undefined : "loss"}
            hint={`구멍 ${health?.gaps ?? "—"} · 재연결 ${health?.reconnects ?? "—"}`}
          />
          {/* 🔴 **지갑과 증거금은 다른 돈이다** (T14). 하나로 보여 주면 "굴리는 돈이
            얼마인지" 와 "계좌에 얼마 남았는지" 가 뭉개진다 — 원장이 1000 이라고 할 때
            거래소는 800 이었고, 그 차이만큼의 주문이 조용히 거부됐다. */}
          <Card
            name="증거금 · 지갑"
            value={`${num(board?.cash ?? null, 2)} · ${num(board?.wallet ?? null, 2)}`}
            tone={board?.halted_at ? "loss" : undefined}
            hint={
              board?.halted_at
                ? `🔴 증거금이 바닥나 새 진입을 멈췄다 (${board.halted_at.slice(0, 6)}) — 지갑이 모자란다`
                : `채워 넣은 돈 ${num(board?.topped_up ?? null, 2)} · 모형 ${
                    board?.funding ?? "—"
                  }`
            }
          />
          <Card
            name="원장 손익"
            value={pct(board?.return_pct ?? null)}
            tone={
              board && board.return_pct !== 0
                ? board.return_pct > 0
                  ? "gain"
                  : "loss"
                : undefined
            }
            hint={
              board?.tripped_at
                ? `🔴 브레이커 — 고점 대비 낙폭 ${num(board.max_drawdown_pct ?? null, 1)}% 가 ${
                    board.drawdown_stop_pct ?? "—"
                  }% 에 닿아 새 진입을 멈췄다 (${board.tripped_at.slice(0, 6)}) — 사람이 보고 켠다`
                : `매매 ${board?.closed ?? 0}/${board?.trades ?? 0} · 승 ${board?.wins ?? 0}`
            }
          />
          {/* 🔴 **낙폭을 독립 카드로 올린다** (사용자 요구 2026-08-30: *"지금 내 펀드
            가격 기준이 원래 가격에 비해 몇 % 감폭됐는지, MDD 숫자를 표시"*).
            값은 원래 원장이 재고 있었는데(T22) "원장 손익" 카드의 힌트 줄에 묻혀
            있었다 — 백테스트를 MDD 로 판정해 놓고 라이브에서는 안 보이면 그 둘을
            비교할 수가 없다.

            ⚠️ 기준은 **이 판이 번 돈**(증거금 + 누적 손익)이다. 지갑을 더하면 판
            다섯이 지갑을 공유하므로 낙폭이 판 수만큼 희석되고, 그러면 백테스트
            낙폭(증거금 기준)과 **비교가 안 된다** (ledger.py T22 주석). */}
          <Card
            name="낙폭 (고점 대비)"
            value={`${num(board?.drawdown_pct ?? null, 1)}%`}
            tone={(board?.drawdown_pct ?? 0) > 0 ? "loss" : undefined}
            hint={
              <>
                최대 <b>{num(board?.max_drawdown_pct ?? null, 1)}%</b>
                {board?.drawdown_stop_pct ? ` · 브레이커 ${board.drawdown_stop_pct}%` : ""}
                <br />이 판이 번 돈(증거금+누적손익) 기준 · 지갑 제외
              </>
            }
          />
          {/* 🔴 **점검은 알아서 돈다** (사용자 요구 2026-08-18: *"난 봉 점검을 사람이
            굳이 굳이 눌러서 점검해야 하는 이유를 전혀 모르겠어"*). 러너가 30초마다
            스스로 묻고, 버튼은 *"지금 당장"* 용으로 남는다.

            ⚠️ 손으로 누른 결과가 있으면 그것을 먼저 보여 준다 — 방금 누른 사람은
            그 답을 기다리고 있다. */}
          <div className="card">
            <span className="card-name">봉 점검 (자동 30초)</span>
            <b
              className={
                (probe ?? health?.probe)
                  ? (probe ?? health?.probe)!.streaming
                    ? "card-value gain"
                    : "card-value loss"
                  : "card-value"
              }
            >
              {(probe ?? health?.probe)
                ? (probe ?? health?.probe)!.streaming
                  ? "봉이 흐른다"
                  : "봉이 멈췄다"
                : "아직 점검 전"}
            </b>
            <button
              className="btn small"
              disabled={!current || busy !== ""}
              onClick={() => {
                if (!current) return;
                setBusy("probe");
                runProbe(current.session_id)
                  .then((body) => {
                    setProbe(body);
                    // ⭐ **지금 보고 있는 축의 마지막 봉**을 깜빡인다. 거래소의 진행 중
                    //    봉을 쓰면 차트에 **없는 봉**을 가리켜 아무것도 안 그려진다 —
                    //    실제로 그 사고를 겪었다 (2026-08-18).
                    const last = state?.frames?.[0]?.candles.at(-1)?.ts ?? null;
                    setFlash(last);
                    window.setTimeout(() => setFlash(null), 1000);
                  })
                  .catch((exc: unknown) => live.setError(String(exc)))
                  .finally(() => setBusy(""));
              }}
            >
              {busy === "probe" ? "묻는 중…" : "지금 당장 확인"}
            </button>
            {/* ⚠️ **미점검을 정상으로 칠하지 않는다** — 그렇게 두면 사고가 난 40분이
              화면상 정상으로 보인다. */}
            <span className="card-hint">
              {(() => {
                const shown = probe ?? health?.probe;
                if (!shown) return "첫 자동 점검을 기다린다";
                const lag =
                  shown.lag_seconds === null
                    ? ""
                    : ` · 지연 ${Math.round(shown.lag_seconds)}초`;
                return `${shown.verdict.slice(0, 30)}${lag}`;
              })()}
            </span>
          </div>
          {/* 🔴 **판이 재시작을 넘어 이어졌다** (T16 ②). 이 말이 없으면 "판정 0 회
            인데 매매 12건" 이 고장으로 읽힌다 — 실제로는 옛 판을 물려받은 것이다. */}
          <Card
            name="RUN 이어받기"
            value={health?.run?.resumed ? "이어받은 RUN" : "새로 뜬 RUN"}
            tone={health?.run?.resumed ? "gain" : undefined}
            hint={
              health?.run?.resumed
                ? "매매 목록·손익·시드·증거금이 딸려 왔다 · 판정 횟수와 시드 봉 구간은 안 물려받는다 (이어 붙이면 '로직이 도는가' 가 거짓말한다) · RUN 을 지우면 닫힌다"
                : "같은 종목·매매법의 열린 RUN 이 없었다 — 원장이 비어서 시작한다"
            }
          />
          {/* 🔴 **갓 뜬 판은 아직 아무것도 못 봤다** (사용자 지적 2026-08-18: *"이미
            스마트 박스에 한참 닿은 것 같은데 반응이 없어"*).

            라이브는 판이 살아 있는 동안 마감된 봉만 판정한다 — 시드로 받은 과거 봉은
            이미 판정된 것으로 친다(안 그러면 뜨자마자 유령 주문이 난다).

            ⚠️ 화면이 이 말을 안 하면 "0 회" 가 **고장**으로만 읽힌다. 실제로는 대기다. */}
          <Card
            name="첫 판정"
            value={
              upFor !== undefined && upFor < interval
                ? `${when(health?.next_judge_at)} KST`
                : "지나갔다"
            }
            hint={
              upFor !== undefined && upFor < interval
                ? `뜬 지 ${ago(upFor).replace(" 전", "")} · 아직 ${
                    judgeFrame ?? "진입 축"
                  } 봉을 한 개도 마감까지 못 지켜봤다 — 시드 봉과 RUN 이 꺼져 있던 사이의 자리는 판정하지 않는다`
                : `${judgeFrame ?? "진입 축"} 봉이 마감될 때마다 판정한다`
            }
          />
        </div>

        {/* ── 국면 문 (T26 ⑤) ─────────────────────────────
            🔴 "자리가 없었다"(후보 0)와 "자리는 있었는데 막았다"(보류 N)를
            가르지 않으면 보류가 고장으로만 읽힌다 (규칙 #8). */}
        {state?.gate ? (
          <div className="row">
            <span className="card-name">국면 문</span>
            <span className="chip" title="진입 축 한 단계 위의 주 추세">
              국면 {state.gate.major ?? "판정 전"}
            </span>
            <span
              className={state.gate.active ? "chip gain" : "chip loss"}
              title={
                state.gate.active
                  ? `도는 매매법: ${state.gate.running.join(", ")}`
                  : `선언 국면(${state.gate.regimes.join(", ")})과 지금 국면이 안 맞는다`
              }
            >
              {state.gate.active ? "매매법 돌아감" : "꺼짐 (국면 불일치)"}
            </span>
            {state.gate.blocked > 0 ? (
              <span className="chip loss" title={state.gate.blocked_why.join(" · ")}>
                진입 보류 {state.gate.blocked}
              </span>
            ) : null}
          </div>
        ) : null}

        {/* ── 축 신선도 ──────────────────────────────────── */}
        {health?.frame_ages ? (
          <div className="row">
            <span className="card-name">축 신선도 (봉이 닫힌 뒤)</span>
            {Object.entries(health.frame_ages).map(([frame, age]) => (
              <span
                key={frame}
                className="chip"
                title={`마지막 봉 ${Math.round(age)}초 전`}
              >
                {frame} {ago(age)}
              </span>
            ))}
          </div>
        ) : null}

        {/* ── 성과 리포트 지금 보내기 (사용자 요청 2026-08-23) ───
            계좌·로직 카드 묶음 안에 둔다 (사용자 요청 2026-08-24) — 판을 보다
            바로 쏘는 자리라 계좌 요약 옆이 맞다.
            🔴 판 화면이라 **그 판 종목만** 담는다 (판 이메일). */}
        {/* "이 판만" 리포트 보내기는 리포트 대시보드 메일 카드의 **판 고르기**로 옮겼다 (UX 점검 2026-09-05). */}
      </Fold>

      {/* ── 안전장치가 실제로 돈 적이 있는가 ─────────────── */}
      <Fold
        name="안전장치 발동"
        summary={`${Object.keys(health?.guards ?? {}).length}종 발동`}
        keep="guards"
        initialShut
      >
        <p className="card-hint">
          🔴 <b>안 도는 방어선은 없는 것과 같다.</b> 코드와 테스트만으로는 알 수
          없다 — 하루에 나온 결함 넷 중 단위 테스트가 잡은 것은 0개였다.{" "}
          <b>0 인 것이 나쁘다는 뜻은 아니다</b> — 발동할 일이 없었다는 뜻일 수도
          있다. 구별하는 것은 사람이다.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>안전장치</th>
                <th>무엇을 막나</th>
                <th className="num">발동</th>
                <th>마지막</th>
              </tr>
            </thead>
            <tbody>
              {GUARDS.map(([key, what]) => {
                const hit = health?.guards?.[key];
                return (
                  <tr key={key}>
                    <td className="mono">{key}</td>
                    <td className="faint">{what}</td>
                    <td className={`num ${hit ? "gain" : ""}`}>
                      {hit?.count ?? "0"}
                    </td>
                    <td className="faint" title={hit?.why}>
                      {hit ? `${when(hit.at)} · ${hit.why}` : "아직 없다"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Fold>

      {/* ⛔ **종목 순위를 여기서 뺐다** (사용자 요구 2026-08-20). 이 화면은 *"이 판이
          어떻게 하고 있나"* 를 보는 곳이고, 순위는 *"다음에 뭘 띄울까"* 다 — 판을
          여럿 열어 두면 같은 표가 화면마다 반복되고 거래소 왕복도 그만큼 는다.

          ⇒ 콘솔의 **도는 RUN 바로 위**로 옮겼다. 판을 띄우기 직전에 보는 자리다. */}

      {/* 🔴 **판을 못 찾았다.** 주소에 판 id 가 없거나, 그 RUN 이 사라졌다.
          ⛔ 임의로 다른 판을 띄우지 않는다 — 주소와 화면이 다른 것을 가리키게 된다. */}
      {!current ? (
        <div className="card wide">
          <span className="card-name">
            {run ? `판 ${run} 을 찾을 수 없다` : "RUN 을 고르지 않았다"}
          </span>
          <span className="card-hint">
            {run
              ? "지워졌거나 러너가 없다 — 콘솔에서 도는 RUN을 고른다."
              : "주소가 /paper/{판 id} 여야 한다 — 콘솔에서 RUN 을 열면 주소가 붙는다."}
          </span>
          <div className="row" style={{ marginTop: "8px" }}>
            <button className="btn primary" onClick={home}>
              거래소 콘솔로
            </button>
          </div>
        </div>
      ) : null}

      {/* ── 차트 ───────────────────────────────────────── */}
      {first ? (
        <>
          {/* 🔴 **어느 종목의 봉인가** (사용자 요구 2026-08-19). 창을 여럿 띄워 놓으면
              봉만 보고는 못 가른다 — 축 단추 바로 위가 눈이 먼저 가는 자리다.
              ⚠️ 서버가 말하는 값을 쓴다 — 화면이 기억한 값은 RUN 을 바꿀 때 낡는다. */}
          <div className="chart-head">
            <b className="mono">{current?.symbol ?? info?.symbol ?? "—"}</b>
            <span className="faint">{current?.playbook ?? ""}</span>
            {info ? (
              <span className="faint">
                구조 {info.judge_frame} · 방아쇠 {info.trigger_frame} · 눈금{" "}
                {info.tick ?? "모른다"}
              </span>
            ) : null}
            {/* 🔴 **결론은 종목 옆이다** (사용자 요구 2026-08-30). 차트 아래 칩 무리에
                섞어 두니 문 하나하나와 구별이 안 됐다 — *"지금 무슨 전략인가"* 는
                눈이 먼저 가는 자리에 있어야 한다.

                ⚠️ 아래 문턱 칩은 그대로 둔다. 그것은 **근거**이고 이것은 **결론**이다. */}
            {stance ? (
              <span
                className="chip"
                title={
                  stance.state === "선다"
                    ? "플레이북이 받았다 — 진입·손절·목표선이 차트에 그려진다"
                    : stance.state === "안 받았다"
                      ? `탐지는 됐지만 플레이북이 안 받았다 — ${stance.why}. 계획선이 없는 것이 맞다`
                      : `어느 전략도 서지 않았다 — ${stance.why}`
                }
                style={{
                  fontWeight: 600,
                  color:
                    stance.state === "선다"
                      ? "var(--gain)"
                      : stance.state === "안 받았다"
                        ? "var(--entry-line)"
                        : "var(--warm-gray)",
                }}
              >
                {stance.state === "선다"
                  ? `진입 자리 ${stance.count}건`
                  : stance.state === "안 받았다"
                    ? `탐지 ${stance.count}건 · 주문 없음`
                    : "현금 대기"}
              </span>
            ) : null}
          </div>
          <div className="row">
            {/* ⚠️ **`?? []` 다.** `frames` 는 있는데 `timeframes` 가 아직 없는 응답이
                오면 `.map` 이 터지고, 그 순간 화면이 통째로 사라진다 — 흰 화면 신고의
                후보 하나였다. 없으면 단추가 없는 것이 맞지 화면이 죽을 일은 아니다. */}
            {(state.timeframes ?? []).map((item) => (
              <button
                key={item}
                className={item === frame ? "btn small primary" : "btn small"}
                onClick={() => {
                  pickedFrame.current = true;
                  setFrame(item);
                }}
                // 🔴 **축을 바꿔도 판정은 안 바뀐다** — 그것을 눌리는 자리에서 말한다.
                title={
                  item === judgeFrame
                    ? `${item} 은 판정 축이다 — 매매 로직이 이 봉의 마감에서만 돈다`
                    : `${item} 은 보기 축이다 — 판정은 ${judgeFrame ?? "?"} 에서 돈다`
                }
              >
                {item}
                {item === judgeFrame ? " ◆" : ""}
              </button>
            ))}
            {/* ⚠️ 보고 있는 축의 나이를 옆에 붙인다 — 그 축이 얼면 그것부터 알아야
                한다. 라이브에서 축이 조용히 멈추는 사고를 여러 번 겪었다. */}
            {health?.frame_ages?.[frame] !== undefined ? (
              <span className="card-hint">
                마지막 봉 {ago(health.frame_ages[frame])}
              </span>
            ) : null}
            {/* 🔴 **차트 축 ≠ 판정 축.** 이 한 줄이 없어서 10초봉을 보던 사람이
                "왜 10초마다 판정을 안 하나" 로 읽었다. */}
            <span className="card-hint">
              ◆ = 판정 축 {judgeFrame ?? "—"} · 나머지는 보기용이다
            </span>
            {/* 🔴 **축 단추 줄 오른쪽 끝** (사용자 요구 2026-08-30). 차트 아래에 두니
                다른 칩들과 섞여 스위치로 안 읽혔다. */}
            <Live on={live_on} onToggle={() => setLiveOn((was) => !was)} />
          </div>
          {/* 🔴 **판정이 돈 봉을 차트에 표시한다** (사용자 지적 2026-08-18:
              *"매매 로직이 돌고 있는지 확인 불가"*). 숫자만으로는 *"언제 마지막으로
              판단했나"* 를 못 읽는다 — 차트 위 점이 그것을 말한다.

              ⚠️ `cursor` 는 **판정 커서**다. 진입 축 봉이 마감될 때만 움직이므로,
              그 점이 안 움직이면 판정이 멈춘 것이다. */}
          {/* ⚠️ 위 `state?.frames?.length` 가 이미 걸렀지만, **타입으로도** 못 박는다 —
              단언(`!`)은 컴파일러만 속이고 런타임에는 `undefined` 가 그대로 들어간다.
              그러면 차트 안에서 터지고, 그 순간 화면이 통째로 사라진다. */}
          <Chart
            follow={live_on}
            frame={first}
            plan={planLines}
            flashAt={flash}
            entryFrame={judgeFrame}
            judgedAt={state.cursor}
            // 🔴 **안 돌면 대기 구간도 없다.** 서버가 안 주면 `undefined` 로 흘려
            //    보내고, 차트가 "모른다" 고 말한다 — `false` 로 바꾸지 않는다.
            active={state.gate?.active}
            // 🔴 **꼬리가 실시간으로 흔들리는 봉** (사용자 요구 2026-08-18).
            //    `/state` 폴링과 **다른 주기**로 온다 — 저쪽은 봉 800개라 1.5초가
            //    한계고, 이쪽은 봉 하나라 축에 맞춰 1초까지 내려간다.
            forming={forming}
            marks={marks}
            // ⭐ 끝난 매매를 상자로 — 점만으로는 "어디서 어디까지" 가 안 보인다 (2026-09-21).
            trades={pastTrades}
            focusTradeId={focusTrade}
          />
          {/* ⭐ 지표(이평 · 볼린저) 설정 — 모든 차트가 같은 설정을 본다 (사용자 요구 2026-09-06). */}
          <div style={{ marginTop: 8 }}>
            <IndicatorPanel compact />
          </div>
        </>
      ) : null}

      {/* ── 매매 로그 ──────────────────────────────────── */}
      <h2 className="section-title">매매 {state?.log.length ?? 0}건</h2>
      <div className="table-wrap">
        {state?.log.length ? (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>결과</th>
                <th>방향</th>
                <th className="num">진입</th>
                <th className="num">청산</th>
                <th className="num">손익</th>
                <th className="num">계획RR</th>
                <th>주문</th>
                <th>근거</th>
                <th>시각</th>
              </tr>
            </thead>
            <tbody>
              {state.log.map((trade) => {
                const sent = health?.placed?.[trade.trade_id];
                const gain = trade.gain_pct;
                const rows = trade.evidence ?? [];
                const shown = why === trade.trade_id;
                const picked = focusTrade === trade.trade_id;
                return (
                  <Fragment key={trade.trade_id}>
                    {/* 줄을 누르면 차트에서 그 매매 상자가 진해진다 — 표와 차트를 잇는 유일한 손잡이다.
                        ⚠️ 끝난 매매만 상자가 있다(열린 건은 계획선이 그린다). */}
                    <tr
                      className={picked ? "picked" : undefined}
                      onClick={() => setFocusTrade(picked ? null : trade.trade_id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td className="mono faint">
                        {trade.trade_id.slice(0, 6)}
                      </td>
                      <td>
                        {trade.outcome}
                        {trade.half_by ? (
                          <span className="faint"> · {trade.half_by}</span>
                        ) : null}
                      </td>
                      <td>{trade.direction}</td>
                      <td className="num">{num(trade.entry, 1)}</td>
                      <td className="num">{num(trade.exit, 1)}</td>
                      <td
                        className={`num ${
                          gain === null ? "" : gain >= 0 ? "gain" : "loss"
                        }`}
                      >
                        {pct(gain)}
                      </td>
                      <td className="num">{num(trade.planned_rr)}</td>
                      {/* 🔴 **거래소가 이 매매를 아는가.** 원장이 보유중인데 여기가
                          비어 있으면 유령 포지션이다 — 실제로 겪었다. */}
                      <td>
                        {sent === undefined ? (
                          <span className="chip">기록 없음</span>
                        ) : sent.status === "rejected" ||
                          sent.status === "blocked" ? (
                          <span className="chip loss" title={sent.error}>
                            {sent.status === "blocked" ? "차단" : "거절"}
                          </span>
                        ) : (
                          <span className="chip gain">
                            {sent.contracts}계약
                          </span>
                        )}
                      </td>
                      {/* 🔴 **왜 들어갔는가** (T16 ①). 없으면 없을 때 돌아간 매매를
                          복기할 방법이 차트를 눈으로 다시 읽는 것뿐인데, 그것은 절대
                          규칙 #11 이 금지한 행위다. */}
                      <td>
                        {rows.length ? (
                          <button
                            type="button"
                            className="why"
                            onClick={() =>
                              setWhy(shown ? null : trade.trade_id)
                            }
                          >
                            ⓘ {rows.length}
                          </button>
                        ) : (
                          <span
                            className="faint"
                            title={
                              trade.actor && trade.actor !== "시스템"
                                ? `${trade.actor} — 근거가 원래 없다`
                                : "근거가 비어 있다 — 이 매매는 복기할 수 없다"
                            }
                          >
                            {trade.actor && trade.actor !== "시스템"
                              ? trade.actor
                              : "없음"}
                          </span>
                        )}
                      </td>
                      <td className="faint">
                        {when(trade.opened_at ?? trade.placed_at)}
                      </td>
                    </tr>
                    {shown ? (
                      <tr className="why-row">
                        <td colSpan={10}>
                          <ul>
                            {rows.map((row) => (
                              <li key={`${row.source}:${row.detail}`}>
                                <span className="chip">{row.family}</span>
                                <span className="mono faint">
                                  {" "}
                                  {row.source}
                                </span>
                                {row.detail ? ` · ${row.detail}` : ""}
                                {row.price ? (
                                  <span className="faint">
                                    {" "}
                                    · {num(row.price, 1)}
                                  </span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                          {/* ⚠️ 등급 부호를 방향으로 읽지 않게 한 줄 적어 둔다. */}
                          <p className="faint">
                            진입 시점에 박아 둔 값이다 — 그 뒤 레벨이 바뀌어도
                            안 움직인다.
                          </p>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="empty">아직 매매가 없다</p>
        )}
      </div>

      {/* ── 판 관리 ────────────────────────────────────── */}
      {current ? (
        <div className="row">
          <span className="card-name">
            RUN <span className="mono">{current.session_id}</span> ·{" "}
            {current.playbook} · {current.symbol}
          </span>
          {/* 🔴 **삭제가 무엇을 하는지 화면이 말한다** (사용자 질문 2026-08-18:
              *"play 삭제하면, 기존 주문은 다 청산하고 나오는거야?"*).

              순서: ① 거래소 포지션을 **시장가 전량 청산** → ② 러너 취소 →
                    ③ 원장·저널 삭제

              ⚠️ 청산이 실패해도 삭제는 끝난다 — 막으면 지울 수 없는 RUN 이 생긴다.
                 그때는 거래소 콘솔에서 손으로 닫아야 하고, 응답에 그 사실이 실린다.

              ⛔ 미결 지정가·조건부는 포지션이 닫히면 거래소가 정리한다(reduce_only).
                 남으면 콘솔에서 취소한다. */}
          {dropping ? (
            <>
              <span className="loss">
                포지션을 시장가로 청산하고 RUN 을 지운다 — 되돌릴 수 없다
              </span>
              <button
                className="btn small danger"
                disabled={busy !== ""}
                onClick={() =>
                  act("drop", () => dropSession(current.session_id))
                }
              >
                {busy === "drop" ? "지우는 중…" : "정말 지운다"}
              </button>
              <button className="btn small" onClick={() => setDropping(false)}>
                취소
              </button>
            </>
          ) : (
            <button
              className="btn small danger"
              onClick={() => setDropping(true)}
            >
              RUN 삭제
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}
