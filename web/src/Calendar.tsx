/**
 * 주요 일정 달력 — 지표 발표 예정일(FRED) · 연준 회의 · 유니버스 종목 실적 예정(Finnhub) (T276 · 2026-09-13).
 *
 * **달력 모양**이다 (사용자 2026-09-13: *"왼쪽 사이드바에서 캘린더 형태로"*). 한 달을 7열 격자로 그리고 날짜 칸에
 * 그날의 일정을 칩으로 싣는다. 칩을 누르면 격자 아래에 그 자리에서 펼쳐진다 — 팝업 없음. 크기(작게·보통·크게)는
 * 브라우저에 기억하고, `/calendar/popout` 은 사이드바 없이 격자만이라 새 탭에 띄운다.
 *
 * 🔴 **서버 시각으로 잰다.** 카운트다운·"발표 지남"·뉴욕/서울 시계는 전부 서버가 준 `clock` 에 브라우저의 경과
 *    시간만 더한 것이다 — 호스트 시계는 못 믿는다(2026-09 실측 · 재동기화 +1.1s · RTC +2일). 뉴욕 오프셋은 서버의
 *    tz DB 가 준다(서머타임).
 * 🔴 **예정일과 사실만 보여 준다. 방향은 말하지 않는다** (규칙 #2 · #11). 과거 반응 표는 "그날 값이 이랬고 그 뒤
 *    BTC·ETH 가 이만큼 움직였다" 는 사실이고, 묶음 비율은 표본 수와 같이 있다(30 미만은 회색 · 결정 #3).
 * ⛔ 못 받은 출처는 **이유와 함께** 남는다 — 조용히 빠지지 않는다(규칙 #8).
 *
 * 실적 칩은 저평가 후보 화면과 **같은 부품**(`FundamentalsDetail`)으로 재무를 보여 준다 — 새로 그리면 두 곳이 갈린다.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  calendarHistory,
  calendarUpcoming,
  type CalendarEvent,
  type CalendarHistory,
  type CalendarView,
} from "./api";
import { ErrorCard, num, when } from "./ui";
import { FundamentalsDetail } from "./ValueRanking";

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const MAX_DAYS = 90;
const NEAR_MS = 3 * 60 * 60 * 1000;
const POLL_MS = 30_000;
const SIZE_KEY = "calendar.size";

type Size = "s" | "m" | "l";
const SIZES: Record<Size, { label: string; cell: number; font: number; chips: number }> = {
  s: { label: "작게", cell: 52, font: 11, chips: 2 },
  m: { label: "보통", cell: 72, font: 12, chips: 3 },
  l: { label: "크게", cell: 112, font: 13, chips: 5 },
};

type Actual = CalendarView["actuals"][string];

/** Finnhub `hour` 를 말로. 모르는 값은 그대로 보여 준다 — 조용히 지우지 않는다. */
export function hourLabel(hour: string | number | null | undefined): string {
  if (hour === "bmo") return "장 전";
  if (hour === "amc") return "장 마감 후";
  if (hour === "dmh") return "장중";
  return hour ? String(hour) : "";
}

/** `YYYY-MM-DD` — 로컬 달력 날짜. 시간대 변환 없이 숫자만 붙인다. */
export function isoDay(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/**
 * 한 달의 격자 칸 — 일요일부터 시작해 7의 배수로 채운다. 이웃 달 칸은 `inMonth: false`.
 *
 * @param y 연도.
 * @param m 0 부터 세는 달 (JS Date 와 같다).
 */
export function monthCells(y: number, m: number): Array<{ iso: string; day: number; inMonth: boolean }> {
  const first = new Date(y, m, 1);
  const lead = first.getDay();
  const start = new Date(y, m, 1 - lead);
  const cells: Array<{ iso: string; day: number; inMonth: boolean }> = [];
  for (let i = 0; i < 42; i += 1) {
    const at = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    cells.push({
      iso: isoDay(at.getFullYear(), at.getMonth(), at.getDate()),
      day: at.getDate(),
      inMonth: at.getMonth() === m,
    });
    // 여섯째 줄이 통째로 다음 달이면 자른다.
    if (i === 34 && cells.slice(28).every((c) => !c.inMonth)) {
      return cells.slice(0, 28);
    }
  }
  return cells.slice(35).every((c) => !c.inMonth) ? cells.slice(0, 35) : cells;
}

/** 움직임을 말로 — 사실의 크기다. 예측이 아니다. */
export function moveWord(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "—";
  const size = Math.abs(pct);
  const sign = pct > 0 ? "상승" : "하락";
  const label = size < 0.3 ? "보합" : size < 1 ? `소폭 ${sign}` : `큰 폭 ${sign}`;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}% ${label}`;
}

/** 남은 시간을 `D일 HH:MM:SS` 로. 음수면 지난 시간. */
export function spanLabel(ms: number): string {
  const total = Math.floor(Math.abs(ms) / 1000);
  const d = Math.floor(total / 86_400);
  const h = Math.floor((total % 86_400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const hms = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return d > 0 ? `${d}일 ${hms}` : hms;
}

/** UTC ms 를 오프셋(분)만큼 옮겨 `HH:MM:SS` 로 — `Intl` 없이(실행 환경의 ICU 유무에 안 흔들린다). */
export function wallClock(utcMs: number, offsetMin: number, withDate = false): string {
  const iso = new Date(utcMs + offsetMin * 60_000).toISOString();
  return withDate ? `${iso.slice(0, 10)} ${iso.slice(11, 19)}` : iso.slice(11, 19);
}

function readSize(): Size {
  try {
    const got = localStorage.getItem(SIZE_KEY);
    return got === "s" || got === "m" || got === "l" ? got : "m";
  } catch {
    return "m";
  }
}

function writeSize(size: Size): void {
  try {
    localStorage.setItem(SIZE_KEY, size);
  } catch {
    // 브라우저 저장이 막혀 있어도 화면은 돈다.
  }
}

function Chip({
  item,
  selected,
  font,
  onClick,
}: {
  item: CalendarEvent;
  selected: boolean;
  font: number;
  onClick: () => void;
}) {
  const short = item.kind === "earnings" ? (item.symbol ?? item.title) : item.title.replace(/^미국 /, "");
  return (
    <button
      type="button"
      className={`chip ${item.kind === "earnings" ? "" : "live"}`}
      onClick={onClick}
      aria-pressed={selected}
      title={item.title}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        cursor: "pointer",
        fontSize: font,
        outline: selected ? "2px solid var(--cyan-edge)" : "none",
      }}
    >
      {short}
    </button>
  );
}

function HistoryTable({ table }: { table: CalendarHistory }) {
  if (table.reason) return <p className="faint">과거 반응 표: {table.reason}</p>;
  const n = table.aggregate.n;
  const enough = n >= table.min_sample;
  const held = table.aggregate.held_h1 ?? {};
  return (
    <>
      <p className={enough ? "" : "faint"}>
        2022년부터 {n}회{" "}
        {table.symbols.map((s) => (
          <span key={s}>
            · {s.replace("KRW-", "")} 첫 {table.first_minutes}분 방향이 {table.h1_minutes}분 뒤에도 같음{" "}
            <b>{held[s] === null || held[s] === undefined ? "—" : `${held[s]}%`}</b>
          </span>
        ))}
        {enough ? null : ` · ⚠️ 표본 ${table.min_sample} 미만 — 비율로 판단하지 않는다`}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>발표일</th>
              {table.value_label ? <th>{table.value_label}</th> : null}
              {table.symbols.map((s) => (
                <th key={s}>
                  {s.replace("KRW-", "")} 첫 {table.first_minutes}분 → {table.h1_minutes}분
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => (
              <tr key={row.date}>
                <td className="mono">{row.date}</td>
                {table.value_label ? (
                  <td className="mono">
                    {row.value === undefined ? "—" : `${num(row.value, 2)}${table.value_unit ?? ""}`}
                    {row.delta === undefined || row.delta === null ? "" : (
                      <span className="faint">
                        {" "}
                        (전달 {row.delta > 0 ? "+" : ""}
                        {num(row.delta, 2)}p)
                      </span>
                    )}
                  </td>
                ) : null}
                {table.symbols.map((s) => {
                  const mv = row.moves[s];
                  return (
                    <td key={s} className="mono">
                      {mv ? `${moveWord(mv.first)} → ${moveWord(mv.h1)}` : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="faint">
        값은 그날 나온 발표치(전년비·실업률)와 전달 대비 변화, 움직임은 발표 시각 봉의 시가 기준 %. 코인 15분봉으로 쟀고
        주식은 표본이 차면 붙인다. 이 표는 과거 사실이지 다음 발표의 방향이 아니다.
      </p>
    </>
  );
}

function Detail({
  item,
  actual,
  now,
  clock,
}: {
  item: CalendarEvent;
  actual?: Actual;
  now: number;
  clock: CalendarView["clock"] | null;
}) {
  const note = typeof item.detail.note === "string" ? item.detail.note : "";
  const hour = hourLabel(item.detail.hour);
  const eps = item.detail.eps_estimate;
  const epsActual = item.detail.eps_actual;
  const atMs = item.at ? Date.parse(item.at) : NaN;
  const remain = Number.isFinite(atMs) ? atMs - now : null;
  const [history, setHistory] = useState<CalendarHistory | null>(null);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    setHistory(null);
    setHistoryError("");
    if (!item.history) return;
    let alive = true;
    calendarHistory(item.history)
      .then((body) => alive && setHistory(body))
      .catch((exc: unknown) => alive && setHistoryError(String(exc)));
    return () => {
      alive = false;
    };
  }, [item.history]);

  return (
    <section>
      <h3>
        {item.date} · {item.title}{" "}
        <span className="faint">
          {item.url ? (
            <a href={item.url} target="_blank" rel="noreferrer">
              {item.source}
            </a>
          ) : (
            item.source
          )}
        </span>
      </h3>
      <p>
        {remain === null || clock === null ? (
          <span className="faint">발표 시각을 모른다 — 카운트다운 없음{hour ? ` (${hour})` : ""}</span>
        ) : remain > 0 ? (
          <>
            발표까지 <b className="mono">{spanLabel(remain)}</b>{" "}
            <span className="faint">
              · 뉴욕 {wallClock(atMs, clock.ny_offset_min, true)} {clock.ny_zone} · 서울{" "}
              {wallClock(atMs, clock.kst_offset_min, true)}
              {item.kind === "earnings" ? " · 회사마다 다르다(대략)" : ""}
            </span>
          </>
        ) : (
          <>
            <span className="chip live">발표 {spanLabel(remain)} 지남</span>{" "}
            <span className="faint">
              뉴욕 {wallClock(atMs, clock.ny_offset_min, true)} {clock.ny_zone} · 서울 {wallClock(atMs, clock.kst_offset_min, true)}
            </span>
          </>
        )}
      </p>
      {item.kind === "earnings" ? (
        <>
          <p className="faint">
            {hour ? `${hour}` : "시각 미정"}
            {typeof eps === "number" ? ` · EPS 예상 ${num(eps, 2)}` : ""}
            {/* 발표 뒤 Finnhub 가 실제값을 채운다 — 예상과 나란히. 방향이 아니라 사실이다(규칙 #2). */}
            {typeof epsActual === "number" ? (
              <>
                {" · "}
                <b>실제 {num(epsActual, 2)}</b>
              </>
            ) : null}
          </p>
          {item.symbol && item.market ? (
            <FundamentalsDetail symbol={item.symbol} market={item.market} />
          ) : (
            <p className="faint">시장을 모르는 종목이라 재무 카드를 열 수 없다.</p>
          )}
        </>
      ) : (
        <>
          {note ? <p className="faint">{note}</p> : null}
          {/* "이전 → 실제" 는 사실이고 방향이 아니다(규칙 #2). fresh 가 아니면 아직 전달 값이다. */}
          {actual ? (
            <p className={actual.fresh ? "" : "faint"}>
              {actual.fresh ? (
                <b>
                  실제 {actual.value}
                  {actual.unit}
                </b>
              ) : (
                `아직 전달 값 ${actual.value}${actual.unit} (BLS 갱신 대기)`
              )}
              {actual.note ? <span className="faint"> · {actual.note}</span> : null}
            </p>
          ) : null}
          {historyError ? <ErrorCard message={historyError} /> : null}
          {item.history ? (
            history ? (
              <HistoryTable table={history} />
            ) : (
              <p className="faint">과거 반응 표 읽는 중…</p>
            )
          ) : (
            <p className="faint">이 발표는 과거 반응 표가 아직 없다.</p>
          )}
        </>
      )}
    </section>
  );
}

function localToday(): { y: number; m: number } {
  const now = new Date();
  return { y: now.getFullYear(), m: now.getMonth() };
}

export function CalendarPage({ popout = false }: { popout?: boolean }) {
  const start = useMemo(localToday, []);
  const [cursor, setCursor] = useState(start);
  const [view, setView] = useState<CalendarView | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [size, setSize] = useState<Size>(readSize);
  const offsetRef = useRef(0);
  const [now, setNow] = useState(() => Date.now());

  const load = () =>
    // 서버는 오늘부터 앞으로만 준다 — 한 번에 90일을 받아 세 달치 격자를 채운다 (서버 30분 캐시 · actuals 는 밖).
    calendarUpcoming(MAX_DAYS)
      .then((body) => {
        offsetRef.current = Date.parse(body.clock.utc) - Date.now();
        setView(body);
        setError("");
      })
      .catch((exc: unknown) => setError(String(exc)));

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 1초 시계 — 서버 시각 = 브라우저 시각 + 오프셋.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() + offsetRef.current), 1000);
    return () => window.clearInterval(id);
  }, []);

  const picked = view?.events.find((e) => e.key === selected) ?? null;
  const pickedAt = picked?.at ? Date.parse(picked.at) : NaN;
  const near = Number.isFinite(pickedAt) && Math.abs(pickedAt - now) < NEAR_MS;

  // 발표 앞뒤 3시간은 30초마다 다시 읽는다 — 실제값(actuals)이 캐시 밖이라 나오는 즉시 보인다.
  useEffect(() => {
    if (!near) return;
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [near]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const item of view?.events ?? []) {
      const got = map.get(item.date) ?? [];
      got.push(item);
      map.set(item.date, got);
    }
    return map;
  }, [view]);

  const cells = useMemo(() => monthCells(cursor.y, cursor.m), [cursor]);
  const serverToday = view ? view.clock.utc.slice(0, 10) : null;
  const monthLabel = `${cursor.y}년 ${cursor.m + 1}월`;
  const isCurrent = cursor.y === start.y && cursor.m === start.m;
  const firstCell = cells[0];
  const beyond = firstCell !== undefined && view !== null && firstCell.iso > view.to;
  const dims = SIZES[size];
  const clock = view?.clock ?? null;

  const move = (delta: number) => {
    const at = new Date(cursor.y, cursor.m + delta, 1);
    setCursor({ y: at.getFullYear(), m: at.getMonth() });
    setSelected(null);
  };

  return (
    <div className="page">
      <section>
        <h2>주요 일정 달력</h2>
        {clock ? (
          <p className="mono">
            서버 시각 · 뉴욕 {wallClock(now, clock.ny_offset_min, true)} {clock.ny_zone} · 서울{" "}
            {wallClock(now, clock.kst_offset_min, true)} KST
          </p>
        ) : null}
        <p className="faint">
          지표 발표 예정일 · 연준 회의 · 유니버스 종목의 실적 예정. <b>예정일과 사실만 보여 주고 방향은 말하지 않는다.</b>{" "}
          칩을 누르면 아래에 카운트다운·과거 반응·재무가 펼쳐진다.
        </p>
        <p>
          <button type="button" className="chip" onClick={() => move(-1)} style={{ cursor: "pointer" }}>
            ◀ 지난달
          </button>{" "}
          <b>{monthLabel}</b>{" "}
          <button type="button" className="chip" onClick={() => move(1)} style={{ cursor: "pointer" }}>
            다음달 ▶
          </button>
          {!isCurrent ? (
            <>
              {" "}
              <button
                type="button"
                className="chip live"
                onClick={() => {
                  setCursor(start);
                  setSelected(null);
                }}
                style={{ cursor: "pointer" }}
              >
                오늘로
              </button>
            </>
          ) : null}
          {" · "}
          {(Object.keys(SIZES) as Size[]).map((key) => (
            <button
              key={key}
              type="button"
              className={`chip ${size === key ? "live" : ""}`}
              onClick={() => {
                setSize(key);
                writeSize(key);
              }}
              style={{ cursor: "pointer", marginRight: 2 }}
            >
              {SIZES[key].label}
            </button>
          ))}
          {!popout ? (
            <>
              {" · "}
              <a href="/calendar/popout" target="_blank" rel="noreferrer" className="chip">
                새 탭으로 ↗
              </a>
            </>
          ) : null}
          {view ? (
            <span className="faint">
              {" "}
              · 범위 {view.from} ~ {view.to} · 받은 시각 {when(view.at)}
            </span>
          ) : null}
        </p>
      </section>
      {error && <ErrorCard message={error} />}
      {view?.failures.length ? (
        <section>
          <h3>못 받은 출처</h3>
          <ul>
            {view.failures.map((f) => (
              <li key={f.key}>
                <b>{f.label}</b> <span className="faint">— {f.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {beyond ? (
        <p className="faint">서버는 오늘부터 {MAX_DAYS}일까지만 준다 — 이 달은 아직 비어 있다.</p>
      ) : null}
      <section>
        <div
          role="grid"
          aria-label={monthLabel}
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
            gap: 1,
            background: "var(--stone-border)",
            border: "1px solid var(--stone-border)",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          {WEEKDAY.map((w, i) => (
            <div
              key={w}
              role="columnheader"
              className="faint"
              style={{
                background: "var(--stone-canvas)",
                padding: "4px 6px",
                fontSize: dims.font,
                textAlign: "center",
                color: i === 0 ? "var(--loss)" : i === 6 ? "var(--cyan-edge)" : undefined,
              }}
            >
              {w}
            </div>
          ))}
          {cells.map((cell) => {
            const items = byDay.get(cell.iso) ?? [];
            const isToday = serverToday !== null && cell.iso === serverToday;
            const past = serverToday !== null && cell.iso < serverToday;
            return (
              <div
                key={cell.iso}
                role="gridcell"
                style={{
                  background: "var(--pure-white)",
                  minHeight: dims.cell,
                  padding: 4,
                  opacity: cell.inMonth ? 1 : 0.45,
                }}
              >
                <div
                  className={past ? "faint" : ""}
                  style={{
                    fontSize: dims.font,
                    fontWeight: isToday ? 700 : 400,
                    color: isToday ? "var(--cyan-edge)" : undefined,
                  }}
                >
                  {cell.day}
                  {isToday ? <span className="faint"> 오늘</span> : null}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
                  {items.slice(0, dims.chips).map((item) => (
                    <Chip
                      key={item.key}
                      item={item}
                      font={dims.font}
                      selected={selected === item.key}
                      onClick={() => setSelected(selected === item.key ? null : item.key)}
                    />
                  ))}
                  {items.length > dims.chips ? (
                    <button
                      type="button"
                      className="faint"
                      onClick={() => setSelected(items[dims.chips]?.key ?? null)}
                      style={{ fontSize: dims.font - 1, textAlign: "left", background: "none", border: 0, padding: 0, cursor: "pointer" }}
                    >
                      +{items.length - dims.chips}
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </section>
      {picked ? <Detail item={picked} actual={view?.actuals?.[picked.key]} now={now} clock={clock} /> : null}
      {view && !view.events.length && !view.failures.length ? (
        <p className="faint">앞으로 {MAX_DAYS}일 안에 감시 중인 일정이 없다.</p>
      ) : null}
      {view ? (
        <section>
          <h3>감시 중인 발표</h3>
          <p className="faint">
            {view.watch.map((w) => (
              <span key={w.key}>
                <b>{w.label}</b>
                {w.note ? ` — ${w.note}` : ""}
                {w.url ? (
                  <>
                    {" "}
                    <a href={w.url} target="_blank" rel="noreferrer">
                      출처
                    </a>
                  </>
                ) : null}
                {" · "}
              </span>
            ))}
            여기 없는 발표는 안 나온다 — <span className="mono">config/calendar.yml</span> 이 정한다.
          </p>
        </section>
      ) : null}
    </div>
  );
}
