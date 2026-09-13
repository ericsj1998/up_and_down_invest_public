/**
 * 주요 일정 달력 — 지표 발표 예정일(FRED) · 연준 회의 · 유니버스 종목 실적 예정(Finnhub) (T276 · 2026-09-13).
 *
 * **달력 모양**이다 (사용자 2026-09-13: *"왼쪽 사이드바에서 캘린더 형태로"*). 한 달을 7열 격자로 그리고 날짜 칸에
 * 그날의 일정을 칩으로 싣는다. 칩을 누르면 격자 아래에 그 자리에서 펼쳐진다 — 팝업 없음.
 *
 * 🔴 **예정일만 보여 준다. 방향은 말하지 않는다** (규칙 #2 · #11). "오르면 하락 가능성" 같은 문장은 화면 어디에도
 *    없다 — 그 자리는 과거 빈도 표가 맡을 것이고, 표본 하한(30)을 어떻게 다룰지 정한 뒤에 붙는다(T276 결정 #3).
 * ⛔ 못 받은 출처는 **이유와 함께** 아래에 남는다 — 조용히 빠지지 않는다(규칙 #8).
 *
 * 실적 칩은 저평가 후보 화면과 **같은 부품**(`FundamentalsDetail`)으로 재무를 보여 준다 — 새로 그리면 두 곳이 갈린다.
 * 서버는 오늘부터 앞으로만 준다(최대 90일). 지난 날짜 칸은 비어 있고 그렇게 말한다.
 */

import { useEffect, useMemo, useState } from "react";
import { calendarUpcoming, type CalendarEvent, type CalendarView } from "./api";
import { ErrorCard, num, when } from "./ui";
import { FundamentalsDetail } from "./ValueRanking";

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const MAX_DAYS = 90;
const CHIPS_PER_CELL = 3;

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

function localToday(): { y: number; m: number; iso: string } {
  const now = new Date();
  return { y: now.getFullYear(), m: now.getMonth(), iso: isoDay(now.getFullYear(), now.getMonth(), now.getDate()) };
}

function Chip({ item, selected, onClick }: { item: CalendarEvent; selected: boolean; onClick: () => void }) {
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
        outline: selected ? "2px solid var(--cyan-edge)" : "none",
      }}
    >
      {short}
    </button>
  );
}

function Detail({ item, actual }: { item: CalendarEvent; actual?: Actual }) {
  const note = typeof item.detail.note === "string" ? item.detail.note : "";
  const hour = hourLabel(item.detail.hour);
  const eps = item.detail.eps_estimate;
  const epsActual = item.detail.eps_actual;
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
        <p className="faint">
          {note ? `${note}. ` : ""}
          {/* "이전 → 실제" 는 사실이고 방향이 아니다(규칙 #2). fresh 가 아니면 아직 전달 값이다. */}
          {actual ? (
            <span className={actual.fresh ? "" : "faint"}>
              {actual.fresh ? (
                <b>
                  실제 {actual.value}
                  {actual.unit}
                </b>
              ) : (
                `아직 전달 값 ${actual.value}${actual.unit} (BLS 갱신 대기)`
              )}
              {actual.note ? ` · ${actual.note}` : ""}.{" "}
            </span>
          ) : null}
          과거 이 발표 뒤 시장이 어느 쪽으로 얼마나 움직였는지는 아직 세지 않았다 — 표본이 30 미만일 때를 어떻게 다룰지
          정한 뒤 붙는다. 방향은 이 화면이 말하지 않는다.
        </p>
      )}
    </section>
  );
}

export function CalendarPage() {
  const today = useMemo(localToday, []);
  const [cursor, setCursor] = useState({ y: today.y, m: today.m });
  const [view, setView] = useState<CalendarView | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // 서버는 오늘부터 앞으로만 준다 — 한 번에 90일을 받아 세 달치 격자를 채운다 (서버 30분 캐시).
    calendarUpcoming(MAX_DAYS)
      .then((body) => alive && setView(body))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, []);

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
  const picked = view?.events.find((e) => e.key === selected) ?? null;
  const monthLabel = `${cursor.y}년 ${cursor.m + 1}월`;
  const isCurrent = cursor.y === today.y && cursor.m === today.m;
  const lastCovered = view?.to ?? "";
  const firstCell = cells[0];
  const beyond = firstCell !== undefined && lastCovered !== "" && firstCell.iso > lastCovered;

  const move = (delta: number) => {
    const at = new Date(cursor.y, cursor.m + delta, 1);
    setCursor({ y: at.getFullYear(), m: at.getMonth() });
    setSelected(null);
  };

  return (
    <div className="page">
      <section>
        <h2>주요 일정 달력</h2>
        <p className="faint">
          지표 발표 예정일 · 연준 회의 · 유니버스 종목의 실적 예정. <b>예정일만 보여 주고 방향은 말하지 않는다.</b>{" "}
          칩을 누르면 아래에 펼쳐진다. 실적은 그 종목의 재무(PER·PBR·5년 백분위)로 이어진다.
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
                  setCursor({ y: today.y, m: today.m });
                  setSelected(null);
                }}
                style={{ cursor: "pointer" }}
              >
                오늘로
              </button>
            </>
          ) : null}
          {view ? (
            <span className="faint">
              {" "}
              · 서버가 준 범위 {view.from} ~ {view.to} · 받은 시각 {when(view.at)}
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
                fontSize: 12,
                textAlign: "center",
                color: i === 0 ? "var(--loss)" : i === 6 ? "var(--cyan-edge)" : undefined,
              }}
            >
              {w}
            </div>
          ))}
          {cells.map((cell) => {
            const items = byDay.get(cell.iso) ?? [];
            const isToday = cell.iso === today.iso;
            const past = cell.iso < today.iso;
            return (
              <div
                key={cell.iso}
                role="gridcell"
                style={{
                  background: "var(--pure-white)",
                  minHeight: 72,
                  padding: 4,
                  opacity: cell.inMonth ? 1 : 0.45,
                }}
              >
                <div
                  className={past ? "faint" : ""}
                  style={{
                    fontSize: 12,
                    fontWeight: isToday ? 700 : 400,
                    color: isToday ? "var(--cyan-edge)" : undefined,
                  }}
                >
                  {cell.day}
                  {isToday ? <span className="faint"> 오늘</span> : null}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
                  {items.slice(0, CHIPS_PER_CELL).map((item) => (
                    <Chip
                      key={item.key}
                      item={item}
                      selected={selected === item.key}
                      onClick={() => setSelected(selected === item.key ? null : item.key)}
                    />
                  ))}
                  {items.length > CHIPS_PER_CELL ? (
                    <button
                      type="button"
                      className="faint"
                      onClick={() => setSelected(items[CHIPS_PER_CELL]?.key ?? null)}
                      style={{ fontSize: 11, textAlign: "left", background: "none", border: 0, padding: 0, cursor: "pointer" }}
                    >
                      +{items.length - CHIPS_PER_CELL}
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </section>
      {picked ? <Detail item={picked} actual={view?.actuals?.[picked.key]} /> : null}
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
