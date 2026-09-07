/**
 * 분석 도형 읽기 — 서버가 만든 것을 **읽기만** 한다 (2026-08-30 신규).
 *
 * 사용자 요구: *"추세, 박스권, 지지 저항, 델타 볼륨, 이평선, BB 등을 차트에서 빠르게
 * 분석할 수 있게 보여주는 기능."*
 *
 * ## 🟢 서버는 이미 다 만들고 있었다
 *
 * `/admin/inspect` 가 플래그를 받아 도형을 돌려준다 — 추세선·채널·박스·박스권·스윙·
 * 이평·ATR·RSI·거래량까지. **화면이 그중 `structure.box_range` 하나만 읽고 있었다.**
 * 그래서 이 작업은 새로 만드는 것이 아니라 **연결하는 것**이다.
 *
 * ⛔ **여기서 계산하지 않는다** (규칙 #9). 지표·구조물은 서버가 판정에 쓰는 그 코드로
 * 잰다 — 화면이 다시 계산하면 판정과 다른 그림이 나오고, 그 어긋남은 조용하다.
 */

/** 값 하나를 숫자로 — 못 읽으면 `null`. ⚠️ 0 으로 채우지 않는다 (규칙 #8). */
function number(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const got = Number(raw);
  return Number.isFinite(got) ? got : null;
}

/**
 * 가격 **띠** 하나 — 서버가 거른 결과(`/analysis/frame` 의 `levels`)의 모양이다.
 *
 * 🔴 **거르기는 서버가 한다** (2026-08-30). 한때 화면이 골랐는데, 그러면 판정과 다른
 * 규칙이 두 벌이 되고 그 어긋남은 조용하다 (규칙 #9) — `analysis/levels.py` 가
 * 뭉치기·접점·최근·관통·비용 다섯 규칙으로 30개를 4~5개로 줄인다.
 *
 * ⚠️ `kind` 는 **역할**이지 방향이 아니다. `resistance` 는 *"위에서 막는 자리"* 이고
 * 값이 높다는 뜻이 아니다 — 박스권 하단이 지지, 상단이 저항이다.
 */
export interface Zone {
  low: number;
  high: number;
  /** `support` · `resistance` · `smart`(스마트 띠). */
  kind: string;
  /** 사람이 읽는 역할 이름. 없을 수 있다. */
  role: string;
  /** 몇 번 닿았나 — **믿을 만한가의 척도**다. 1~2 회짜리는 우연일 수 있다. */
  touches: number;
  /** 이 띠가 생긴 시각 (ISO). 없으면 창 처음부터로 본다. */
  from: string | null;
}

/** 지금 추세 — `trend.structure` 한 장. */
export interface Trend {
  /** `UPTREND` · `DOWNTREND` · `RANGE` 등 서버가 말하는 값 그대로. */
  trend: string;
  /** 마디 수 — 몇 번 꺾여서 나온 판단인가. */
  legs: number;
}

/**
 * 추세 판단을 읽는다.
 *
 * ⛔ **이름을 여기서 번역하지 않는다.** 서버가 쓰는 말을 그대로 들고 다니고 화면에서만
 * 사람 말로 바꾼다 — 중간에서 바꾸면 로그·근거와 대조할 때 두 어휘가 된다.
 */
export function readTrend(shapes: readonly Record<string, unknown>[]): Trend | null {
  const one = shapes.at(0);
  if (!one) return null;
  const trend = String(one["trend"] ?? "");
  if (!trend) return null;
  return { trend, legs: number(one["legs"]) ?? 0 };
}

/** 추세 이름을 사람 말로 — 모르는 값은 **그대로 보여 준다** (지어내지 않는다). */
export function trendText(trend: string): string {
  if (trend === "UPTREND") return "상승";
  if (trend === "DOWNTREND") return "하락";
  if (trend === "RANGE") return "횡보";
  return trend;
}

/**
 * 켤 수 있는 분석 묶음 — **플래그가 아니라 사람이 쓰는 말**로 묶는다.
 *
 * 🔴 카탈로그에는 플래그가 열몇 개인데 한 번에 켜면 화면이 선으로 뒤덮인다. 사용자가
 * 고른 셋(추세·박스권·지지저항)부터 묶어 두고, 나머지는 같은 틀에 끼운다.
 *
 * ⚠️ 하나의 묶음이 **여러 플래그**일 수 있다 — 추세는 판단(`trend.structure`)과
 * 그 근거(구조물)가 따로 온다.
 */
export const GROUPS: { id: string; label: string; flags: string[]; hint: string }[] = [
  {
    id: "trend",
    label: "추세",
    // ⚠️ 판단(`trend.structure`)과 **그 선**(`swing_trendline`)이 따로 온다. 선만
    //    켜면 "그래서 지금 뭔가" 가 없고, 판단만 켜면 근거가 안 보인다.
    //
    // ⛔ 구 추세선(`structure.trendline`)은 안 켠다 — 400봉 창에 40개+ 가 나오는
    //    과탐지가 이미 확인됐다 (`rule_candidates.md` 축 J). 신형(스윙 기반)만 쓴다.
    flags: ["trend.structure", "trend.break", "structure.swing_trendline"],
    hint: "주 추세와 그 추세선 — 굵은 구간이 실제 접점, 점선은 이어 그은 연장분이다",
  },
  {
    id: "range_box",
    label: "박스권",
    flags: ["structure.box_range"],
    hint: "상단·하단과 그 사이 — 눌린 곳이 아니라 **오간 곳**이다",
  },
  {
    id: "levels",
    label: "지지·저항",
    flags: ["structure.box"],
    hint: "피벗이 뭉친 수평대. 닿은 횟수가 믿을 만한가의 척도다",
  },
];


/** 추세선·채널 한 줄 — **봉 번호**로 온다 (시각이 아니다). */
export interface Segment {
  /** 시작 봉 번호. */
  x1: number;
  /** 접점이 끝나는 봉 번호 — 여기까지가 **실제 근거**이고 그 뒤는 연장분이다. */
  anchorX: number;
  /** 끝 봉 번호 (연장 포함). */
  x2: number;
  y1: number;
  yAnchor: number;
  y2: number;
  /**
   * 선의 성격.
   *
   * ⚠️ **직렬화기마다 이름이 다르다** (실측 2026-08-30): 구 추세선은 `high`/`low`,
   * 스윙 추세선은 `resistance`/`support` 로 온다. 하나만 보고 색을 칠하면 저항선이
   * 지지 색으로 그려지고, 그 그림은 **조용히 반대를 말한다.**
   */
  kind: string;
  touches: number;
}

/**
 * 추세선 도형을 읽는다.
 *
 * @param shapes `structure.trendline` 또는 `structure.swing_trendline` 의 도형들.
 *
 * 🔴 **x 가 봉 번호다.** 시각이 아니라 창 안의 인덱스이고, 그래서 그리는 쪽이 봉
 * 배열로 시각을 찾아야 한다 — 그 변환을 빼먹으면 선이 엉뚱한 데 그려진다.
 *
 * ⚠️ 접점 구간(`x1..anchorX`)과 연장분(`anchorX..x2`)을 **가른다.** 앞은 시장이
 * 실제로 닿은 곳이고 뒤는 우리가 이어 그은 것이다 — 같은 굵기로 그리면 없는 근거를
 * 있는 것처럼 보이게 한다.
 */
export function readSegments(shapes: readonly Record<string, unknown>[]): Segment[] {
  const out: Segment[] = [];
  for (const shape of shapes) {
    // 🔴 **`Number()` 를 그냥 쓰면 안 된다** (시험이 잡았다). `Number(null)` 은 **0**
    //    이고 0 은 유한한 수라, 빠진 칸이 조용히 통과해 **창 왼쪽 끝에 선**이 생긴다.
    //    `number()` 는 null·undefined·빈 문자열을 전부 `null` 로 돌려준다.
    const x1 = number(shape["x1"]);
    const anchorX = number(shape["anchor_x2"]);
    const x2 = number(shape["x2"]);
    const y1 = number(shape["y1"]);
    const yAnchor = number(shape["y_anchor2"]);
    const y2 = number(shape["y2"]);
    if (
      x1 === null ||
      anchorX === null ||
      x2 === null ||
      y1 === null ||
      yAnchor === null ||
      y2 === null
    ) {
      continue;
    }
    // ⚠️ 봉 번호가 뒤집힌 것은 버린다 — 뒤로 가는 선은 그릴 수 없다.
    if (x2 < x1) continue;
    out.push({
      x1,
      anchorX,
      x2,
      y1,
      yAnchor,
      y2,
      kind: String(shape["kind"] ?? ""),
      touches: number(shape["touches"]) ?? 0,
    });
  }
  return out;
}

/**
 * 선이 너무 많으면 **닿은 횟수로 고른다** — 레벨과 같은 사상.
 *
 * 🔴 이 프로젝트는 추세선 과탐지를 이미 겪었다: 400봉 창에 **40개+** 가 나와
 * "합류" 가 필터 기능을 잃었다 (`rule_candidates.md` 축 J). 원인은 조합이다 —
 * 스윙 40개면 선 쌍이 780가지이고 그중 우연한 3접점이 나온다.
 *
 * ⇒ 화면에서도 같은 일이 난다. 닿은 횟수가 많은 것만 남긴다.
 */
export function pickSegments(rows: readonly Segment[], limit = 4): Segment[] {
  return [...rows].sort((a, b) => b.touches - a.touches).slice(0, Math.max(0, limit));
}
