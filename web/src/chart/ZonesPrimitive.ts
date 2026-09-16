/**
 * 시간·가격으로 막은 사각 영역 — 손절 구간(붉은) · 익절 구간(초록) 을 봉 **아래**에 칠한다.
 *
 * lightweight-charts 에 사각형 개념이 없어 프리미티브로 만든다. `Chart.tsx` 의 `BandsPrimitive` 는 화면 폭 전체에
 * 가격 띠를 깔지만, 매매 영역은 **그 매매의 시간 구간**에만 칠해야 한다 — 그래서 x 도 좌표로 바꾼다.
 *
 * 화면 밖 시각은 `timeToCoordinate` 가 `null` 을 준다. 보이는 범위와 견줘 왼쪽/오른쪽 끝으로 붙인다 — 그래야 매매
 * 구간이 화면보다 길 때도 띠가 끊기지 않는다.
 */
import type {
  IChartApi,
  Logical,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

/** `stroke` 가 있으면 테두리도 그린다(고른 매매 상자) · `dashed` 면 점선 테두리(아직 열린 매매). */
export type Rect = { from: number; to: number; low: number; high: number; color: string; stroke?: string; dashed?: boolean };

type Scope = {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
};

export class ZonesPrimitive implements ISeriesPrimitive<Time> {
  private rects: Rect[] = [];
  private series: ISeriesApi<"Candlestick"> | null = null;
  private chart: IChartApi | null = null;
  private refresh: (() => void) | null = null;

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.chart = param.chart;
    this.refresh = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
    this.refresh = null;
  }

  set(rects: Rect[]): void {
    this.rects = rects;
    this.refresh?.();
  }

  /** 시각 → x 좌표. 화면 밖이면 보이는 범위의 어느 쪽인지 보고 끝으로 붙인다. 못 정하면 `null`. */
  private x(time: number, width: number): number | null {
    const chart = this.chart;
    if (chart === null) return null;
    const scale = chart.timeScale();
    const got = scale.timeToCoordinate(time as Time);
    if (got !== null) return got;
    // 자료 점이 아닌 시각(빈 자리 · 자료 끝 너머)은 가장 가까운 봉의 논리 좌표로.
    const index = scale.timeToIndex(time as Time, true);
    if (index !== null) {
      const at = scale.logicalToCoordinate(index as unknown as Logical);
      if (at !== null) return at;
    }
    const visible = scale.getVisibleRange();
    if (visible === null) return null;
    if (time < (visible.from as number)) return 0;
    if (time > (visible.to as number)) return width;
    return null;
  }

  paneViews() {
    const owner = this;
    return [
      {
        zOrder(): "bottom" {
          return "bottom";
        },
        renderer() {
          return {
            draw(target: { useBitmapCoordinateSpace: (fn: (scope: Scope) => void) => void }) {
              const series = owner.series;
              if (series === null || owner.rects.length === 0) return;
              target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio, verticalPixelRatio }) => {
                const cssWidth = bitmapSize.width / horizontalPixelRatio;
                for (const r of owner.rects) {
                  const top = series.priceToCoordinate(r.high);
                  const bottom = series.priceToCoordinate(r.low);
                  const left = owner.x(r.from, cssWidth);
                  const right = owner.x(r.to, cssWidth);
                  if (top === null || bottom === null || left === null || right === null) continue;
                  if (right <= left) continue;
                  const x = left * horizontalPixelRatio;
                  const y = top * verticalPixelRatio;
                  const w = Math.max(1, (right - left) * horizontalPixelRatio);
                  const h = Math.max(1, (bottom - top) * verticalPixelRatio);
                  context.fillStyle = r.color;
                  context.fillRect(x, y, w, h);
                  if (r.stroke !== undefined) {
                    context.save();
                    context.strokeStyle = r.stroke;
                    context.lineWidth = Math.max(1, horizontalPixelRatio);
                    context.setLineDash(r.dashed === true ? [4 * horizontalPixelRatio, 3 * horizontalPixelRatio] : []);
                    context.strokeRect(x + 0.5, y + 0.5, Math.max(1, w - 1), Math.max(1, h - 1));
                    context.restore();
                  }
                }
              });
            },
          };
        },
      },
    ];
  }
}
