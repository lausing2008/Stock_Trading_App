// T412-GAINLOSS: the strike-by-strike gain/loss table on the Options Calculator.
//
// USER REQUEST: expiration-date buttons, and a table showing the gain or loss for different
// strike prices — the shape of the worked example in every options primer:
//
//   Strike | Premium | Cost (1 contract) | Value at $120 | Net P/L | ROI
//
// ...extended, on the user's follow-up, to ALL FOUR basic positions: buy call, sell call, buy
// put, sell put.
//
// WHAT MAKES THIS ONE DIFFERENT FROM THE PRIMER. The premiums are REAL — the quoted NBBO
// midpoint for that strike at the chosen expiry, from this platform's own chain endpoint — not
// the round made-up numbers a textbook uses. That is the whole value of attaching it to an
// expiry picker, and it is also why each row carries its own quote quality: a strike whose bid
// and ask are 40% apart is not tradeable at the midpoint the row shows, and a table that hid
// that would quietly promise a fill nobody can get.
//
// THE THING THE FOUR-POSITION VERSION MUST NOT GET WRONG: ROI's DENOMINATOR.
//
// For a LONG option, return on premium paid is the right measure — the premium is the whole
// outlay and the whole risk. For a SHORT option it is not, and using it would be actively
// misleading: every short that expires worthless keeps 100% of the premium, so a "return on
// premium" column would read +100% on nearly every row and make selling look like free money.
// The honest denominator for a short is the CAPITAL THE POSITION TIES UP:
//
//   buy call / buy put   -> premium paid            (the outlay, and the maximum loss)
//   sell put             -> strike x 100            (cash-secured: the cash that must be posted)
//   sell call            -> spot x 100              (covered: the shares that must be held)
//
// which matches the collateral convention the calculator's own capital-required figure already
// uses, so the two halves of this page cannot disagree.
//
// Max loss differs by position too, and one of the four is genuinely unbounded — a naked short
// call has no worst case, and a number in that cell would be a lie. It reports "unbounded".
//
// Still an EXPIRY calculation, matching the rest of this page: value at the target price is
// intrinsic value, because that is all an option is worth once expired. No Black-Scholes, no
// time value, no Greeks — this app has no IV surface and inventing one here would be the
// dishonest part.

export type OptionAction = 'buy' | 'sell';
export type OptionRight = 'call' | 'put';

export interface ChainQuote {
  strike: number;
  bid: number;
  ask: number;
  last_price: number;
  oi: number;
  volume: number;
  iv: number;
}

export interface GainLossRow {
  strike: number;
  action: OptionAction;
  right: OptionRight;
  /** NBBO midpoint, or last price when the quote is one-sided. */
  premium: number;
  premiumSource: 'mid' | 'last';
  /** Bid/ask spread as a percentage of the midpoint. null when there is no two-sided quote. */
  spreadPct: number | null;
  /** Negative when you pay (a debit), positive when you receive (a credit). */
  cashFlow: number;
  /** What the position ties up — the ROI denominator. See the header note. */
  capitalBasis: number;
  capitalLabel: 'premium paid' | 'cash secured' | 'shares held';
  /** The option's intrinsic value at the target price, per the contract count. Always >= 0:
   * this is what the CONTRACT is worth, independent of which side of it you are on. */
  valueAtTarget: number;
  /** Your profit or loss, signed for your side of the trade. */
  netPL: number;
  roiPct: number;
  /** null means genuinely unbounded — a naked short call. Not "unknown", not zero. */
  maxLoss: number | null;
  /** The underlying price at which this position breaks even at expiry. */
  breakeven: number;
  /** How far the underlying must travel from spot to reach breakeven, as a signed percentage.
   * Negative means it can FALL that far and still break even (a short call, a long put). */
  breakevenMovePct: number | null;
  moneyness: 'ITM' | 'ATM' | 'OTM';
  /** True when nothing has traded and nothing is open — the premium is a quote nobody has taken. */
  illiquid: boolean;
}

const CONTRACT = 100;

/** How close to spot counts as at-the-money, as a fraction. Presentation only — it labels a row,
 * it does not change any arithmetic. */
const ATM_BAND = 0.01;

/** A midpoint is only meaningful with a two-sided quote. With one side missing the midpoint would
 * be half the real price, so fall back to the last traded price and say which was used. */
export function premiumFor(q: ChainQuote): { premium: number; source: 'mid' | 'last'; spreadPct: number | null } {
  const bid = Number(q.bid) || 0;
  const ask = Number(q.ask) || 0;
  if (bid > 0 && ask > 0 && ask >= bid) {
    const mid = (bid + ask) / 2;
    return { premium: mid, source: 'mid', spreadPct: mid > 0 ? ((ask - bid) / mid) * 100 : null };
  }
  return { premium: Number(q.last_price) || 0, source: 'last', spreadPct: null };
}

/** Intrinsic value per share at expiry. This IS the option's whole value once expired. */
export function intrinsic(right: OptionRight, strike: number, priceAtExpiry: number): number {
  return right === 'call'
    ? Math.max(0, priceAtExpiry - strike)
    : Math.max(0, strike - priceAtExpiry);
}

export function moneynessOf(right: OptionRight, strike: number, spot: number): 'ITM' | 'ATM' | 'OTM' {
  if (spot > 0 && Math.abs(strike - spot) / spot <= ATM_BAND) return 'ATM';
  if (right === 'call') return strike < spot ? 'ITM' : 'OTM';
  return strike > spot ? 'ITM' : 'OTM';
}

/** The capital a position ties up, and what to call it. See the header note on why a short
 * cannot use premium as its ROI denominator. */
export function capitalFor(
  action: OptionAction, right: OptionRight, strike: number, premium: number, spot: number, contracts: number,
): { basis: number; label: GainLossRow['capitalLabel'] } {
  if (action === 'buy') {
    return { basis: premium * CONTRACT * contracts, label: 'premium paid' };
  }
  if (right === 'put') {
    return { basis: strike * CONTRACT * contracts, label: 'cash secured' };
  }
  return { basis: spot * CONTRACT * contracts, label: 'shares held' };
}

/** The worst case at expiry, or null when there isn't one.
 *
 * - long call / long put: the premium paid. Bounded, and it is the whole risk.
 * - short put: the stock goes to zero, so you are assigned at the strike having collected the
 *   premium — (strike - premium) x 100.
 * - short call: NULL. There is no highest price a stock can reach, so there is no maximum loss.
 *   Every other cell in this table is a number; this one must not be, because any number here
 *   would understate it.
 */
export function maxLossFor(
  action: OptionAction, right: OptionRight, strike: number, premium: number, contracts: number,
): number | null {
  if (action === 'buy') return premium * CONTRACT * contracts;
  if (right === 'put') return Math.max(0, strike - premium) * CONTRACT * contracts;
  return null;
}

/** The underlying price at which the position breaks even at expiry.
 *
 * Identical for both sides of the same contract, which is the point: a buyer and a seller of the
 * same option break even at exactly the same price — it is the price at which the contract's
 * intrinsic value equals the premium that changed hands. Above it the buyer profits and the
 * seller loses; below it, the reverse.
 *
 *   call -> strike + premium
 *   put  -> strike - premium
 *
 * This is the single number that says how far the stock actually has to move for the trade to be
 * worth doing, which no other column states directly: a row can show a large ROI at an assumed
 * target while requiring a move the stock has never made in a month.
 */
export function breakevenFor(right: OptionRight, strike: number, premium: number): number {
  return right === 'call' ? strike + premium : strike - premium;
}

/** One table row per strike, for the given position held to expiry.
 *
 * Rows whose premium is zero or missing are DROPPED rather than shown with an infinite or
 * meaningless ROI. A strike with no price is not a cheaper trade, it is an unquoted one.
 */
export function buildGainLossRows(
  quotes: ChainQuote[],
  action: OptionAction,
  right: OptionRight,
  spot: number,
  targetPrice: number,
  contracts = 1,
): GainLossRow[] {
  const out: GainLossRow[] = [];
  for (const q of quotes) {
    const strike = Number(q.strike);
    if (!Number.isFinite(strike) || strike <= 0) continue;
    const { premium, source, spreadPct } = premiumFor(q);
    if (!(premium > 0)) continue;

    const grossPremium = premium * CONTRACT * contracts;
    const valueAtTarget = intrinsic(right, strike, targetPrice) * CONTRACT * contracts;

    // A buyer pays the premium and owns the intrinsic value; a seller collects the premium and
    // owes it. One expression, signed — not two code paths that can drift apart.
    const sign = action === 'buy' ? 1 : -1;
    const netPL = sign * (valueAtTarget - grossPremium);

    const { basis, label } = capitalFor(action, right, strike, premium, spot, contracts);

    out.push({
      strike,
      action,
      right,
      premium,
      premiumSource: source,
      spreadPct: spreadPct == null ? null : Number(spreadPct.toFixed(1)),
      cashFlow: sign * -grossPremium,
      capitalBasis: basis,
      capitalLabel: label,
      valueAtTarget,
      netPL,
      roiPct: basis > 0 ? Number(((netPL / basis) * 100).toFixed(1)) : 0,
      maxLoss: maxLossFor(action, right, strike, premium, contracts),
      breakeven: Number(breakevenFor(right, strike, premium).toFixed(2)),
      breakevenMovePct: spot > 0
        ? Number((((breakevenFor(right, strike, premium) - spot) / spot) * 100).toFixed(1))
        : null,
      moneyness: moneynessOf(right, strike, spot),
      illiquid: (Number(q.oi) || 0) === 0 && (Number(q.volume) || 0) === 0,
    });
  }
  return out.sort((a, b) => a.strike - b.strike);
}

/** Keep the `count` strikes nearest to spot, then restore ascending strike order.
 *
 * A real chain runs to a hundred strikes, most of them far enough out to be noise. Centring on
 * spot rather than truncating from one end keeps both sides of the money visible — a table of
 * only deep-OTM strikes would make every row look like a lottery ticket.
 */
export function nearestStrikes(rows: GainLossRow[], spot: number, count = 12): GainLossRow[] {
  if (rows.length <= count) return rows;
  return [...rows]
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
    .slice(0, count)
    .sort((a, b) => a.strike - b.strike);
}

/** The strike with the highest ROI at the target price, or null when nothing profits.
 *
 * Deliberately NOT called "best". For a long position the highest ROI at one assumed price is the
 * row that needed the most movement to get there, and it is the first to expire worthless if that
 * movement does not arrive. For a short it is typically the row carrying the most risk. Naming it
 * a winner would turn an arithmetic identity into a recommendation.
 */
export function highestRoi(rows: GainLossRow[]): GainLossRow | null {
  const profitable = rows.filter(r => r.netPL > 0);
  if (!profitable.length) return null;
  return profitable.reduce((best, r) => (r.roiPct > best.roiPct ? r : best));
}

/** Days from today to an ISO expiry date, in whole calendar days.
 *
 * Both sides are reduced to a UTC date built from date PARTS, so this never shifts by one in a
 * timezone west of UTC — an expiry is a trading date, not an instant, and comparing it against a
 * local wall-clock instant is how a 10-day expiry renders as 9.
 */
export function daysToExpiry(expiry: string, today: Date = new Date()): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(expiry);
  if (!m) return null;
  const exp = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((exp - now) / 86400000);
}
