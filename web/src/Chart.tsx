/**
 * 캔들 차트 — TradingView Lightweight Charts (T33 · 사용자 확정 2026-08-22).
 *
 * 🔴 **손으로 그리던 1,072줄 SVG 를 걷어냈다.** 줌·팬·축 눈금·클리핑·리사이즈를
 * 전부 라이브러리가 들고, 우리는 **무엇을 그릴지**만 만든다:
 *
 *     캔들          CandlestickSeries — 마감 봉만 (판정과 같은 재료)
 *     만드는 중 봉   series.update() — 초 단위로 꼬리가 흔들린다
 *     계획선        createPriceLine — 손절·진입·1차·목표
 *     대기 띠       BandsPrimitive — 스마트 띠를 봉 위에 깔개로 깐다
 *     돌파선        점선 price line — 넘으면 사는 자리
 *     매매 순간     markers — 진입·익절·손절의 "언제"
 *
 * 🔴 **색은 토큰에서 읽는다** (T33 → T34 다크모드의 전제). 여기 하드코딩을 남기면
 * 토글이 차트만 밝은 채로 남긴다.
 *
 * ⚠️ **띠·돌파선은 판정 축 도형으로만 그린다** (`judgedOnly`) — 옛 차트의 규칙
 * 그대로다. 아무도 안 쓰는 선 위에 "여기서 진입한다"를 그리면 화면이 거짓말한다.
 *
 * ⚠️ `active` 가 참이 아닐 때는 대기 띠를 안 깐다 — 플레이북이 꺼져 있는데 띠를
 * 그리면 *"여기 오면 산다"* 는 거짓말이 된다 (규칙 #8).
 */

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type ISeriesPrimitive,
  type LineData,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, Frame, Plan } from "./chartTypes";
import {
  type Gate,
  gateText,
  gateTone,
  readGates,
  readSlope,
  readVol,
  statusOf,
} from "./adx";
import { mayPaint, mayReplant } from "./paint";
import { frameSeconds } from "./ui";
import { buildEnabled, useChartSettings, type Ohlc } from "./chart/indicators";
import {
  applyOverlays,
  computeOverlays,
  type OverlayHandle,
} from "./chart/overlays";

const HEIGHT = 460;
/** 추세강도 판의 높이 — 가격 판을 눌러 버리지 않을 만큼만. */
const ADX_HEIGHT = 110;
/**
 * 마지막 봉 오른쪽에 남기는 여백 (봉 수).
 *
 * ⚠️ 0 이면 마지막 봉이 축 라벨에 붙어 잘려 보인다. 라이브로 고정할 때도 이만큼은
 * 남긴다 — 그래야 "지금" 이 화면 끝에 눌려 있지 않다.
 */
export const RIGHT_PAD = 3;

/**
 * 오른쪽 끝에 **붙인다** — 배율은 그대로 두고 오른쪽만 맞춘다.
 *
 * @param chart 차트.
 * @param count 봉 수.
 * @returns 실제로 움직였나. 이미 붙어 있었으면 거짓이다.
 *
 * 🔴 **`scrollToRealTime()` 을 쓰지 않는다** (사용자 신고 2026-08-30: *"라이브 버튼
 * 누르면 계속 오른쪽으로 한번 팅기듯이 움직였다가 다시 고정되네"*).
 *
 * 그 함수는 **애니메이션으로** 라이브러리 자체의 오른쪽 여백까지 날아간다. 우리 여백
 * (`RIGHT_PAD`)과 값이 다르므로, 날아간 직후 구독이 깨어나 도로 잡아당겼다 —
 * **그 왕복이 팅김이었다.** 게다가 그 효과가 봉이 올 때마다 다시 돌아 갱신마다
 * 반복됐다.
 *
 * ⇒ 붙이는 방법을 **한 벌로** 만든다. 구독도 단추도 이 함수를 부르므로 두 자리가
 *   같은 자리를 뜻하고, 서로 잡아당길 수 없다.
 *
 * ⚠️ 이미 붙어 있으면 **아무것도 안 한다.** 같은 값을 다시 넣으면 라이브러리가
 * 다시 그리고, 그것이 초당 한 번이면 눈에 띈다.
 */
export function pinRight(chart: IChartApi, count: number): boolean {
  if (count === 0) return false;
  const scale = chart.timeScale();
  const range = scale.getVisibleLogicalRange();
  if (range === null) return false;
  const edge = count - 1 + RIGHT_PAD;
  // ⚠️ 반 봉 안이면 붙은 것으로 친다. 정확히 0 을 요구하면 봉이 닫힐 때마다
  //    깜빡거린다 (마지막 봉이 여백만큼 밀려나기 때문이다).
  if (Math.abs(range.to - edge) < 0.5) return false;
  scale.setVisibleLogicalRange({
    from: edge - (range.to - range.from),
    to: edge,
  });
  return true;
}

/** CSS 토큰 하나 — 차트는 DOM 밖(캔버스)이라 변수를 값으로 풀어 넘겨야 한다. */
function tone(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const found = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return found || fallback;
}

/** `#rrggbb` 에 투명도를 붙인다 — 띠는 봉을 가리면 안 된다. */
function wash(hex: string, alpha: number): string {
  const raw = hex.replace("#", "");
  if (raw.length !== 6) return hex;
  const r = parseInt(raw.slice(0, 2), 16);
  const g = parseInt(raw.slice(2, 4), 16);
  const b = parseInt(raw.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 마커가 뜻하는 것 — 색은 `Chart` 가 테마 토큰에서 푼다. */
export type MarkTone = "entry" | "gain" | "loss";

const MARK_TONES: Record<MarkTone, () => string> = {
  entry: () => tone("--entry-line", "#b8860b"),
  gain: () => tone("--gain", "#0f7b6c"),
  loss: () => tone("--loss", "#b4423a"),
};
/**
 * ⚠️ **함수로 든다.** 값으로 담으면 모듈이 처음 읽힐 때의 테마에 굳는다 — 다크 모드로
 * 바꿔도 그 값은 안 바뀐다. 그릴 때마다 푼다.
 */

function stamp(ts: string): UTCTimestamp {
  return Math.floor(Date.parse(ts) / 1000) as UTCTimestamp;
}

/** 마크 시각을 봉 시작으로 내린다 — 봉에 안 걸린 마커는 그려지지 않는다. */
function snap(ts: string, frame: string): UTCTimestamp {
  // ⚠️ **공용 변환기를 쓴다** — 여기 표를 따로 들고 있다가 `ui.tsx` 와 갈렸다
  //    (사용자 감사 2026-08-30). 축이 하나 늘면 표는 안 따라오고, 그러면 그 축의
  //    마커가 조용히 엉뚱한 봉에 붙는다.
  const step = frameSeconds(frame);
  const raw = Math.floor(Date.parse(ts) / 1000);
  return (raw - (raw % step)) as UTCTimestamp;
}

function toBar(row: Candle): CandlestickData<Time> {
  return {
    time: stamp(row.ts),
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
  };
}

type Band = { low: number; high: number; color: string };

/**
 * 가격 띠 깔개 — 화면 폭 전체에 [low, high] 구간을 칠한다.
 *
 * Lightweight Charts 에 사각형 개념이 없어서 프리미티브로 만든다. 좌표 변환은
 * 시리즈가 하고(`priceToCoordinate`), 우리는 색칠만 한다.
 */
class BandsPrimitive implements ISeriesPrimitive<Time> {
  private bands: Band[] = [];
  private series: ISeriesApi<"Candlestick"> | null = null;
  private refresh: (() => void) | null = null;

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.refresh = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.refresh = null;
  }

  set(bands: Band[]): void {
    this.bands = bands;
    this.refresh?.();
  }

  paneViews() {
    const owner = this;
    return [
      {
        // 봉 **아래**에 깐다 — 위에 올리면 캔들이 띠에 묻힌다.
        zOrder(): "bottom" {
          return "bottom";
        },
        renderer() {
          return {
            draw(target: {
              useBitmapCoordinateSpace: (
                fn: (scope: {
                  context: CanvasRenderingContext2D;
                  bitmapSize: { width: number; height: number };
                  verticalPixelRatio: number;
                }) => void,
              ) => void;
            }) {
              const series = owner.series;
              if (series === null || owner.bands.length === 0) return;
              target.useBitmapCoordinateSpace(
                ({ context, bitmapSize, verticalPixelRatio }) => {
                  for (const band of owner.bands) {
                    const top = series.priceToCoordinate(band.high);
                    const bottom = series.priceToCoordinate(band.low);
                    if (top === null || bottom === null) continue;
                    context.fillStyle = band.color;
                    context.fillRect(
                      0,
                      top * verticalPixelRatio,
                      bitmapSize.width,
                      Math.max(1, (bottom - top) * verticalPixelRatio),
                    );
                  }
                },
              );
            },
          };
        },
      },
    ];
  }
}

type Props = {
  frame: Frame;
  plan?: Plan | null;
  flashAt?: string | null;
  /** 판정이 돈 마지막 봉 — 차트가 **로직이 살아 있는지**를 보여 준다. */
  judgedAt?: string | null;
  /** **판정 축** — 띠·돌파선은 이 축의 도형으로만 잰다. */
  entryFrame?: string | null;
  /** 플레이북이 지금 도는가. 참이 아니면 대기 띠를 안 깐다. */
  active?: boolean;
  /** **지금 만들어지고 있는 봉** — 판정은 이 봉을 안 본다. */
  forming?: Candle | null;
  /** 매매가 일어난 순간들 — 진입·1차 익절·청산. */
  marks?: { at: string; label: string; tone: MarkTone }[];
  /**
   * **라이브(최신 추종)가 켜져 있나** — 부모가 들고 단추도 부모가 그린다.
   *
   * 🔴 단추가 **축 단추 줄 오른쪽 끝**에 있어야 한다 (사용자 요구 2026-08-30). 그 줄은
   * 부모가 그리므로 상태도 부모에 있다 — 차트 안에 두면 단추가 차트 아래로 밀린다.
   */
  follow?: boolean;
  /**
   * **그냥 그릴 가격 띠** — 분석 샌드박스가 쓴다 (차트 주문 탭).
   *
   * ⚠️ `active` 경로의 대기 띠와 **다른 것**이다. 그쪽은 플레이북이 *"여기 오면 산다"*
   * 를 말하는 띠라 판이 도는 중에만 그린다 — 분석은 그 판단과 무관하게 구조를 본다.
   */
  zones?: { low: number; high: number; kind: string }[];
  /** 가로 가격선 — 전고/전저처럼 **선으로** 보여야 하는 근거 (사용자 2026-09-11). 점(marks)과 같이 써도 된다. */
  lines?: { price: number; label: string; tone: MarkTone }[];
  /**
   * **끌 수 있는 계획선** — 차트 주문 탭이 쓴다 (3단계).
   *
   * 🔴 `plan` 과 다르다. 그쪽은 원장이 확정한 값이라 **읽기 전용**이고, 이쪽은 사람이
   * 손대는 제안이다. 둘을 한 통로로 두면 원장 값을 끌 수 있게 되고, 그것은 절대
   * 규칙 #4(집행값의 SSoT 는 RiskManager) 위반으로 가는 문이 된다.
   */
  draft?: { entry: number; stop: number; first: number; target: number } | null;
  /** 선을 끌었다 — 어느 선이 어디로 갔는지 부모에게 알린다. */
  onDrag?: (
    which: "entry" | "stop" | "first" | "target",
    price: number,
  ) => void;
  /**
   * **추세선·채널** — 봉 번호로 온 선분들 (분석 샌드박스).
   *
   * ⚠️ 접점 구간과 연장분을 **가른다**. 앞은 시장이 닿은 곳이고 뒤는 우리가 이어
   * 그은 것이다 — 같은 굵기로 그리면 없는 근거를 있는 것처럼 보이게 한다.
   */
  segments?: {
    x1: number;
    anchorX: number;
    x2: number;
    y1: number;
    yAnchor: number;
    y2: number;
    kind: string;
  }[];
};

const PLAN_LINES = [
  { key: "stop", label: "손절", token: "--loss", fallback: "#b4423a" },
  { key: "entry", label: "진입", token: "--entry-line", fallback: "#b8860b" },
  { key: "first", label: "1차", token: "--gain", fallback: "#0f7b6c" },
  { key: "target", label: "목표", token: "--gain", fallback: "#0f7b6c" },
] as const;

export function Chart({
  frame,
  plan,
  flashAt,
  judgedAt,
  // entryFrame · active 는 옛 "대기 띠" 가 읽던 값 — 타입에는 남기고(호출처 호환) 여기서는 안 읽는다 (T224).
  forming,
  marks,
  follow,
  zones,
  lines,
  draft,
  onDrag,
  segments,
}: Props) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const maLine = useRef<ISeriesApi<"Line"> | null>(null);
  const bands = useRef<BandsPrimitive | null>(null);
  const badges = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const planLines = useRef<IPriceLine[]>([]);
  // 🔴 **추세강도는 가격이 아니다** — 같은 축에 그리면 0~100 이 캔들 옆에 눌려 붙어
  //    아무것도 안 보인다. lightweight-charts v5 의 **판(pane)** 으로 아래에 붙인다.
  const adxLine = useRef<ISeriesApi<"Line"> | null>(null);
  const adxLines = useRef<IPriceLine[]>([]);
  // ⭐ 사용자 지표(이평 · 볼린저) — 설정은 모든 차트가 공유한다 (`chart/indicators/settings.ts`). 표시용이고
  //    판정값(보라 SMA · ADX)과 별개다 — 그쪽은 서버가 세션의 함수로 재서 보낸다 (규칙 #9).
  const overlayLines = useRef<OverlayHandle[]>([]);
  const [chartSettings] = useChartSettings();
  /**
   * **차트에 실제로 들어간 마지막 봉 시각** (초).
   *
   * 🔴 `update()` 가 거부하는 기준은 props 가 아니라 **시리즈가 들고 있는 값**이다.
   * 둘을 헷갈리면 축을 바꿀 때 `Cannot update oldest data` 로 터지고, 경계가 없으면
   * 화면이 통째로 사라진다 (2026-08-30 실측).
   */
  const lastPainted = useRef<number | null>(null);
  /**
   * **라이브가 켜져 있나** — 부모가 들고 있는 값을 구독에서 읽는다.
   *
   * 🔴 `useRef` 인 이유: 구독 콜백은 판을 지을 때 **한 번** 만들어지고 그 안의 값이
   * 얼어붙는다. 상태를 그대로 읽으면 영원히 첫 값(참)만 보이고, 꺼도 계속 끌려간다.
   */
  const follows = useRef(follow !== false);
  follows.current = follow !== false;
  /** 우리가 스스로 옮기는 중인가 — 되먹임 고리를 끊는다. */
  const pinning = useRef(false);

  // ── 판 만들기 (한 번) ────────────────────────────────────────────────
  useEffect(() => {
    if (holder.current === null) return;
    const up = tone("--gain", "#0f7b6c");
    const down = tone("--loss", "#b4423a");
    const made = createChart(holder.current, {
      height: HEIGHT,
      autoSize: true,
      layout: {
        background: { color: tone("--pure-white", "#ffffff") },
        textColor: tone("--warm-gray", "#78716c"),
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: tone("--stone-border", "#e8e6e5") },
        horzLines: { color: tone("--stone-border", "#e8e6e5") },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: frame.timeframe === "10s",
        // 🔴 **봉을 굵게 시작한다** (사용자 신고 2026-08-30: *"몸통으로만 찍히고 있다"*).
        //
        //    서버는 800봉을 준다. 기본 폭(6px)이면 화면에 200봉이 들어가고, 그보다
        //    좁혀지면 봉 하나가 2~3px 이 된다 — 그 폭에서는 **꼬리(1px)가 몸통에
        //    묻혀 안 보인다.** 자료에는 꼬리가 있는데 화면에서 사라지는 것이다.
        //
        //    실측(ADA · Gate): 4h 는 최근 40봉 중 꼬리 없는 봉이 **0개**다.
        //    ⚠️ 다만 10s 는 23/40 이 **진짜로** 꼬리가 없다 — 틱이 0.0001 이라
        //    10초에 한두 틱만 움직이면 고가가 종가와 같아진다. 그것은 그리기 문제가
        //    아니라 시장이 그런 것이고, 굵게 그려도 안 생긴다.
        barSpacing: 9,
        // ⚠️ 너무 좁아지는 것을 막는다 — 줌아웃은 되되 꼬리가 죽는 폭까지는 안 간다.
        minBarSpacing: 1.5,
      },
      crosshair: { mode: 0 },
    });
    const candles = made.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      wickUpColor: up,
      wickDownColor: down,
      borderVisible: false,
    });
    const rug = new BandsPrimitive();
    candles.attachPrimitive(rug);
    // 🔴 **트레일 청산선(SMA)** — full_ride 전략의 실제 청산 경로. 캔들 뒤에 얇게 깐다.
    //    가격선·마지막값 라벨은 끈다(오른쪽 축이 계획선으로 이미 붐빈다).
    const ma = made.addSeries(LineSeries, {
      color: tone("--ma-line", "#8957e5"),
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    // 🔴 **추세강도(ADX) 판** — 진입 문(롱 35 · 숏 20)도 청산 문(31 · 16)도 이 값이
    //    정하는데 화면에 없었다 (사용자 지적 2026-08-30). 세 번째 인자가 판 번호다.
    //
    // ⚠️ 값 자체는 **서버가 세션의 `adx()` 로 재서 보낸다** (규칙 #9). 여기서 다시
    //    계산하면 화면과 판정이 다른 값을 그리고, 그 어긋남은 조용하다.
    const power = made.addSeries(
      LineSeries,
      {
        color: tone("--adx-line", "#0e7490"),
        lineWidth: 2,
        priceLineVisible: false,
        // ⭐ **지금 값 하나는 축에 남긴다** — 이 판에서 축에 있어야 할 유일한 숫자다.
        //   문턱은 아래 칩이 말하므로 축을 비워 둔다.
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        // 🔴 **소수점을 없앤다** (사용자 지적 2026-08-30). 기본 서식이 가격을 따라
        //    `20.50` 처럼 찍혀 위 가격 축(0.20)과 같은 종류로 보였다. 추세강도는
        //    0~100 의 **지수**이고 소수 둘째 자리에 뜻이 없다.
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      },
      1,
    );
    // ⭐ 아래 판은 곁다리다 — 가격 판을 눌러 버리면 원래 보던 것을 잃는다.
    made.panes()[1]?.setHeight(ADX_HEIGHT);
    // ⚠️ 판을 새로 지으면 **기억도 비운다** — 옛 축의 마지막 시각이 남아 있으면
    //    새 축의 첫 조각이 "과거" 로 보여 영영 안 그려진다.
    lastPainted.current = null;
    // 🔴 **오른쪽 끝에 붙어 있는지 지켜본다.** 사람이 과거로 끌면 추종이 꺼지고,
    //    그때부터 새 봉은 화면 밖에 쌓인다 — 그 사실을 화면이 말해야 한다.
    //
    // ⚠️ 끝에서 몇 봉은 붙은 것으로 친다. 정확히 0 을 요구하면 봉이 하나 닫힐 때마다
    //    깜빡거리며 꺼진다 (마지막 봉이 오른쪽 여백만큼 밀려나기 때문이다).
    // 🔴 **라이브가 켜져 있으면 오른쪽 끝을 놓지 않는다** (사용자 요구 2026-08-30:
    //    *"휠 확대 축소, 가로 축 세로 축 조절 등등의 기능 동작 시에도 라이브가 켜져
    //    있다면, 오른쪽은 항상 가장 최신 봉에서 벗어나면 안돼"*).
    //
    //    옛 SVG 차트에도 같은 주석이 있었다 — *"라이브 중에는 무엇을 해도 오른쪽 끝이
    //    안 풀린다"*. 그때는 줌이 `end` 를 계산해 넣어서 확대 한 번에 추종이 조용히
    //    꺼졌고, 그 뒤로 새 봉이 화면 밖에 쌓였다. 사람은 차트가 멈춘 줄 안다.
    //
    // ⚠️ **배율은 유지한다.** 폭(`to - from`)을 그대로 두고 오른쪽만 끝에 맞춘다 —
    //    `scrollToRealTime()` 은 확대 상태를 흔든다.
    //
    // ⛔ 되먹임을 끊는다 (`pinning`). 우리가 옮긴 것이 다시 이 콜백을 부르고, 그것이
    //    또 옮기면 화면이 떨린다.
    made.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!follows.current || range === null || pinning.current) return;
      pinning.current = true;
      pinRight(made, series.current?.data().length ?? 0);
      pinning.current = false;
    });
    chart.current = made;
    series.current = candles;
    maLine.current = ma;
    adxLine.current = power;
    overlayLines.current = [];
    bands.current = rug;
    badges.current = createSeriesMarkers(candles, []);
    // 다크 모드 (T34) — 차트는 캔버스라 CSS 토큰 변화를 스스로 못 본다.
    // data-theme 이 바뀌면 토큰을 다시 읽어 칠한다. 띠·계획선은 다음 데이터
    // 갱신에 따라온다 (라이브는 초 단위로 갱신되므로 사실상 즉시다).
    const watcher = new MutationObserver(() => {
      const brightUp = tone("--gain", "#0f7b6c");
      const brightDown = tone("--loss", "#b4423a");
      made.applyOptions({
        layout: {
          background: { color: tone("--pure-white", "#ffffff") },
          textColor: tone("--warm-gray", "#78716c"),
        },
        grid: {
          vertLines: { color: tone("--stone-border", "#e8e6e5") },
          horzLines: { color: tone("--stone-border", "#e8e6e5") },
        },
      });
      candles.applyOptions({
        upColor: brightUp,
        downColor: brightDown,
        wickUpColor: brightUp,
        wickDownColor: brightDown,
      });
      ma.applyOptions({ color: tone("--ma-line", "#8957e5") });
    });
    watcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      watcher.disconnect();
      badges.current = null;
      bands.current = null;
      overlayLines.current = [];
      maLine.current = null;
      series.current = null;
      chart.current = null;
      made.remove();
    };
    // ⚠️ 시간축이 바뀌면 초 표시가 달라지므로 판을 새로 짓는다.
  }, [frame.timeframe]);

  // ── 봉 붓기 ─────────────────────────────────────────────────────────
  // 🔴 **`forming` 을 상자에 담는다** — 값은 최신이되 아래 `setData` 를 **다시 부르지
  //    않는다** (사용자 신고 2026-08-30: *"띡 사라지고 봉 생기고 다시 꼬리 생기고"*).
  //
  //    전에는 `forming` 이 아래 효과의 의존성에 있었다. 진행 중 봉은 **1초마다** 새
  //    객체로 오므로, 봉 400개짜리 `setData` 가 **매초** 돌았다 — 시리즈를 통째로 갈아
  //    끼우는 일이라 눈에 띄는 것이 당연하다. 그때 적어 둔 *"1초에 한 번이면 눈에 안
  //    띈다"* 는 **추측이었고 틀렸다.**
  //
  // ⚠️ 의존성에서 뺄 때 상자가 필요한 이유: 그냥 빼면 효과가 **낡은 조각**을 얹는다
  //    (닫힌 클로저). 상자는 렌더마다 갱신되므로 언제 읽어도 최신이다.
  const tick = useRef(forming);
  tick.current = forming;

  useEffect(() => {
    if (series.current === null) return;
    // 오름차순 + 같은 시각 중복 제거 — setData 의 계약이다.
    //
    // ⚠️ **정렬까지 한다.** Map 은 넣은 순서를 지킬 뿐 시간순을 보장하지 않는다 —
    //    서버가 오름차순으로 주므로 지금까지는 맞았지만, 계약을 코드가 지켜야 한다.
    const rows = new Map<number, CandlestickData<Time>>();
    for (const row of frame.candles)
      rows.set(stamp(row.ts) as number, toBar(row));
    const sorted = [...rows.entries()].sort((a, b) => a[0] - b[0]);
    series.current.setData(sorted.map(([, bar]) => bar));
    const lastClosed = sorted.at(-1)?.[0] ?? null;
    // 🔴 **다시 그리면 만들어지던 봉이 사라진다.** `setData` 는 시리즈를 통째로 갈아
    //    끼우므로 `update()` 로 얹어 둔 진행 중 봉이 지워진다.
    //
    // ⇒ 갈아 끼운 **그 자리에서** 다시 얹는다. 두 폴링의 타이밍과 무관해진다.
    //
    // ⛔ 다만 **마감 봉이 이미 그 시각을 들고 있으면 손대지 않는다** (`mayReplant`).
    //    마감된 값이 최종본이고, 손에 든 진행 중 조각은 그보다 **먼저** 찍힌 것이라
    //    고가가 더 낮다 — 덮어쓰면 꼬리가 줄었다가 다음 틱에 다시 자란다.
    //    그것이 사용자가 본 깜빡임의 절반이다.
    const now = tick.current;
    if (now) {
      const at = stamp(now.ts) as number;
      if (mayReplant(lastClosed, at)) {
        series.current.update(toBar(now));
        lastPainted.current = at;
        return;
      }
    }
    // 🔴 **차트에 실제로 들어간 마지막 시각을 기억한다** (사용자 신고 2026-08-30:
    //    `Cannot update oldest data`).
    //
    //    예전 가드는 `frame.candles.at(-1)` — 즉 **props** 와 비교했다. 그런데
    //    `update()` 가 거부하는 기준은 **시리즈가 들고 있는 마지막 값**이다. 축을
    //    바꾸는 순간 그 둘이 갈린다:
    //
    //      1m 을 보다가 4h 로 바꾼다
    //      → 시리즈는 4h 로 다시 그려지는데, `forming` 은 아직 1m 조각(18:03)이다
    //      → props 의 마지막은 4h 봉(16:00) 이라 `18:03 < 16:00` 이 거짓 → 통과
    //      → 시리즈에 18:03 이 박힌다
    //      → 곧 진짜 4h 조각(16:00)이 오는데, props 기준으로는 또 통과 →
    //         시리즈의 마지막(18:03)보다 과거라 **여기서 터진다**
    //
    //    ⇒ 비교 대상을 **우리가 방금 넣은 값**으로 바꾼다. 어디서 온 조각이든 막힌다.
    lastPainted.current = lastClosed;
    // ⛔ **`forming` 은 의존성에 없다.** 넣으면 봉 400개짜리 `setData` 가 매초 돈다 —
    //    그것이 이 깜빡임의 나머지 절반이었다. 최신 값은 위의 상자가 들고 있다.
  }, [frame.candles]);

  // ── 사용자 지표 겹치기 (이평 · 볼린저) ─────────────────────────────
  // ⚠️ 마감 봉으로만 계산한다 — 만드는 중 봉을 넣으면 매초 다시 그린다. 그 봉 하나의 지표값은 어차피 확정 전이다.
  const overlayBars = useMemo<Ohlc[]>(
    () =>
      frame.candles.map((row) => ({
        time: stamp(row.ts) as number,
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
      })),
    [frame.candles],
  );
  const overlaySeries = useMemo(
    () => computeOverlays(buildEnabled(chartSettings.indicators), overlayBars),
    [chartSettings.indicators, overlayBars],
  );
  useEffect(() => {
    const made = chart.current;
    if (made === null) return;
    overlayLines.current = applyOverlays(
      made,
      overlayLines.current,
      overlaySeries,
    );
  }, [overlaySeries, frame.timeframe]);

  // ── 만드는 중 봉 — 꼬리가 흔들린다 ──────────────────────────────────
  //
  // ⛔ **`frame.candles` 는 의존성에 없다** (사용자 신고 2026-08-30 · 깜빡임의 세 번째
  //    갈래). 있으면 마감 봉이 새로 올 때 이 효과가 **또** 돌면서, 방금 확정된 마감
  //    봉 위에 손에 든 진행 중 조각을 덮어쓴다:
  //
  //      setData  →  마감 봉 100 (고 78047.7 · 최종본)      lastPainted = 100
  //      이 효과  →  mayPaint(100, 100) = 참 → update(조각 100 · 고 78047.6)
  //      → **꼬리가 줄었다가** 다음 틱에 다시 자란다
  //
  //    위 `setData` 효과가 이미 *"다시 그린 뒤 도로 얹기"* 를 `mayReplant` 로 옳게
  //    처리한다. 여기는 **새 틱이 왔을 때만** 돌면 된다.
  //
  // ⚠️ 여기서는 `mayPaint` 가 맞다 — 같은 시각을 허용해야 꼬리가 자란다. 위와 규칙이
  //    다른 것이 실수가 아니라 **다른 일을 하기 때문**이다.
  useEffect(() => {
    if (series.current === null || !forming) return;
    const at = stamp(forming.ts) as number;
    // ⛔ **시리즈가 들고 있는 마지막보다 과거면 버린다** — `update` 는 뒤로 못 간다.
    //    (같은 시각은 허용한다. 그것이 만들어지는 중인 봉을 갱신하는 정상 경로다.)
    if (!mayPaint(lastPainted.current, at)) return;
    series.current.update(toBar(forming));
    lastPainted.current = at;
  }, [forming]);

  // 🔴 **켜는 순간 바로 붙는다.** 구독은 사람이 움직일 때만 불리므로, 껐다 켜기만
  //    해서는 화면이 안 움직인다 — 그러면 단추가 안 듣는 것처럼 보인다.
  //
  // ⚠️ **구독과 같은 함수를 쓴다.** 전에는 여기서 `scrollToRealTime()` 을 불렀는데,
  //    그것이 라이브러리 자체 여백까지 애니메이션으로 날아가고 구독이 도로 당겨서
  //    **팅겼다** (사용자 신고 2026-08-30). 붙는 자리가 두 벌이면 서로 잡아당긴다.
  //
  // ⛔ 되먹임을 끊는다 — 우리가 옮긴 것이 구독을 깨우고 그것이 또 옮기면 떨린다.
  useEffect(() => {
    const made = chart.current;
    if (follow === false || made === null) return;
    pinning.current = true;
    pinRight(made, series.current?.data().length ?? 0);
    pinning.current = false;
  }, [follow, frame.candles]);

  // ── 끌 수 있는 계획선 (차트 주문) ───────────────────────────────────
  //
  // 🔴 **lightweight-charts 에 "끄는 선" 이 없다.** `createPriceLine` 은 그리기만
  //    한다 — 그래서 마우스를 직접 듣는다: 선 근처에서 누르면 그 선을 잡고, 움직이면
  //    좌표를 가격으로 되바꿔 부모에게 알린다.
  //
  // ⚠️ **잡는 동안 차트를 잠근다** (`handleScroll`·`handleScale`). 안 잠그면 선을
  //    끄는 것이 차트를 끄는 것이 되어 화면이 통째로 움직인다.
  const held = useRef<"entry" | "stop" | "first" | "target" | null>(null);
  const draftLines = useRef<IPriceLine[]>([]);
  const extraLines = useRef<IPriceLine[]>([]);
  /** 추세선 시리즈들 — 선 하나가 시리즈 하나다 (LWC 에 "선분" 개념이 없다). */
  const segLines = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    const box = holder.current;
    const made = chart.current;
    const drawn = series.current;
    if (box === null || made === null || drawn === null || !draft || !onDrag)
      return;

    /** 화면 y 를 가격으로 — 축 밖이면 `null`. */
    const priceAt = (event: MouseEvent): number | null => {
      const rect = box.getBoundingClientRect();
      const got = drawn.coordinateToPrice(event.clientY - rect.top);
      return got === null ? null : Number(got);
    };

    const down = (event: MouseEvent) => {
      const price = priceAt(event);
      if (price === null) return;
      // ⚠️ **가장 가까운 선을 잡되, 너무 멀면 안 잡는다.** 아무 데나 눌러도 잡히면
      //    차트를 못 끈다 — 잡는 반경은 화면 높이의 2% 로 둔다.
      const rect = box.getBoundingClientRect();
      const near = Math.abs(
        Number(drawn.coordinateToPrice(0) ?? 0) -
          Number(drawn.coordinateToPrice(rect.height * 0.02) ?? 0),
      );
      const rows: ["entry" | "stop" | "first" | "target", number][] = [
        ["entry", draft.entry],
        ["stop", draft.stop],
        ["first", draft.first],
        ["target", draft.target],
      ];
      let best: (typeof rows)[number] | null = null;
      for (const row of rows) {
        if (Math.abs(row[1] - price) > near) continue;
        if (
          best === null ||
          Math.abs(row[1] - price) < Math.abs(best[1] - price)
        )
          best = row;
      }
      if (best === null) return;
      held.current = best[0];
      // ⛔ 잠근다 — 안 잠그면 선이 아니라 차트가 끌린다.
      made.applyOptions({ handleScroll: false, handleScale: false });
      event.preventDefault();
    };

    const moveTo = (event: MouseEvent) => {
      if (held.current === null) return;
      const price = priceAt(event);
      if (price !== null) onDrag(held.current, price);
    };

    const up = () => {
      if (held.current === null) return;
      held.current = null;
      made.applyOptions({ handleScroll: true, handleScale: true });
    };

    box.addEventListener("mousedown", down);
    window.addEventListener("mousemove", moveTo);
    window.addEventListener("mouseup", up);
    return () => {
      box.removeEventListener("mousedown", down);
      window.removeEventListener("mousemove", moveTo);
      window.removeEventListener("mouseup", up);
      // ⚠️ 떠날 때 반드시 푼다 — 잠근 채로 언마운트되면 다음 차트가 안 움직인다.
      made.applyOptions({ handleScroll: true, handleScale: true });
    };
  }, [draft, onDrag]);

  // 끌리는 선을 그린다 — 값이 바뀔 때마다 다시 긋는다.
  useEffect(() => {
    const drawn = series.current;
    if (drawn === null) return;
    for (const line of draftLines.current) drawn.removePriceLine(line);
    draftLines.current = [];
    if (!draft) return;
    const rows: [string, number, string, string][] = [
      ["손절", draft.stop, "--loss", "#b4423a"],
      ["진입", draft.entry, "--entry-line", "#b8860b"],
      ["1차", draft.first, "--gain", "#0f7b6c"],
      ["목표", draft.target, "--gain", "#0f7b6c"],
    ];
    draftLines.current = rows.map(([title, price, token, fallback]) =>
      drawn.createPriceLine({
        price,
        color: tone(token, fallback),
        // ⭐ 끌 수 있는 선은 **굵게** — 읽기 전용 계획선과 눈으로 갈린다.
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: `${title} ⇕`,
      }),
    );
  }, [draft]);

  // 근거 가격선(전고/전저 등) — 점선 · 축 라벨 · 이름. 값이 바뀔 때마다 다시 긋는다.
  useEffect(() => {
    const drawn = series.current;
    if (drawn === null) return;
    for (const line of extraLines.current) drawn.removePriceLine(line);
    extraLines.current = [];
    for (const item of lines ?? []) {
      const [token, fallback] =
        item.tone === "loss"
          ? ["--loss", "#b4423a"]
          : item.tone === "gain"
            ? ["--gain", "#0f7b6c"]
            : ["--entry-line", "#b8860b"];
      extraLines.current.push(
        drawn.createPriceLine({
          price: item.price,
          color: tone(token, fallback),
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: item.label,
        }),
      );
    }
  }, [lines]);

  // ── 추세선·채널 ─────────────────────────────────────────────────────
  //
  // 🔴 **x 가 봉 번호로 온다.** 시각이 아니라 창 안의 인덱스라, 봉 배열로 시각을
  //    찾아야 한다 — 그 변환을 빼먹으면 선이 엉뚱한 데 그려진다.
  //
  // ⚠️ **접점 구간과 연장분을 가른다.** 앞은 시장이 실제로 닿은 곳이고 뒤는 우리가
  //    이어 그은 것이다. 같은 굵기로 그리면 **없는 근거를 있는 것처럼** 보이게 한다 —
  //    이 프로젝트는 추세선 과탐지로 이미 한 번 데였다 (400봉에 40개+ · 축 J).
  useEffect(() => {
    const made = chart.current;
    if (made === null) return;
    for (const line of segLines.current) made.removeSeries(line);
    segLines.current = [];
    if (!segments || segments.length === 0 || frame.candles.length === 0)
      return;

    const at = (index: number): Time | null => {
      const row =
        frame.candles[Math.max(0, Math.min(frame.candles.length - 1, index))];
      return row ? stamp(row.ts) : null;
    };
    const up = tone("--gain", "#0f7b6c");
    const down = tone("--loss", "#b4423a");

    for (const seg of segments) {
      // ⚠️ **직렬화기마다 이름이 다르다** (실측 2026-08-30). 구 추세선은 `high`/`low`,
      //    스윙 추세선은 `resistance`/`support` 로 온다 — 하나만 보면 저항선이 지지
      //    색으로 그려지고, 그 그림은 조용히 반대를 말한다.
      const falling = seg.kind === "high" || seg.kind === "resistance";
      const color = falling ? down : up;
      const t1 = at(seg.x1);
      const tA = at(seg.anchorX);
      const t2 = at(seg.x2);
      if (t1 === null || tA === null || t2 === null) continue;
      // ⭐ 접점 구간 — **굵게**. 여기가 근거다.
      const solid = made.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      solid.setData([
        { time: t1, value: seg.y1 },
        { time: tA, value: seg.yAnchor },
      ]);
      segLines.current.push(solid);
      // ⚠️ 연장분 — **점선·얇게**. 우리가 이어 그은 것이지 시장이 닿은 곳이 아니다.
      if (seg.x2 > seg.anchorX) {
        const dashed = made.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        dashed.setData([
          { time: tA, value: seg.yAnchor },
          { time: t2, value: seg.y2 },
        ]);
        segLines.current.push(dashed);
      }
    }
    return () => {
      for (const line of segLines.current) made.removeSeries(line);
      segLines.current = [];
    };
  }, [segments, frame.candles]);

  // ── 계획선 ──────────────────────────────────────────────────────────
  useEffect(() => {
    const drawn = series.current;
    if (drawn === null) return;
    for (const line of planLines.current) drawn.removePriceLine(line);
    planLines.current = [];
    if (!plan) return;
    // 🔴 **추세추종(full_ride)은 고정 익절이 없다** (사용자 지적 2026-08-24). 목표·1차선은
    //    진입+100R 자리표시자라, 그리면 "29배 목표" 처럼 거짓말한다 — 숨긴다. 그리고 손절선이
    //    곧 청산선이다(봉마다 SMA 로 상향 트레일) — 라벨을 "청산" 으로 바꿔 실제 동작을 말한다.
    const ride = plan.full_ride === true;
    // ⭐ 진입 대비 % 와 손익비를 라벨에 — 트레이딩뷰 포지션 도구처럼 선만 보고 읽힌다(사용자 2026-09-11).
    const entryPrice = Number(plan.entry);
    const pctOf = (price: number): string => {
      if (!Number.isFinite(entryPrice) || entryPrice === 0) return "";
      const pct = ((price - entryPrice) / entryPrice) * 100;
      return ` ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
    };
    const rr = (plan as { rr?: string }).rr;
    for (const spec of PLAN_LINES) {
      if (ride && (spec.key === "first" || spec.key === "target")) continue;
      const raw = plan[spec.key];
      if (raw === undefined || raw === null) continue;
      const title =
        ride && spec.key === "stop"
          ? "청산(트레일)"
          : spec.key === "entry"
            ? spec.label
            : `${spec.label}${pctOf(Number(raw))}${spec.key === "target" && rr ? ` · 손익비 ${rr}` : ""}`;
      planLines.current.push(
        drawn.createPriceLine({
          price: Number(raw),
          color: tone(spec.token, spec.fallback),
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title,
        }),
      );
    }
  }, [plan]);

  // ── 트레일 청산선(SMA) 데이터 + 현재가 갭 ──────────────────────────
  // 🔴 서버가 세션의 그 `sma()` 로 계산해 봉별 점으로 보낸다 (규칙 #9 · 클라 재구현 금지).
  const maPoints = useMemo<LineData<Time>[]>(() => {
    const layer = frame.layers.find((item) => item.flag === "overlay.trail_ma");
    const seen = new Map<number, number>();
    for (const shape of layer?.shapes ?? []) {
      const ts = shape["ts"];
      const price = shape["price"];
      if (
        typeof ts === "string" &&
        (typeof price === "string" || typeof price === "number")
      ) {
        seen.set(stamp(ts) as number, Number(price));
      }
    }
    return [...seen.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as Time, value }));
  }, [frame.layers]);

  useEffect(() => {
    if (maLine.current === null) return;
    maLine.current.setData(maPoints);
  }, [maPoints]);

  // 청산선까지의 갭 — 현재가가 SMA 위로 이만큼(롱은 이게 버퍼). 음수면 청산 임박.
  const maGap = useMemo(() => {
    const level = maPoints.at(-1)?.value ?? 0;
    const price = Number(frame.candles.at(-1)?.close ?? 0);
    if (level === 0 || price === 0) return null;
    return { level, pct: ((price - level) / level) * 100 };
  }, [maPoints, frame.candles]);

  // ── 추세강도(ADX) 데이터 · 문턱 ─────────────────────────────────────
  // 🔴 서버가 세션의 그 `adx()` 로 재서 봉별 점으로 보낸다 (규칙 #9 · 클라 재구현 금지).
  //    `overlay.trail_ma` 와 같은 사정이고, 같은 이유로 여기서 다시 계산하지 않는다.
  const adxPoints = useMemo<LineData<Time>[]>(() => {
    const layer = frame.layers.find((item) => item.flag === "overlay.adx");
    const seen = new Map<number, number>();
    for (const shape of layer?.shapes ?? []) {
      const ts = shape["ts"];
      const value = shape["value"];
      if (
        typeof ts === "string" &&
        (typeof value === "string" || typeof value === "number")
      ) {
        seen.set(stamp(ts) as number, Number(value));
      }
    }
    return [...seen.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as Time, value }));
  }, [frame.layers]);

  const gates = useMemo<Gate[]>(() => {
    const layer = frame.layers.find(
      (item) => item.flag === "overlay.adx_gates",
    );
    return readGates((layer?.shapes ?? []) as Record<string, unknown>[]);
  }, [frame.layers]);

  /** 지금 값 — 마지막 **닫힌** 봉의 것이다 (판정도 그 봉으로 한다). */
  const adxNow = adxPoints.at(-1)?.value ?? null;

  // ── 진입 순간에 정해지는 한 숫자짜리 근거 둘 ────────────────────────
  // ⚠️ **판을 만들지 않는다.** 차트 상자는 460px 고정이라 판이 늘면 캔들이 쓸 높이가
  //    그만큼 줄어든다 — 판 넷이면 가격이 100px 남고, 원래 보려던 것이 제일 안 보인다.
  //    그리고 이 둘은 시간축을 따라 볼 값이 아니라 **들어가는 그 순간의 값**이다.
  const slope = useMemo(() => {
    const layer = frame.layers.find((item) => item.flag === "overlay.ma_slope");
    return readSlope((layer?.shapes ?? []) as Record<string, unknown>[]);
  }, [frame.layers]);

  const vol = useMemo(() => {
    const layer = frame.layers.find(
      (item) => item.flag === "overlay.vol_target",
    );
    return readVol((layer?.shapes ?? []) as Record<string, unknown>[]);
  }, [frame.layers]);

  useEffect(() => {
    if (adxLine.current === null) return;
    adxLine.current.setData(adxPoints);
  }, [adxPoints]);

  // 문턱 가로선 — 판이 바뀔 때만 다시 긋는다 (봉마다 다시 그으면 깜빡인다).
  //
  // 🔴 **축 라벨을 끈다** (사용자 지적 2026-08-30: *"우측 축은 0.2 인데 아래 숫자는 35,
  //    서로 따로 논다"*).
  //
  //    문턱이 다섯이라 라벨 상자 다섯 개가 110px 짜리 판의 축을 **통째로 덮었다.**
  //    그 상자들이 위 가격 축(0.16~0.28)과 한 줄로 이어져 보여서, 두 판이 각자
  //    다른 자를 쓴다는 사실이 가려졌다 — 값이 아니라 **배치**가 만든 오해다.
  //
  //    ⚠️ 게다가 35 가 둘이다(롱 진입 · 본대 인계). 같은 자리에 상자 둘이 겹쳐
  //    그려지므로 축은 더 읽을 수 없게 된다.
  //
  // ⇒ 선 위의 이름(`title`)만 남기고 숫자 상자는 뺀다. **잃는 것이 없다** — 아래 칩이
  //   문턱마다 값과 남은 거리를 이미 적는다("롱 진입 ≥35 · 14.5 모자람").
  useEffect(() => {
    const drawn = adxLine.current;
    if (drawn === null) return;
    for (const line of adxLines.current) drawn.removePriceLine(line);
    // ⚠️ 값이 같은 문턱은 **선을 한 번만** 긋는다. 겹쳐 그으면 진해 보여서 다른 규칙처럼
    //    읽히고, 이름은 어차피 하나만 보인다. 칩은 그대로 둘 다 나온다.
    const seen = new Set<number>();
    const once: Gate[] = [];
    for (const gate of gates) {
      if (seen.has(gate.value)) continue;
      seen.add(gate.value);
      once.push(gate);
    }
    adxLines.current = once.map((gate) =>
      drawn.createPriceLine({
        price: gate.value,
        // ⚠️ 진입 문과 청산 문을 **다른 색**으로 — 35 와 31 이 같은 색이면 사람이
        //    둘을 한 규칙으로 읽는다. 실제로는 들어가는 문과 나가는 문이다.
        color:
          gate.kind === "in"
            ? tone("--gain", "#0f7b6c")
            : tone("--loss", "#b4423a"),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: `${gate.label} ${gate.value}`,
      }),
    );
  }, [gates]);

  useEffect(() => {
    if (bands.current === null) return;
    const up = tone("--gain", "#0f7b6c");
    const down = tone("--loss", "#b4423a");
    bands.current.set([
      // 분석 띠 — 옅게 깐다. (옛 "대기 띠·돌파선" 은 폐기된 매매법의 전사라 지웠다 · T224)
      ...(zones ?? []).map((zone) => ({
        low: zone.low,
        high: zone.high,
        color: wash(zone.kind === "resistance" ? down : up, 0.12),
      })),
    ]);
  }, [zones]);

  // ── 매매 순간 + 판정 커서 ───────────────────────────────────────────
  useEffect(() => {
    if (badges.current === null) return;
    const rows: SeriesMarker<Time>[] = (marks ?? []).map((mark) => ({
      time: snap(mark.at, frame.timeframe),
      position: "aboveBar",
      shape: "circle",
      // 🔴 **여기서 색을 푼다** (사용자 감사 2026-08-30). 전에는 부르는 쪽이 `#0f7b6c`
      //    같은 **날 hex** 를 넘겼고, 다크 모드에서 토큰이 바뀌어도 마커만 밝은 테마
      //    색으로 남았다 — 차트의 다른 모든 것은 바뀌는데 매매 표시만 안 바뀌었다.
      color: MARK_TONES[mark.tone](),
      text: mark.label,
      size: 1,
    }));
    if (judgedAt) {
      // 🔴 이 점이 안 움직이면 판정이 멈춘 것이다 — 숫자만으로는 그걸 못 읽는다.
      rows.push({
        time: snap(judgedAt, frame.timeframe),
        position: "belowBar",
        shape: "circle",
        color: tone("--cyan-signal", "#3ba6f1"),
        text: "판정",
        size: 0,
      });
    }
    if (flashAt) {
      rows.push({
        time: snap(flashAt, frame.timeframe),
        position: "aboveBar",
        shape: "arrowDown",
        color: tone("--entry-line", "#b8860b"),
        text: "⚡",
        size: 1,
      });
    }
    rows.sort((a, b) => (a.time as number) - (b.time as number));
    badges.current.setMarkers(rows);
  }, [marks, judgedAt, flashAt, frame.timeframe]);

  // ── 겹쳐 보기: 띠 요약 한 줄 (옛 차트의 규칙 문구를 압축) ───────────
  return (
    <div>
      <div ref={holder} style={{ width: "100%", height: HEIGHT }} />
      {maGap ? (
        <div className="row" style={{ marginTop: 4 }}>
          <span
            className="chip"
            title="청산선(SMA) — full_ride 전략의 실제 청산가는 봉마다 이 선으로 상향한다. 갭은 현재가가 이 선 위로 떨어진 여유(롱). 음수면 청산 임박."
            style={{ color: tone("--ma-line", "#8957e5") }}
          >
            청산선(SMA){" "}
            {maGap.level.toLocaleString(undefined, {
              maximumFractionDigits: 4,
            })}
          </span>
          <span
            className="chip"
            style={{
              color:
                maGap.pct >= 0
                  ? tone("--gain", "#0f7b6c")
                  : tone("--loss", "#b4423a"),
            }}
            title="현재가가 청산선(SMA) 위로 떨어져 있는 거리다 — **손익이 아니다**. 롱은 이 버퍼가 클수록 안전하고, 0 에 가까우면 곧 청산이다."
          >
            {/* 🔴 **"현재가 +14.58%" 는 손익으로 읽힌다** (사용자 물음 2026-08-30:
                *"이건 뭘 의미하는거야?"*). 실제 뜻은 **청산선까지의 여유**인데,
                이름이 값의 뜻을 안 말하고 있었다. */}
            청산선까지 {maGap.pct >= 0 ? "+" : ""}
            {maGap.pct.toFixed(2)}%
          </span>
        </div>
      ) : null}
      {adxNow !== null ? (
        <div className="row" style={{ marginTop: 4 }}>
          <span
            className="chip"
            style={{ color: tone("--adx-line", "#0e7490") }}
            title="추세 강도(ADX 14) — 방향은 말하지 않는다. 진입 문·청산 문이 모두 이 값으로 열리고 닫힌다. 마지막 닫힌 봉 기준이라 봉 안에서는 안 변한다."
          >
            추세강도 {adxNow.toFixed(1)}
          </span>
          {/* 🔴 **문마다 지금 걸렸는지 말한다** — 사용자 요구 2026-08-30:
              *"어떤 전략으로 대기 중이고 어떤 전략으로 어떻게 진입했는지"*.
              ⛔ "곧 들어간다" 같은 예언은 안 한다 (원칙 P4) — 값과 거리만 적는다. */}
          {gates.map((gate) => {
            const { met } = statusOf(adxNow, gate);
            const mood = gateTone(gate, met);
            return (
              <span
                key={gate.key}
                className="chip"
                title={`${gate.owner} 의 문턱 · ${
                  gate.kind === "in"
                    ? "이 값 이상이면 진입이 열린다"
                    : gate.kind === "out"
                      ? "이 값 이하로 마감하면 청산한다 (추세가 약해졌다)"
                      : "이 값 이상이면 캐리가 나가고 본대가 받는다 (추세가 강해졌다)"
                }`}
                style={{
                  color:
                    mood === "good"
                      ? tone("--gain", "#0f7b6c")
                      : mood === "bad"
                        ? tone("--loss", "#b4423a")
                        : tone("--warm-gray", "#78716c"),
                }}
              >
                {gateText(adxNow, gate)}
              </span>
            );
          })}
        </div>
      ) : null}
      {slope || vol ? (
        <div className="row" style={{ marginTop: 4 }}>
          {slope ? (
            <span
              className="chip"
              title={`SMA 선이 ${slope.bars}봉 전보다 얼마나 움직였나. 가격이 SMA 아래인 것만으로는 숏이 아니다 — 급락 직후는 반등 자리일 수 있어서, 선 자체가 내려가는 것(음수)을 더 본다.`}
              style={{
                color:
                  slope.pct < 0
                    ? tone("--loss", "#b4423a")
                    : tone("--gain", "#0f7b6c"),
              }}
            >
              SMA 기울기 {slope.pct >= 0 ? "+" : ""}
              {slope.pct.toFixed(2)}% ({slope.bars}봉)
            </span>
          ) : null}
          {vol ? (
            <span
              className="chip"
              title={`변동성 타게팅 — 요즘 많이 흔들리면 작게, 잠잠하면 크게 건다. 되돌아보기 ${vol.lookback}봉은 이 규칙의 요점이다: 짧게 재면 강추세에서 수량이 줄어 무너진다 (T81 §8-D).`}
            >
              변동성 {vol.vol.toFixed(2)}% · 크기 {vol.mult.toFixed(2)}배
            </span>
          ) : null}
        </div>
      ) : null}
      {frame.note ? <p className="card-hint">{frame.note}</p> : null}
    </div>
  );
}
