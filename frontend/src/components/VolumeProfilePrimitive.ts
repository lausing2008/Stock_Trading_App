/**
 * VolumeProfilePrimitive — lightweight-charts v4 series primitive rendering a volume
 * profile as a horizontal-bar histogram anchored to the left edge of the profiled range.
 *
 * Adapted from TradingView's own official plugin example
 * (github.com/tradingview/lightweight-charts, plugin-examples/src/plugins/volume-profile) —
 * same draw()/update() split (IPrimitivePaneRenderer/IPrimitivePaneView), same
 * priceToCoordinate()-per-bucket approach for y-positioning, adapted here to render the
 * full bucket list computed by computeVolumeProfile() rather than the original example's
 * single hardcoded two-point profile, and themed to match this app's dark chart palette.
 */
import type {
  AutoscaleInfo,
  Coordinate,
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  Logical,
  SeriesOptionsMap,
  SeriesType,
  Time,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { VolumeProfileResult } from '@/lib/volumeProfile';

export type VolumeProfilePrimitiveData = {
  /** Anchor time — the left edge of the histogram bars. Typically the first bar of the profiled range. */
  time: Time;
  profile: VolumeProfileResult;
  /** Width of the widest bar, in bar-spacing units (e.g. 30 = 30 candle-widths wide). */
  width: number;
};

function positionsBox(position1Media: number, position2Media: number, pixelRatio: number): { position: number; length: number } {
  const min = Math.round(Math.min(position1Media, position2Media) * pixelRatio);
  const max = Math.round(Math.max(position1Media, position2Media) * pixelRatio);
  return { position: min, length: Math.max(1, max - min) };
}

type RendererItem = { y: Coordinate; height: number; width: number };

type RendererData = {
  x: Coordinate | null;
  bgTop: Coordinate | null;
  bgBottom: Coordinate | null;
  width: number;
  items: RendererItem[];
  pocY: Coordinate | null;
  vahY: Coordinate | null;
  valY: Coordinate | null;
};

class VolumeProfileRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private _data: RendererData) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useBitmapCoordinateSpace(scope => {
      const { x, bgTop, bgBottom, width, items, pocY, vahY, valY } = this._data;
      if (x === null || bgTop === null || bgBottom === null) return;
      const ctx = scope.context;

      // Value-area background band (POC ± value area) drawn first, behind the bars
      if (vahY !== null && valY !== null) {
        const vaPos = positionsBox(vahY, valY, scope.verticalPixelRatio);
        const xPos = positionsBox(x, x + width, scope.horizontalPixelRatio);
        ctx.fillStyle = 'rgba(99, 102, 241, 0.08)';
        ctx.fillRect(xPos.position, vaPos.position, xPos.length, vaPos.length);
      }

      for (const item of items) {
        const yPos = positionsBox(item.y, (item.y as number) - item.height, scope.verticalPixelRatio);
        const xPos = positionsBox(x, x + item.width, scope.horizontalPixelRatio);
        ctx.fillStyle = 'rgba(96, 165, 250, 0.55)';
        ctx.fillRect(xPos.position, yPos.position, xPos.length, Math.max(1, yPos.length - 1));
      }

      // POC line — full profile width, brighter
      if (pocY !== null) {
        const linePos = positionsBox(pocY - 1, pocY + 1, scope.verticalPixelRatio);
        const xPos = positionsBox(x, x + width, scope.horizontalPixelRatio);
        ctx.fillStyle = 'rgba(251, 191, 36, 0.9)';
        ctx.fillRect(xPos.position, linePos.position, xPos.length, linePos.length);
      }
    });
  }
}

class VolumeProfilePaneView implements ISeriesPrimitivePaneView {
  private _x: Coordinate | null = null;
  private _bgTop: Coordinate | null = null;
  private _bgBottom: Coordinate | null = null;
  private _width = 0;
  private _items: RendererItem[] = [];
  private _pocY: Coordinate | null = null;
  private _vahY: Coordinate | null = null;
  private _valY: Coordinate | null = null;

  constructor(private _source: VolumeProfilePrimitive) {}

  update() {
    const data = this._source.data;
    if (!data) { this._items = []; return; }
    const series = this._source.series;
    const timeScale = this._source.chart.timeScale();

    this._x = timeScale.timeToCoordinate(data.time);
    this._width = timeScale.options().barSpacing * data.width;

    const { profile } = data;
    const maxVolume = Math.max(...profile.buckets.map(b => b.volume), 1);

    this._items = profile.buckets
      .map((b): RendererItem | null => {
        const yTop = series.priceToCoordinate(b.priceHigh);
        const yBottom = series.priceToCoordinate(b.priceLow);
        if (yTop === null || yBottom === null) return null;
        return {
          y: yBottom,
          height: Math.max(1, (yBottom as number) - (yTop as number)),
          width: (this._width * b.volume) / maxVolume,
        };
      })
      .filter((x): x is RendererItem => x !== null);

    this._pocY = series.priceToCoordinate(profile.poc);
    this._vahY = series.priceToCoordinate(profile.vah);
    this._valY = series.priceToCoordinate(profile.val);
    const topPrice = profile.buckets[profile.buckets.length - 1]?.priceHigh;
    const bottomPrice = profile.buckets[0]?.priceLow;
    this._bgTop = topPrice !== undefined ? series.priceToCoordinate(topPrice) : null;
    this._bgBottom = bottomPrice !== undefined ? series.priceToCoordinate(bottomPrice) : null;
  }

  renderer() {
    return new VolumeProfileRenderer({
      x: this._x,
      bgTop: this._bgTop,
      bgBottom: this._bgBottom,
      width: this._width,
      items: this._items,
      pocY: this._pocY,
      vahY: this._vahY,
      valY: this._valY,
    });
  }
}

export class VolumeProfilePrimitive implements ISeriesPrimitive<Time> {
  data: VolumeProfilePrimitiveData | null = null;
  private _paneViews: VolumeProfilePaneView[];

  constructor(
    public chart: IChartApi,
    public series: ISeriesApi<keyof SeriesOptionsMap>,
  ) {
    this._paneViews = [new VolumeProfilePaneView(this)];
  }

  setData(data: VolumeProfilePrimitiveData | null) {
    this.data = data;
    this.updateAllViews();
  }

  updateAllViews() {
    this._paneViews.forEach(pv => pv.update());
  }

  paneViews() {
    return this._paneViews;
  }

  autoscaleInfo(startTimePoint: Logical, endTimePoint: Logical): AutoscaleInfo | null {
    if (!this.data) return null;
    const vpCoordinate = this.chart.timeScale().timeToCoordinate(this.data.time);
    if (vpCoordinate === null) return null;
    const vpIndex = this.chart.timeScale().coordinateToLogical(vpCoordinate);
    if (vpIndex === null) return null;
    // Only expand autoscale while the profile's anchor is in/near the visible range —
    // an off-screen profile shouldn't force the y-axis to include its price range.
    if (endTimePoint < vpIndex || startTimePoint > vpIndex + this.data.width) return null;
    const buckets = this.data.profile.buckets;
    if (buckets.length === 0) return null;
    return {
      priceRange: {
        minValue: buckets[0].priceLow,
        maxValue: buckets[buckets.length - 1].priceHigh,
      },
    };
  }
}
