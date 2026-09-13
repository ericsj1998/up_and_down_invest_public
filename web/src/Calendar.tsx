/**
 * 주요 일정 달력 — 지표 발표 예정일(FRED) · 연준 회의 · 유니버스 종목 실적 예정(Finnhub) (T276 · 2026-09-13).
 *
 * 🔴 **예정일만 보여 준다. 방향은 말하지 않는다** (규칙 #2 · #11). "오르면 하락 가능성" 같은 문장은 화면 어디에도
 *    없다 — 그 자리는 과거 빈도 표가 맡을 것이고, 표본 하한(30)을 어떻게 다룰지 정한 뒤에 붙는다(T276 결정 #3).
 * ⛔ 못 받은 출처는 **이유와 함께** 아래에 남는다 — 조용히 빠지지 않는다(규칙 #8).
 *
 * 행을 누르면 그 자리에서 펼쳐진다. 실적 행은 저평가 후보 화면과 **같은 부품**(`FundamentalsDetail`)으로 재무를
 * 보여 준다 — 새로 그리면 두 곳이 갈린다. 팝업 없음.
 */

import { useEffect, useState } from "react";
import { calendarUpcoming, type CalendarEvent, type CalendarView } from "./api";
import { ErrorCard, num, when } from "./ui";
import { FundamentalsDetail } from "./ValueRanking";

const WINDOWS = [14, 30, 60, 90] as const;
const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

/** Finnhub `hour` 를 말로. 모르는 값은 그대로 보여 준다 — 조용히 지우지 않는다. */
export function hourLabel(hour: string | number | null | undefined): string {
  if (hour === "bmo") return "장 전";
  if (hour === "amc") return "장 마감 후";
  if (hour === "dmh") return "장중";
  return hour ? String(hour) : "";
}

/** `2026-10-14` → `10-14 (수)`. 출처의 현지(미국) 날짜다 — 시간대 변환 없이 글자만 자른다. */
export function dayLabel(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00Z`);
  const weekday = Number.isNaN(parsed.getTime()) ? "" : ` (${WEEKDAY[parsed.getUTCDay()]})`;
  return `${iso.slice(5)}${weekday}`;
}

/** 오늘·이번 주·이후 — 사용자 요청 순서("오늘 것이 먼저, 이번 주가 그다음"). */
export function bucketOf(iso: string, today: string): "오늘" | "이번 주" | "이후" {
  if (iso === today) return "오늘";
  const gap = (Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000;
  return gap > 0 && gap < 7 ? "이번 주" : "이후";
}

function localToday(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

type Actual = CalendarView["actuals"][string];

function Row({
  item,
  actual,
  open,
  onToggle,
}: {
  item: CalendarEvent;
  actual?: Actual;
  open: boolean;
  onToggle: () => void;
}) {
  const note = typeof item.detail.note === "string" ? item.detail.note : "";
  const hour = hourLabel(item.detail.hour);
  const eps = item.detail.eps_estimate;
  // 발표 뒤 Finnhub 가 실제값을 채운다 — 예상과 나란히. 방향이 아니라 사실이다(규칙 #2).
  const epsActual = item.detail.eps_actual;
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }} aria-expanded={open}>
        <td className="mono">{dayLabel(item.date)}</td>
        <td>
          <span className={`chip ${item.kind === "earnings" ? "" : "live"}`}>
            {item.kind === "earnings" ? "실적" : "지표"}
          </span>
        </td>
        <td>
          <b>{item.title}</b>
          {item.kind === "earnings" ? (
            <span className="faint">
              {hour ? ` · ${hour}` : ""}
              {typeof eps === "number" ? ` · EPS 예상 ${num(eps, 2)}` : ""}
              {typeof epsActual === "number" ? (
                <>
                  {" · "}
                  <b>실제 {num(epsActual, 2)}</b>
                </>
              ) : (
                ""
              )}
            </span>
          ) : note ? (
            <span className="faint"> · {note}</span>
          ) : null}
          {/* 발표 뒤 실제값 — "이전 → 실제" 는 사실이고 방향이 아니다(규칙 #2). fresh 가 아니면 아직 전달 값이다. */}
          {actual ? (
            <span className={actual.fresh ? "" : "faint"}>
              {" · "}
              {actual.fresh ? <b>실제 {actual.value}{actual.unit}</b> : `아직 전달 값 ${actual.value}${actual.unit} (BLS 갱신 대기)`}
              {actual.note ? <span className="faint"> · {actual.note}</span> : null}
            </span>
          ) : null}
        </td>
        <td className="faint">
          {item.url ? (
            <a href={item.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
              {item.source}
            </a>
          ) : (
            item.source
          )}
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={4}>
            {item.kind === "earnings" && item.symbol ? (
              item.market ? (
                <FundamentalsDetail symbol={item.symbol} market={item.market} />
              ) : (
                <p className="faint">시장을 모르는 종목이라 재무 카드를 열 수 없다.</p>
              )
            ) : (
              <p className="faint">
                {note ? `${note}. ` : ""}
                과거 이 발표 뒤 시장이 어느 쪽으로 얼마나 움직였는지는 아직 세지 않았다 — 표본이 30 미만일 때를
                어떻게 다룰지 정한 뒤 붙는다. 방향은 이 화면이 말하지 않는다.
              </p>
            )}
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function CalendarPage() {
  const [days, setDays] = useState<number | undefined>(undefined);
  const [view, setView] = useState<CalendarView | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError("");
    calendarUpcoming(days)
      .then((body) => alive && setView(body))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [days]);

  const today = localToday();
  const buckets: Array<["오늘" | "이번 주" | "이후", CalendarEvent[]]> = [
    ["오늘", []],
    ["이번 주", []],
    ["이후", []],
  ];
  for (const item of view?.events ?? []) {
    const name = bucketOf(item.date, today);
    buckets.find(([b]) => b === name)?.[1].push(item);
  }

  return (
    <div className="page">
      <section>
        <h2>주요 일정 달력</h2>
        <p className="faint">
          지표 발표 예정일 · 연준 회의 · 유니버스 종목의 실적 예정. <b>예정일만 보여 주고 방향은 말하지 않는다.</b>{" "}
          실적 행을 누르면 그 종목의 재무(PER·PBR·5년 백분위)가 열린다.
        </p>
        <p>
          창{" "}
          {WINDOWS.map((n) => (
            <button
              key={n}
              type="button"
              className={`chip ${(view?.days ?? 0) === n ? "live" : ""}`}
              onClick={() => setDays(n)}
              style={{ marginRight: 4 }}
            >
              {n}일
            </button>
          ))}
          {view ? (
            <span className="faint">
              {" "}
              · {view.from} ~ {view.to} · 받은 시각 {when(view.at)}
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
      {view && !view.events.length && !view.failures.length ? (
        <p className="faint">이 창에는 감시 중인 일정이 없다.</p>
      ) : null}
      {buckets
        .filter(([, items]) => items.length)
        .map(([name, items]) => (
          <section key={name}>
            <h3>
              {name} <span className="faint">{items.length}</span>
            </h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>날짜</th>
                    <th>종류</th>
                    <th>일정</th>
                    <th>출처</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <Row
                      key={item.key}
                      item={item}
                      actual={view?.actuals?.[item.key]}
                      open={open === item.key}
                      onToggle={() => setOpen(open === item.key ? null : item.key)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}
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
