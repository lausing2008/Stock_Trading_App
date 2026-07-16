/**
 * Volume Profile — POC/VAH/VAL, high/low volume nodes, computed from existing OHLCV bars.
 *
 * This app has no bid/ask or tick-level trade data (see the T249-era investigation into
 * true footprint charts — yfinance/Alpha Vantage/the current Polygon integration are all
 * bars-only), so this uses the standard retail-tool approximation: each bar's volume is
 * distributed evenly across that bar's high-low price range, then accumulated into a fixed
 * number of price buckets across the profile's overall range. This is the same technique
 * "Session VP" and "Fixed Range VP" both need as input — just a different slice of bars.
 */
import type { Price } from './api';

export type VolumeProfileBucket = {
  priceLow: number;
  priceHigh: number;
  /** Bucket midpoint — used for POC/VAH/VAL comparisons and chart y-positioning. */
  price: number;
  volume: number;
};

export type VolumeProfileResult = {
  buckets: VolumeProfileBucket[];
  /** Point of Control — the single price bucket with the most volume. */
  poc: number;
  /** Value Area High — top of the range containing valueAreaPct of total volume, centered on POC. */
  vah: number;
  /** Value Area Low — bottom of that same range. */
  val: number;
  /** High Volume Nodes — local volume peaks, price levels likely to act as support/resistance. */
  hvn: number[];
  /** Low Volume Nodes — local volume troughs, price levels the market moved through quickly. */
  lvn: number[];
  totalVolume: number;
};

const DEFAULT_BUCKETS = 24;
const DEFAULT_VALUE_AREA_PCT = 0.70; // standard 70% value area, matches TradingView's default

/**
 * Computes a volume profile from a slice of OHLCV bars.
 *
 * @param bars       The bars to profile — caller decides the range (a session's bars for
 *                   "Session VP", a user-selected date range for "Fixed Range VP").
 * @param numBuckets Number of price buckets to distribute volume into (more buckets = finer
 *                   resolution, but noisier with few bars). 24 is a reasonable default for
 *                   a few weeks of daily bars; a longer/denser range may want more.
 */
export function computeVolumeProfile(
  bars: Price[],
  numBuckets: number = DEFAULT_BUCKETS,
  valueAreaPct: number = DEFAULT_VALUE_AREA_PCT,
): VolumeProfileResult | null {
  if (bars.length === 0) return null;

  const rangeHigh = Math.max(...bars.map(b => +b.high));
  const rangeLow = Math.min(...bars.map(b => +b.low));
  if (!(rangeHigh > rangeLow)) return null; // degenerate (single flat price) — nothing to bucket

  const bucketSize = (rangeHigh - rangeLow) / numBuckets;
  const volumes = new Array(numBuckets).fill(0);

  for (const bar of bars) {
    const high = +bar.high, low = +bar.low, volume = +bar.volume;
    if (volume <= 0) continue;
    const barRange = high - low;
    // Distribute this bar's volume evenly across every bucket its high-low range touches —
    // the standard approximation absent real trade-price data. A bar with zero range
    // (high === low) drops its full volume into the single bucket containing that price.
    const firstBucket = Math.max(0, Math.min(numBuckets - 1, Math.floor((low - rangeLow) / bucketSize)));
    const lastBucket = Math.max(0, Math.min(numBuckets - 1, Math.floor((high - rangeLow) / bucketSize)));
    if (barRange <= 0 || firstBucket === lastBucket) {
      volumes[firstBucket] += volume;
      continue;
    }
    const bucketsTouched = lastBucket - firstBucket + 1;
    const volumePerBucket = volume / bucketsTouched;
    for (let i = firstBucket; i <= lastBucket; i++) {
      volumes[i] += volumePerBucket;
    }
  }

  const buckets: VolumeProfileBucket[] = volumes.map((volume, i) => {
    const priceLow = rangeLow + i * bucketSize;
    const priceHigh = priceLow + bucketSize;
    return { priceLow, priceHigh, price: (priceLow + priceHigh) / 2, volume };
  });

  const totalVolume = volumes.reduce((s, v) => s + v, 0);
  if (totalVolume <= 0) return null;

  // POC: bucket with the most volume
  let pocIdx = 0;
  for (let i = 1; i < buckets.length; i++) {
    if (buckets[i].volume > buckets[pocIdx].volume) pocIdx = i;
  }
  const poc = buckets[pocIdx].price;

  // Value area: expand outward from POC, each step adding whichever neighboring bucket
  // (above or below the current area) has more volume, until valueAreaPct of total volume
  // is enclosed — the standard VAH/VAL algorithm.
  let lo = pocIdx, hi = pocIdx;
  let areaVolume = buckets[pocIdx].volume;
  while (areaVolume / totalVolume < valueAreaPct && (lo > 0 || hi < buckets.length - 1)) {
    const volBelow = lo > 0 ? buckets[lo - 1].volume : -1;
    const volAbove = hi < buckets.length - 1 ? buckets[hi + 1].volume : -1;
    if (volAbove >= volBelow) {
      hi++; areaVolume += buckets[hi].volume;
    } else {
      lo--; areaVolume += buckets[lo].volume;
    }
  }
  const val = buckets[lo].priceLow;
  const vah = buckets[hi].priceHigh;

  // HVN/LVN: local maxima/minima in the volume series (excluding the flat ends), only
  // among buckets with at least some volume — a strict interior peak/trough test.
  const hvn: number[] = [];
  const lvn: number[] = [];
  for (let i = 1; i < buckets.length - 1; i++) {
    const v = buckets[i].volume;
    if (v === 0) continue;
    const prev = buckets[i - 1].volume, next = buckets[i + 1].volume;
    if (v > prev && v > next) hvn.push(buckets[i].price);
    else if (v < prev && v < next) lvn.push(buckets[i].price);
  }

  return { buckets, poc, vah, val, hvn, lvn, totalVolume };
}

/** Slices bars to the current trading session — the most recent calendar date present. */
export function sessionBars(bars: Price[]): Price[] {
  if (bars.length === 0) return [];
  const lastDate = bars[bars.length - 1].ts.slice(0, 10);
  const start = bars.findIndex(b => b.ts.slice(0, 10) === lastDate);
  return start === -1 ? bars : bars.slice(start);
}
