/**
 * 종목 순위 — **나란히 놓기만 한다** (T18 ②).
 *
 * 🔴 **표시 전용이다.** 가중합 점수도 "오늘의 1위" 배지도 만들지 않는다 (사용자 확정
 * 2026-08-19). 만드는 순간 그것이 **검증되지 않은 새 규칙**이 되고, 화면에서 추천처럼
 * 보인다 — 이 프로젝트는 규칙을 out-of-sample 로만 채택한다 (§5.6.7).
 *
 * ⇒ 사람이 어느 열로 볼지 고르는 것까지가 이 창의 일이다.
 */

import { useEffect, useState } from "react";
import {
  ranking,
  type Rank,
  type SymbolGroup,
  type SymbolTags,
} from "./api";
import { ErrorCard, Fold, num, pct } from "./ui";

/** 정렬할 수 있는 열 — 전부 **관측값**이고 합성값이 없다. */
const COLUMNS = [
  { key: "turnover", label: "거래대금(24h)", unit: "USDT" },
  // ⭐ **자리가 나는 빈도는 거래량이 아니라 여기서 온다** (사용자 요구 2026-08-20).
  //   실측: BTC 는 거래대금이 XRP 의 36배인데 3시간에 1.67% 움직였고 매매가 0 이었다.
  { key: "recent_pct", label: "최근 1시간 변동성", unit: "%" },
  { key: "volatility", label: "변동성(24h)", unit: "%" },
  { key: "spread", label: "스프레드(지금)", unit: "%" },
  { key: "change", label: "등락", unit: "%" },
] as const;

type Key = (typeof COLUMNS)[number]["key"];

/**
 * 열 하나로 줄을 세운다 — **누를 때마다 방향이 바뀐다** (사용자 신고 2026-08-20).
 *
 * @param rows 종목 줄.
 * @param key 정렬할 열.
 * @param down 내림차순인가.
 *
 * 🔴 사용자 신고: *"클릭해도 내림차순 정렬이 안되네?"* 방향이 **내림차순으로 고정**돼
 * 있어서, 이미 그 열로 정렬된 상태에서 다시 눌러도 아무 일이 안 일어났다 — 눌렀는데
 * 화면이 안 변하면 사람은 **정렬이 안 되는 것**으로 읽는다.
 *
 * ⛔ **못 읽은 값을 0 으로 치지 않는다.** 그러면 *"모르는 것"* 이 *"가장 나쁜 것"* 이
 * 되고, 오름차순에서는 맨 위로 올라와 제일 좋은 것처럼 보인다.
 *
 * ⇒ 빈 값은 **방향과 무관하게 언제나 아래**다.
 */
export function ordered(
  rows: readonly Rank[],
  key: Key,
  down: boolean,
): Rank[] {
  return [...rows].sort((a, b) => {
    const left = a[key];
    const right = b[key];
    if (left === null || left === undefined) return 1;
    if (right === null || right === undefined) return -1;
    return down ? right - left : left - right;
  });
}

/** 큰 수를 읽기 좋게 — 12.4억이 12,400,000,000 보다 눈에 빨리 든다. */
function big(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  return num(value, 0);
}

/**
 * **나갈 수 있는가** — 주문이 나가는 곳의 호가 한 칸 (사용자 제안 2026-08-20).
 *
 * 🔴 이 창은 지금까지 거짓말을 하고 있었다. 왼쪽 스프레드는 **조회용 라이브 API** 값이라
 * SPCX 가 0.007% 로 BTC 급으로 건강해 보인다 — 증거금 420 을 15시간 묶은 계약이다
 * (주문 거래소 실측: 매수 격차 21.8% · 5% 안쪽 깊이 0).
 *
 * ⭐ **못 읽은 것을 "괜찮다" 로 그리지 않는다.** 주문 거래소에 계약이 아예 없는 종목이
 * 있고(SNDK · SKHY), 그것을 초록으로 칠하면 화면이 가장 나쁜 거짓말을 한다.
 */
function Exit({ row }: { row: Rank }) {
  const got = row.exit;
  if (!got || !got.read) {
    return (
      <span
        className="chip"
        title={
          got?.why || "주문 거래소에서 호가를 못 읽었다 — 계약이 없을 수 있다"
        }
      >
        확인 불가
      </span>
    );
  }
  if (!got.ok) {
    return (
      <span className="chip loss" title={got.why}>
        막힌다 · 매수 {pct(got.bid_gap ?? null)}
      </span>
    );
  }
  return (
    <span
      className="chip gain"
      // ⚠️ 깊이는 **판정하지 않고 숫자로만** 준다 — 이 창에서는 아직 예산을 모른다.
      title={`매수 격차 ${pct(got.bid_gap ?? null)} · 5% 안쪽 매수 깊이 ${big(
        got.bid_depth,
      )} USDT (내 명목과 견주는 것은 판을 띄울 때 한다)`}
    >
      나갈 수 있다
    </span>
  );
}

export function Ranking({ markets = [] }: { markets?: string[] }) {
  const [rows, setRows] = useState<Rank[]>([]);
  /**
   * 볼 거래소 — **콘솔이 연결됐다고 말한 것들 중에서** (2026-09-22).
   *
   * 🔴 이 화면은 Gate 만 알았다. 바이낸스 테스트넷에 연결된 로컬 데모에서는 *"연결된 Gate 계정이
   * 없다"* 만 떴다 (사용자 지적: *"바이낸스 테스트넷에서는 바이낸스로, Gate 에서는 Gate 로 떠야지"*).
   * 이름을 여기 적지 않는다 — 목록은 콘솔이 주고, 어느 표인지는 서버 응답이 말한다.
   */
  const [picked, setPicked] = useState("");
  const market = markets.includes(picked) ? picked : (markets[0] ?? "");
  /** 서버가 실제로 답한 거래소 — 제목에 적는다 (화면이 지어내지 않는다). */
  const [shown, setShown] = useState("");
  const [error, setError] = useState("");
  /**
   * 서버가 **줄이 없는 이유**를 말해 줄 때 그 말 (2026-09-22).
   *
   * 🔴 서버는 연결된 거래소가 없으면 `{rows: [], note: "…"}` 를 돌려주는데
   * 화면이 `note` 를 **아예 안 읽고 있었다** — 그래서 머리글만 남은 빈 표가 떴고, 사람은
   * 고장인지 조용한 건지 알 수 없었다 (사용자 신고: *"종목 순위도 아예 출력이 안되고 있어"*).
   * 조용한 실패 금지(규칙 #8)는 서버만의 일이 아니다.
   */
  const [note, setNote] = useState("");
  /**
   * 탭 — **코인 · 주식 추종 · 지수 추종** (사용자 요구 2026-09-22).
   *
   * 🔴 탭 이름도 종목 분류도 **서버가 말한다** (`config/symbol_groups.yml`). 화면에 적으면 거래소가
   * 계약을 하나 올릴 때마다 화면을 고쳐야 한다 — 종목 목록에서 겪은 바로 그 두 벌 문제다.
   *
   * ⭐ 서버는 **고른 탭만** 잰다. 종목마다 호가창과 봉을 묻기 때문에 세 탭을 한꺼번에 받으면
   * 그만큼 느려진다 (사용자: *"종목 순위 띄우는데 좀 오래 걸리긴 한다"*).
   */
  const [tab, setTab] = useState("");
  const [groups, setGroups] = useState<SymbolGroup[]>([]);
  const [tags, setTags] = useState<SymbolTags>({});
  /** 받는 중인가 — 탭을 바꾼 직후 남의 탭 줄을 그대로 두면 분류가 틀린 것처럼 보인다. */
  const [busy, setBusy] = useState(false);
  // ⭐ 거래대금이 기본이다 — 얇은 종목에서는 스프레드가 좁아 보여도 실제로 못 채운다.
  const [sort, setSort] = useState<Key>("turnover");
  // ⭐ **누른 열을 다시 누르면 방향이 뒤집힌다.** 안 그러면 이미 그 열로 정렬된 상태에서
  //   눌렀을 때 화면이 안 변하고, 사람은 정렬이 고장난 것으로 읽는다.
  const [down, setDown] = useState(true);

  useEffect(() => {
    let alive = true;
    const pull = () => {
      ranking(market || undefined, tab || undefined)
        .then((body) => {
          if (!alive) return;
          setRows(body.rows);
          setNote(body.note ?? "");
          setShown(body.market ?? "");
          setGroups(body.groups ?? []);
          setTags(body.tags ?? {});
          setError("");
        })
        .catch((exc: unknown) => alive && setError(String(exc)))
        .finally(() => alive && setBusy(false));
    };
    // 탭·거래소가 바뀌면 옛 줄을 지운다 — 주식 탭에 코인 줄이 1초라도 떠 있으면 안 된다.
    setRows([]);
    setBusy(true);
    pull();
    // ⚠️ 대부분 24시간 통계라 자주 물을 이유가 없다. 스프레드만 지금 값이고, 그 값은
    //    비용 판단에 쓰지 않는다 (그것은 config/costs.yml 의 실측이다).
    // ⭐ **최근 1시간도 1분이면 충분하다** — 1시간 창이 1분에 크게 안 바뀐다. 그리고
    //   이 창은 종목마다 호가창과 봉을 물으므로 주기를 줄이면 왕복이 그만큼 곱해진다.
    const timer = setInterval(pull, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // 거래소·탭이 바뀌면 바로 다시 받는다 — 남의 표를 1분 동안 보여 주지 않는다.
  }, [market, tab]);

  /** 지금 보는 탭 — 아직 안 골랐으면 서버가 첫 번째로 준 것(기본 묶음)이다. */
  const current = tab || groups[0]?.key || "";
  const hint = groups.find((g) => g.key === current)?.hint ?? "";

  const sorted = ordered(rows, sort, down);

  const top = sorted[0];
  // ⚠️ 접힌 상태에서도 이유가 보여야 한다 — 펴 봐야 아는 빈 표가 지금까지의 문제였다.
  const summary = error
    ? "읽지 못했다"
    : top
      ? `${sorted.length}종목 · ${COLUMNS.find((c) => c.key === sort)?.label} ${
          down ? "내림" : "오름"
        }차순`
      : busy
        ? "읽는 중…"
        : note || "아직 안 읽었다";

  return (
    <Fold name="종목 순위" summary={summary} keep="ranking" initialShut>
      <p className="card-hint">
        나란히 놓기만 한다 — <b>점수도 추천도 만들지 않는다</b>. 어느 열로
        볼지는 사람이 고른다.
        {shown ? (
          <>
            {" "}
            · 거래소 <b>{shown}</b>
          </>
        ) : null}
      </p>
      {/* 연결된 거래소가 둘 이상일 때만 고르개를 낸다 — 하나면 고를 것이 없다. */}
      {markets.length > 1 ? (
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          {markets.map((name) => (
            <button
              key={name}
              type="button"
              className={`btn small${name === market ? " picked" : ""}`}
              onClick={() => setPicked(name)}
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}
      {/* ⭐ 탭 — 서버가 준 묶음들. 하나뿐이면 고를 것이 없으니 안 그린다. */}
      {groups.length > 1 ? (
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          {groups.map((g) => (
            <button
              key={g.key}
              type="button"
              className={`btn small${g.key === current ? " picked" : ""}`}
              title={g.hint || undefined}
              onClick={() => setTab(g.key)}
            >
              {g.label}
            </button>
          ))}
        </div>
      ) : null}
      {hint ? <p className="card-hint">{hint}</p> : null}
      {error ? <ErrorCard message={error} /> : null}
      {/* 🔴 **줄이 없으면 왜 없는지 말한다.** 머리글만 남은 표는 고장과 구별되지 않는다. */}
      {!error && !busy && sorted.length === 0 ? (
        <p className="notice warn">
          {note ||
            (groups.length > 1
              ? "이 거래소에는 이 탭에 선언된 종목이 없다 (config/symbol_groups.yml)."
              : "줄이 없다 — 서버가 이유를 말하지 않았다. 이것 자체가 확인할 거리다.")}
        </p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>종목</th>
              <th className="num">가격</th>
              {COLUMNS.map((col) => (
                <th key={col.key} className="num">
                  <button
                    type="button"
                    className={`why${sort === col.key ? " on" : ""}`}
                    // ⭐ 같은 열을 다시 누르면 **방향만** 바뀐다. 다른 열이면 내림차순으로
                    //   시작한다 — 큰 값이 궁금해서 누르는 것이 보통이다.
                    onClick={() => {
                      if (sort === col.key) {
                        setDown(!down);
                        return;
                      }
                      setSort(col.key);
                      setDown(true);
                    }}
                    title="누르면 이 열로 정렬한다 · 다시 누르면 방향이 바뀐다"
                  >
                    {col.label}
                    {sort === col.key ? (down ? " ▼" : " ▲") : ""}
                  </button>
                </th>
              ))}
              <th>나갈 수 있나</th>
              <th>판</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.symbol}>
                <td>
                  <span className="mono">{row.symbol}</span>
                  {row.name ? <span className="faint"> {row.name}</span> : null}
                  {/* 🔴 **숏 추종은 눈에 띄어야 한다** (사용자 요구 2026-09-22) — 이 계약을 롱으로
                      사는 것이 지수를 숏 치는 것이라, 등락의 뜻이 다른 줄과 반대다. */}
                  {(row.tags ?? []).map((key) => (
                    <span
                      key={key}
                      className={`chip ${key === "inverse" ? "warn" : ""}`}
                      title={tags[key]?.hint || undefined}
                      style={{ marginLeft: 6 }}
                    >
                      {tags[key]?.label ?? key}
                    </span>
                  ))}
                </td>
                <td className="num">{num(row.price ?? null, 4)}</td>
                <td className="num">{big(row.turnover)}</td>
                {/* ⭐ **지금 움직이나** — 박스 끝에 닿아야 자리가 난다. */}
                <td className="num">{pct(row.recent_pct ?? null)}</td>
                <td className="num">{pct(row.volatility ?? null)}</td>
                <td className="num">{pct(row.spread ?? null)}</td>
                <td
                  className={`num ${
                    row.change === null || row.change === undefined
                      ? ""
                      : row.change >= 0
                        ? "gain"
                        : "loss"
                  }`}
                >
                  {pct(row.change ?? null)}
                </td>
                {/* 🔴 **나갈 수 있는가** (사용자 제안 2026-08-20). 왼쪽 스프레드는
                    **조회용 라이브 API** 값이라 SPCX 가 0.007% 로 BTC 급으로 건강해
                    보인다 — 증거금 420 을 15시간 묶은 바로 그 계약이다. 이 열은
                    **주문이 나가는 곳**의 호가를 본다. */}
                <td>
                  <Exit row={row} />
                </td>
                {/* 🔴 **못 띄우는 종목이 있는 것을 받아들이되 조용히 넘어가지 않는다**
                    (T18 ④). 호가 눈금이 선언 안 된 종목은 판을 못 띄운다.
                    ⚠️ 위 열과 **합치지 않는다** — 왜 막혔는지가 사라진다. */}
                <td>
                  {row.missing ? (
                    <span className="chip loss" title={row.missing}>
                      없다
                    </span>
                  ) : row.tradable ? (
                    <span className="chip gain">띄울 수 있다</span>
                  ) : (
                    <span
                      className="chip"
                      title="config/costs.yml 의 spec_ticks 에 없다"
                    >
                      눈금 미선언
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Fold>
  );
}
