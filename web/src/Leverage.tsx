/**
 * 도는 RUN 의 **배율을 바꾼다** (사용자 요구 2026-08-19).
 *
 * > *"나는 청산을 유도하기 위해 이제 고레버리지로 일부 run 을 굴릴 예정이야."*
 *
 * ⛔ **보유 중에는 못 바꾼다.** 원장은 배율을 손익률에 그대로 곱하는데, 열린 매매의
 * 배율을 도중에 바꾸면 **이미 지나간 구간의 손익까지 새 배율로** 계산된다 — 그 매매의
 * 성적이 통째로 거짓이 된다. 서버가 409 로 막고, 화면은 **누르기 전에** 말한다.
 *
 * 🔴 **올리면 청산가가 진입에 가까워진다.** 이것은 리스크를 늘리는 방향이라 조용히
 * 넘어가지 않는다 — 바꾼 값을 화면과 로그에 함께 남긴다.
 */

import { useState } from "react";
import { setLeverage } from "./api";

type Props = {
  run: string;
  value: number;
  /** 보유 중인가 — 그러면 못 바꾼다. */
  held: boolean;
  /** 바뀐 뒤 목록을 다시 당긴다. */
  done: () => void;
};

export function Leverage({ run, value, held, done }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!editing) {
    return (
      <span className="lev">
        <b>{value}x</b>
        <button
          type="button"
          className="why"
          disabled={held}
          title={
            held
              ? "보유 중에는 못 바꾼다 — 열린 매매의 배율을 바꾸면 이미 지나간 구간의 손익까지 새 배율로 계산된다"
              : "다음 매매부터 새 배율이다"
          }
          onClick={() => {
            setDraft(String(value));
            setError("");
            setEditing(true);
          }}
        >
          배율 바꾸기
        </button>
        {error ? <span className="loss"> {error}</span> : null}
      </span>
    );
  }

  return (
    <span className="lev">
      <input
        className="lev-input"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        aria-label="레버리지"
      />
      <button
        type="button"
        className="btn small primary"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError("");
          setLeverage(run, Number(draft))
            .then(() => {
              setEditing(false);
              done();
            })
            .catch((exc: unknown) => setError(String(exc).slice(0, 120)))
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "…" : "적용"}
      </button>
      <button type="button" className="btn small" onClick={() => setEditing(false)}>
        취소
      </button>
      {/* 🔴 올리는 것은 리스크를 늘리는 행동이다 — 누르기 전에 말한다. */}
      <span className="faint">
        올리면 청산가가 진입에 가까워진다 · 이미 끝난 매매의 손익은 그때 배율 그대로다
      </span>
      {error ? <span className="loss"> {error}</span> : null}
    </span>
  );
}
