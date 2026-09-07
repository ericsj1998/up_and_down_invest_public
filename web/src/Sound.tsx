/**
 * 음량 설정 — **화면을 안 보고 있을 때 매매를 알려 준다** (사용자 요구 2026-08-20).
 *
 * 🔴 **거래소 체결이 울린다, 원장이 아니다.** 원장이 거짓말한 적이 있고(2026-08-19:
 * 승률 100% 라고 3시간), 그때 소리까지 원장을 따라 울렸으면 사람은 잘 되고 있다고
 * **귀로도** 믿었을 것이다.
 */

import { useEffect, useRef, useState } from "react";
import type { Exchange } from "./api";
import {
  audible,
  freshFills,
  loudest,
  loadSettings,
  locked,
  play,
  ringOf,
  saveSettings,
  unlock,
  type SoundSettings,
} from "./sound";

/** 0~23 고르개 — 시각 두 칸이 같은 모양이어야 한다. */
function Hour({
  value,
  onPick,
  label,
}: {
  value: number;
  onPick: (hour: number) => void;
  label: string;
}) {
  return (
    <label className="row" style={{ gap: "4px", alignItems: "center" }}>
      <span className="card-hint">{label}</span>
      {/* "시" 는 select 밖에 (사용자 2026-09-06) — 선택지는 숫자만, 단위는 고정 글자. */}
      <select
        className="input"
        style={{ width: "64px" }}
        value={value}
        onChange={(event) => onPick(Number(event.target.value))}
      >
        {Array.from({ length: 24 }, (_, hour) => (
          <option key={hour} value={hour}>
            {String(hour).padStart(2, "0")}
          </option>
        ))}
      </select>
      <span className="card-hint">시</span>
    </label>
  );
}

export function Sound({
  history,
  alarm = false,
  scope = "",
}: {
  /**
   * **누구의 이력인가** — 모드(데모/실계좌) · 거래소 목록. 이 값이 바뀌면 기억을 비우고 다시 심는다.
   *
   * 🔴 사용자 신고 2026-09-06: *"데모 → 실계좌 → 데모로 바꾸면 알림이 울린다."* 다른 계좌의 이력이
   * 오면 전부 "새것" 이라 가장 무거운 한 건이 울렸다. 계좌가 바뀐 것은 체결이 아니다.
   */
  scope?: string;
  /**
   * 거래소 체결 이력. **`null` 은 "아직 안 왔다"** 이고 `[]` 는 "비어 있다" 이다.
   *
   * 🔴 둘을 같이 두면 새로고침마다 이력 전체가 울린다 (사용자 신고 2026-08-20) —
   * 첫 렌더는 응답 전이라 `[]` 이고, 그것으로 심으면 곧 도착하는 수십 건이 전부
   * "새것" 이 된다.
   */
  history: Exchange["history"] | null;
  /**
   * **지금 위험한 상태인가** — 손절 없는 포지션 같은 것.
   *
   * 🔴 화면을 안 보고 있을 때 가장 들어야 할 소리다. 체결음은 *"일이 났다"* 지만
   * 이것은 *"지금 무방비다"* 이고, 둘을 같은 소리로 내면 구별이 안 된다.
   */
  alarm?: boolean;
}) {
  const [value, setValue] = useState<SoundSettings>(loadSettings);
  const [shut, setShut] = useState(false);
  // ⚠️ **뜨는 순간에만** 울린다. 매 폴링마다 울리면 4초마다 경보가 나고, 그러면
  //    사람이 소리를 꺼 버린다 — 끄면 진짜일 때도 못 듣는다.
  const rang = useRef(false);

  // 🔴 **이미 있던 체결로 울리지 않는다.** 화면을 열면 이력 수십 건이 한꺼번에
  //    "새것" 이 되는데, 그때 다 울리면 소리가 뜻을 잃는다.
  //
  // ⚠️ 그래서 첫 폴링은 **심는 것**이고 울리지 않는다. `null` 이 그 표식이다.
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => saveSettings(value), [value]);

  // ⭐ 잠금 상태는 화면이 말해야 한다 — 켰다고 믿는데 안 나면 버그로 읽는다.
  useEffect(() => {
    const timer = setInterval(() => setShut(locked()), 2000);
    return () => clearInterval(timer);
  }, []);

  // 범위(모드 · 거래소)가 바뀌면 다음 응답은 **심는 것**이다 — 새 계좌의 지난 이력이 울리면 안 된다.
  useEffect(() => {
    seen.current = null;
  }, [scope]);

  useEffect(() => {
    // 🔴 **"아직 안 왔다" 로는 심지 않는다** (사용자 신고 2026-08-20: *"새로고침할때마다
    //    기존 주문들이 모두 사운드가 재생돼"*). 판정은 `freshFills` 가 한다 — 화면이
    //    아니라 순수 함수에 두어야 시험이 볼 수 있다.
    const step = freshFills(history, seen.current);
    if (step === null) return;
    // ⚠️ **소리를 끈 상태에서도 기억한다.** 안 그러면 켜는 순간 그동안의 것이 몰려 운다.
    seen.current = step.seen;
    if (!audible(value)) return;
    // 🔴 **한 폴링에 한 번만 울린다** (사용자 신고 2026-08-29: *"밀려 있던 체결이 모두
    //    재생된다"*). 탭을 백그라운드에 두면 브라우저가 폴링을 늦추고, 돌아오는 순간
    //    한 응답에 수십 건이 들어온다 — 예전에는 그것을 180ms 간격으로 전부 울렸다.
    //
    // ⛔ 고르는 기준은 **최신이 아니라 무게**다 (`loudest`). 마지막 것을 울리면 손절이
    //    섞인 묶음에서 진입 소리만 나고 손절은 조용히 지나간다.
    const one = loudest(step.ring);
    if (one) play(ringOf(one), value.volume);
  }, [history, value]);

  useEffect(() => {
    if (!alarm) {
      rang.current = false;
      return;
    }
    if (rang.current) return;
    rang.current = true;
    // ⭐ **야간에도 울린다.** 무방비 포지션은 자는 동안 더 위험하다 — 음소거의 뜻은
    //   *"매매 알림이 성가시다"* 이지 *"위험을 알리지 말라"* 가 아니다.
    if (value.on) play("alarm", value.volume);
  }, [alarm, value.on, value.volume]);

  const quiet = value.on && !audible(value);

  return (
    <section className="card wide">
      <h2 className="section-title">음량</h2>
      <p className="card-hint">
        <b>거래소 체결</b>이 울린다 — 원장이 아니다. 슬롯머신 <b>챠킹</b>{" "}
        계열이고 익절은 올라가고 손절은 내려간다. 손익을 못 읽은 청산은{" "}
        <b>제자리 벨 하나</b>다 — 나간 것은 사실이고 방향은 아직 아니다.{" "}
        <b>손절 없는 포지션</b>은 경보음이다 (야간에도 울린다).
      </p>

      <div className="row" style={{ gap: "10px", alignItems: "center" }}>
        <button
          className={value.on ? "btn small picked" : "btn small"}
          type="button"
          onClick={() => {
            // ⭐ 켜는 그 클릭으로 잠금을 푼다 — 따로 누르게 하면 안 눌러 본다.
            void unlock().then(() => setShut(locked()));
            setValue((was) => ({ ...was, on: !was.on }));
          }}
        >
          {value.on ? "소리 켜짐" : "소리 꺼짐"}
        </button>

        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={value.volume}
          disabled={!value.on}
          onChange={(event) =>
            setValue((was) => ({ ...was, volume: Number(event.target.value) }))
          }
          style={{ width: "160px" }}
        />
        <span className="card-hint mono">{value.volume}</span>

        {/* ⭐ **넷을 다 들려준다** — 하나만 나면 소리끼리 구별되는지 알 수 없고,
            그것이 이 기능의 전부다 (진입인지 손절인지 귀로 갈려야 한다). */}
        {(
          [
            ["entry", "진입"],
            ["gain", "익절"],
            ["loss", "손절"],
            // ⭐ **손익을 모르는 청산.** Gate 가 부분 체결된 청산에 pnl 을 안 준다 —
            //    예전에는 이것이 **진입 소리**로 났다 (실측 200건 중 21건).
            ["exit", "청산(모름)"],
            ["alarm", "경보"],
          ] as const
        ).map(([ring, label]) => (
          <button
            key={ring}
            className="btn small"
            type="button"
            disabled={!value.on}
            onClick={() => {
              void unlock().then(() => {
                setShut(locked());
                play(ring, value.volume);
              });
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── 야간 음소거 ─────────────────────────────────── */}
      <div
        className="row"
        style={{ gap: "10px", alignItems: "center", marginTop: "10px" }}
      >
        <button
          className={value.night ? "btn small picked" : "btn small"}
          type="button"
          onClick={() => setValue((was) => ({ ...was, night: !was.night }))}
        >
          {value.night ? "야간 음소거 켜짐" : "야간 음소거 꺼짐"}
        </button>
        {/* ⚠️ 시간대는 **바꿀 수 있어야 한다** (사용자 요구) — 기본 0~8시가 모두에게
            맞지는 않고, 박아 두면 코드를 고쳐야 바뀐다. */}
        <Hour
          label="부터"
          value={value.from}
          onPick={(hour) => setValue((was) => ({ ...was, from: hour }))}
        />
        <Hour
          label="까지"
          value={value.to}
          onPick={(hour) => setValue((was) => ({ ...was, to: hour }))}
        />
        <span className="card-hint">한국시</span>
      </div>

      {/* 🔴 **지금 왜 조용한지 말한다.** 안 말하면 켜 뒀는데 안 울리는 것을 고장으로
          읽는다 (절대 규칙 #8). */}
      {quiet ? (
        <p className="notice warn" style={{ marginTop: "10px" }}>
          지금은 <b>야간 음소거</b> 구간이다 (
          {String(value.from).padStart(2, "0")}
          시~{String(value.to).padStart(2, "0")}시 한국시) — 매매는 그대로 돈다.
        </p>
      ) : null}
      {value.on && shut ? (
        <p className="notice bad" style={{ marginTop: "10px" }}>
          🔴 브라우저가 소리를 막고 있다 — 위 <b>익절</b> 단추를 한 번 누르면
          풀린다. (사람이 한 번 누르기 전에는 소리를 못 낸다)
        </p>
      ) : null}
      {value.from === value.to && value.night ? (
        <p className="notice warn" style={{ marginTop: "10px" }}>
          ⚠️ 시작과 끝이 같아 <b>음소거 구간이 없다</b> — 야간 음소거가 아무
          일도 안 한다.
        </p>
      ) : null}
    </section>
  );
}
