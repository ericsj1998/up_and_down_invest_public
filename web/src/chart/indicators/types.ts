/**
 * 차트 위에 겹치는 분석 지표 — **계약**만 (T222 §5 · 사용자 요구 2026-09-06).
 *
 * 책임 분리:
 *   - 지표 하나 = 파일 하나 (`movingAverage.ts` · `bollinger.ts`). 봉을 받아 **선 묶음**을 돌려준다. 그 외엔 모른다 —
 *     색·굵기 같은 **그리는 법**은 돌려주는 시리즈에 적고, **어떻게 캔버스에 올리는지**는 `overlays.ts` 가 한다.
 *   - 어떤 지표가 있는지는 `registry.ts` 만 안다. 새 지표 = 파일 1개 + 레지스트리 1줄 (CLAUDE.md §1).
 *   - 사용자가 무엇을 켜 두었는지는 `settings.ts` 가 든다 (브라우저에 남는다).
 *
 * ⚠️ **화면용이다.** 판정이 쓰는 SMA200 · ADX 는 서버가 세션의 그 함수로 재서 보낸다(`adx.ts` 머리말). 여기서
 * 그리는 지표는 사람이 보려고 겹치는 것이고, 매매 로직과 값이 같다는 보장이 없다 — 라벨에 "표시용" 을 남긴다.
 */

/** 봉 하나 — 시각은 epoch **초**(lightweight-charts 의 UTCTimestamp 와 같은 단위). */
export interface Ohlc {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface Point {
  time: number;
  value: number;
}

/** 지표가 돌려주는 선 하나 — 그리는 쪽은 이것만 보고 시리즈를 만든다. */
export interface OverlaySeries {
  /** 시리즈 식별자 (지표 id + 역할). 같은 키면 같은 선으로 갱신한다. */
  key: string;
  /** 범례에 적는 이름 ("SMA 20" · "BB 20/2 상단"). */
  label: string;
  color: string;
  width: 1 | 2;
  dashed?: boolean;
  points: Point[];
}

/** 지표 — 봉 배열을 받아 선 묶음으로. 상태가 없고 같은 입력엔 같은 출력이다. */
export interface Indicator {
  readonly id: string;
  readonly label: string;
  compute(bars: readonly Ohlc[]): OverlaySeries[];
}
