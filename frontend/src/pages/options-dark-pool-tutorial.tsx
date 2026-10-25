import React, { useEffect, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { getSession, hasAdvancedAccess } from '@/lib/auth';
import type { OptionStrategy } from '@/lib/api';
import OptionCandidateComparison from '@/components/OptionCandidateComparison';

const topics = [
  ['start', '1. Open the Options workspace'], ['flow', '2. Read call/put activity'],
  ['strategies', '3. Choose an objective and holdings'], ['search', '4. Search expirations and delta ranges'],
  ['comparison', '5. Compare contracts and use the graph'], ['events', '6. Check dates and evidence'],
  ['pressure', '7. Read Market Pressure'], ['dark-pool', '8. Read off-exchange prints'],
  ['positions', '9. Understand held positions'], ['practice', 'Practice with sample contracts'],
  ['troubleshooting', 'Troubleshooting and final review'], ['references', 'Related guides and sources'],
] as const;
function Lesson({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return <section id={id} className="options-tutorial-section"><h2>{title}</h2>{children}</section>;
}
function ExampleCall(strike: number, premium: number, spread: number, oi: number, delta: number, gamma: number): OptionStrategy {
  return { name: 'Covered Call', direction: 'neutral-to-mildly-bullish', net: 'credit', requires_shares: true,
    legs: [{ action: 'sell', right: 'call', strike, price_per_share: premium, cost_per_contract: premium * 100,
      expiry: '2026-12-18', days_to_expiry: 46, option_symbol: `SAMPLE-CALL-${strike}`, price_source: 'quote_mid', spread_pct: spread, oi, delta, gamma }],
    net_per_share: premium, net_per_contract: premium * 100, max_loss_per_contract: (100 - premium) * 100,
    max_profit_per_contract: (strike - 100 + premium) * 100, breakeven: 100 - premium,
    breakeven_move_pct: -premium, collateral_per_contract: 10000,
    what_it_does: 'Fictional covered-call example at a $100 stock reference.', use_when: 'Practice only.' };
}
const demoOriginal = ExampleCall(110, 2, 10, 500, .3, .02);
const demoAlternatives = [ExampleCall(115, 1.5, 6.7, 1000, .22, .015), ExampleCall(105, 3.5, 2.9, 200, .45, .03)];

/** Exported for offline visual/interaction checks. The page below retains Learning's access guard. */
export function OptionsDarkPoolTutorialContent() {
  const [practiceSelection, setPracticeSelection] = useState<number | null>(null);
  return <main className="options-tutorial">
    <Head><title>Options &amp; Dark Pool Tutorial | StockAI</title></Head>
    <header className="options-tutorial-hero">
      <p className="options-tutorial-eyebrow">Learning · Stock-detail workflow</p>
      <h1>Options &amp; Dark Pool — Feature Tutorial</h1>
      <p>Use the new research controls, compare contracts, and understand what the data does and does not establish.</p>
      <div className="options-tutorial-links"><Link href="/stock/TSM?tab=options">Open TSM’s Options tab</Link><a href="#practice">Try the sample comparison</a></div>
    </header>
    <p className="options-tutorial-note">This lesson explains the app. It does not identify a proven best-return strategy or place orders. The default workspace uses settled research prices. Refresh can switch calculations to the provider’s latest available capture; entry still remains WAIT for fresh quotes and broker checks.</p>
    <nav aria-label="Tutorial topics" className="options-tutorial-toc">{topics.map(([id, title]) => <a key={id} href={`#${id}`}>{title}</a>)}</nav>

    <Lesson id="start" title="1. Open the Options workspace">
      <ol><li>Open any supported stock and select <strong>Options</strong>. The link above opens that tab directly for TSM.</li>
        <li>Start with source dates and feed status. A recent retrieval is different from a recent quote or trade.</li>
        <li>Use <strong>Options Game Plan</strong> for strategy comparisons, <strong>Options Flow</strong> for recorded activity, and <strong>Market Pressure</strong> for positioning and off-exchange samples.</li>
        <li>For updated option prices, use <strong>Refresh option chain &amp; recalculate</strong>. Choose all expirations, or one listed expiration for a smaller capture. A successful refresh switches the strategy cards, full chain and gain/loss option prices to the same fixed snapshot.</li></ol>
      <p>Check capture time separately from the stock’s reported time and each contract’s last trade. Last-trade time is not a quote timestamp. Markets closed can mean the latest response still contains the previous session. The snapshot expires after ten minutes; refresh again before seeking a preference. Failed or incomplete captures keep the previous display with a visible error. Use <strong>Use settled archive</strong> only when you deliberately want to return to archive mode.</p>
      <p>A single-expiration capture is limited to that expiry; it cannot establish the best contract across all expirations. The full capture also has explicit page/time/size limits. Flow, positioning and calendar panels keep their own source dates. A refresh resets strategy contract choices and assignment acceptance, while user-entered fills and calculator what-if prices remain yours.</p>
      <p>The computed workspace and this Learning page require Advanced access or an admin account. Missing provider data is shown explicitly; it is not filled in with invented measurements.</p>
    </Lesson>
    <Lesson id="flow" title="2. Read call/put activity without turning it into a forecast">
      <ul><li><strong>Call-heavy / Put-heavy / Mixed volume</strong> describes composition, not bullish/bearish conviction. The current C/P boundaries are 1.3 and 0.8, using the unrounded ratio.</li>
        <li><strong>Dominance summary:</strong> read volume and open interest separately. OI is outstanding contracts, not today’s traded volume. Paid-premium and buyer/seller rows remain unavailable when comparable event data is absent.</li>
        <li><strong>Source and coverage:</strong> check the settled session, expirations read and coverage status. Historical/expired expirations are not current trade candidates; an incomplete scope is not the whole market.</li>
        <li><strong>Paid premium vs estimated turnover:</strong> “unavailable” is not zero. Volume × quoted midpoint × 100 is an estimate, not an observed total paid.</li>
        <li><strong>Unknown IV/OI/volume:</strong> “—” means unmeasured. “ITM (est.)” uses a session reference plausibility check; it does not prove that old strikes and stored prices share a verified adjustment basis.</li></ul>
      <p>An unusual-activity shortlist is a screening aid. Missing premium must not become a fabricated premium ranking, and concentrated activity does not establish trade direction.</p>
    </Lesson>
    <Lesson id="strategies" title="3. Choose an objective and declare the relevant holdings">
      <p>Select the research horizon and enter a planned holding period of 1–365 calendar days. The starting windows are SHORT 7, SWING 14, LONG 28 and GROWTH 14 days: existing evaluation-window defaults, not optimal holding recommendations. The option needs that period plus a two-day research buffer before expiry.</p>
      <div className="options-tutorial-table-scroll" role="region" aria-label="Strategy requirements" tabIndex={0}><table><thead><tr><th>Strategy</th><th>What you declare</th><th>What to watch</th></tr></thead><tbody>
        <tr><th scope="row">Long call</th><td>A premium-loss budget covering the full debit</td><td>The whole option debit can be lost; direction alone is insufficient if the move is late or small.</td></tr>
        <tr><th scope="row">Protective put</th><td>At least 100 unencumbered shares and the option debit budget</td><td>Protection concerns stock plus put. The premium budget does not cap the entire stock position’s loss.</td></tr>
        <tr><th scope="row">Covered call</th><td>At least 100 unencumbered shares and acceptance of selling at the strike</td><td>Upside is capped; most stock downside remains. Shares must remain available for assignment.</td></tr>
        <tr><th scope="row">Cash-secured put</th><td>Strike × 100 cash reserved and acceptance of buying 100 shares</td><td>Assignment can produce a large stock loss; received premium is not the maximum loss.</td></tr>
      </tbody></table></div>
      <p>The highlight is <strong>Goal-fit only</strong>. It checks your objective and declarations. Account permissions, actual holdings, portfolio risk and deliverables are not verified. Recorded regime/trend/outlook evidence is shown with its date limitations; it is not a validated option-return forecast.</p>
    </Lesson>
    <Lesson id="search" title="4. Search expirations and delta ranges">
      <ol><li>Choose the strategy to inspect. Optional <strong>Minimum/Maximum days to expiry</strong> accepts whole calendar days from 1 to 1095.</li>
        <li>Optional <strong>Minimum/Maximum absolute delta</strong> accepts 0–1. For a put contract with delta −0.30, enter a magnitude such as 0.30, not −0.30. These are shared research bounds for all four strategies.</li>
        <li>Leave fields blank for no preference. A real zero is distinct from an unknown delta. An active delta range excludes missing, invalid and wrong-sign delta values.</li>
        <li>Click <strong>Find expirations for holding period</strong>. Editing fields does not fetch data; it clears old comparisons and pending results. Use <strong>Clear contract preferences</strong> to restore blank fields.</li></ol>
      <p>The search checks the earliest four eligible expirations and returns up to three alternatives per expiry. DTE/delta preferences are applied before those cuts. Read coverage and rejection counts explain missing results. A failed expiration is not “no activity”; a bounded search does not establish absence elsewhere.</p>
      <p>Original cards and chosen alternatives must also satisfy active preferences for goal-fit highlighting. No delta band is offered as a best-profit setting or a probability of profit.</p>
    </Lesson>
    <Lesson id="comparison" title="5. Compare contracts and use the strategy card or graph">
      <ol><li>Start with <strong>Contract research leads</strong>. Each strategy highlights one returned contract with a valid two-sided research quote, positive open interest and expiry fit. The rule favors a narrower displayed spread, then higher OI, then closeness to that strategy’s anchor and earlier expiry. This ranks quote quality within the returned shortlist, not expected return or probability of profit. A missing or expired capture, incomplete expiration reads or unacknowledged preference edit can leave no lead.</li>
        <li>Click <strong>Compare this contract</strong> to load a lead into its strategy card. Highlighting alone does not select it; clicking clears any earlier assignment acceptance. The entry status remains WAIT for fresh broker checks.</li>
        <li>Expand <strong>Compare candidate costs, risk and liquidity</strong>. The matching research-lead row is marked. The original reference stays first, including an unpriced original with unavailable payoff figures.</li>
        <li>Review strike, expiry/DTE, debit/credit, maximum loss/profit, expiry breakeven and stated capital. Covered-call capital includes stock at reference spot; protective-put debit excludes the separately required shares. Your stock cost basis is not inferred.</li>
        <li>Use <strong>Sort returned alternatives</strong> to inspect displayed spread, OI, capital or expiry. Unknown measurements sort last. Spread values are rounded; these are sorts of the returned shortlist, not a full-chain or expected-return ranking.</li>
        <li>Press <strong>Compare</strong> on the intended row, or choose it in the dropdown. That updates this strategy’s card and payoff illustration and clears assignment acceptance. Sorting alone preserves the same selected contract.</li>
        <li>Read the new card’s blockers and review assignment acceptance again. Your stock target/stop and combination legs remain unchanged.</li></ol>
      <p>Covered-call selection starts near the reference stock price. A stock forecast target is separate and never creates a listed strike. Archived contract identities and quote screens do not verify a current exchange listing or Fidelity availability; match the exact contract in your broker.</p>
      <p>For covered calls and cash-secured puts, open <strong>Why the loss is large — expiry scenarios</strong>. It separates premium from stock/assignment exposure and shows the stock-zero worst case, reference price, strike and other expiry prices. Enter a hypothetical expiry price to inspect its total P/L. A higher covered-call strike does not protect against a falling stock; cash-secured puts still carry the purchase obligation. These use reference spot, not your stock purchase cost. A new capture clears this local scenario input.</p>
      <p>Card loss labels distinguish stock-plus-call, put/assignment, hedge and option-debit risk. The payoff chart uses the same aggregate premium as the card and spans 75%–125% of reference spot; stock zero can be outside that window.</p>
      <p>Breakeven and payoff bounds are expiration arithmetic before fees, slippage and taxes. Contract delta/gamma are provider estimates, not combined stock-plus-option exposure. A model’s BUY label or confidence does not establish option profitability.</p>
      <details><summary>Why did TSM’s covered call originally appear unavailable?</summary><p>The screenshot’s target selected a nearby strike whose archived quote could not be priced. That did not prove every other call was unusable. Covered-call defaults now use the reference stock price instead of that target; usable alternatives still require explicit selection. If every preset is unpriced, a valid archive session/spot basis can still support an alternative search; a genuinely missing basis cannot be invented.</p></details>
    </Lesson>
    <Lesson id="events" title="6. Check quote and calendar evidence">
      <p>The <strong>Quote and event evidence</strong> panel separates archive session, calendar retrieval time and reported event dates. Per-quote observation time, quote size and independently timed reference spot remain unavailable in this plan. Ingestion time is not quote observation time.</p>
      <p>Reported earnings/ex-dividend dates are compared with both planned hold and the selected option’s entire lifetime. An event after your planned exit but before expiry still matters if you remain in the position.</p>
      <p>The optional <strong>Require a current calendar record with reported next earnings after expiration</strong> filter is off by default. When enabled, it requires a matched, timezone-qualified calendar retrieval no more than 24 hours old and a reported earnings date strictly after expiry. Missing, past, stale, future or malformed evidence cannot pass. The 24-hour rule checks retrieval freshness, not issuer confirmation or a complete event-free window.</p>
      <p>Early assignment is possible for short options; covered calls need particular review around ex-dividend dates. The app does not compute assignment probability. Verify issuer dates and broker requirements before entry.</p>
    </Lesson>
    <Lesson id="pressure" title="7. Read Market Pressure panels in context">
      <ul><li><strong>Dealer gamma levels:</strong> call/put walls, gamma flip and magnet are positioning references, not promised support/resistance or price targets.</li>
        <li><strong>OI by strike:</strong> the chart aggregates the displayed cross-expiry interest. The top-strike table is a subset; it is different from a single-expiry options chain.</li>
        <li><strong>Max pain by expiry:</strong> modeled expiration payout concentration, not a reliable prediction that price will move to that strike.</li>
        <li><strong>OI term structure / concentration:</strong> compare the shown expirations. “High” concentration is relative to those rows, not proof of unusual activity against a historical baseline.</li>
        <li><strong>NOPE:</strong> a delta-weighted pressure reading with its own timestamp. A refresh schedule does not make an old reading current; a pressure label alone is not an entry rule.</li></ul>
    </Lesson>
    <Lesson id="dark-pool" title="8. Read off-exchange prints and baseline quality">
      <p>The wide <strong>Off-exchange prints</strong> table shows executed time in ET and elapsed age, share size, price, estimated side, reported notional and venue/reporting code. On a narrow screen, focus or swipe the table horizontally; the page should not be forced wider.</p>
      <ul><li><strong>Off-exchange is broader than dark pools.</strong> A reporting-facility code does not prove the specific ATS or dark pool that executed a print.</li>
        <li><strong>Side (est.)</strong> is a quote-based inference, not a confirmed buyer/seller initiation record. A large “buy (est.)” is not a standalone buy signal.</li>
        <li><strong>Executed vs retrieved:</strong> the first dates the reported transaction; the second dates the feed retrieval. A recently retrieved old trade is still old. Invalid/future timestamps need review rather than a “just now” interpretation.</li>
        <li><strong>Baseline:</strong> compare the printed median, count, window and sample status. The current reference uses captured prints over 14 days and needs 20 for its relative bar; insufficient history or a query failure is not a zero median.</li>
        <li><strong>Sampling degraded:</strong> failed polls, saturated windows, missing session slots and identity exclusions limit the sample. A percentile is relative to captured eligible prints, not the whole market or guaranteed continuous tape.</li>
        <li><strong>Corrections/identity:</strong> retained versions and quarantined ambiguous events help prevent double-counted or uncertain baseline inputs. They do not prove the provider’s correction semantics or reconstruct every legacy print.</li></ul>
      <p>Read concentration and off-exchange evidence alongside your independent stock thesis and current data. The app does not infer a guaranteed direction from volume, a large print or a venue code.</p>
    </Lesson>
    <Lesson id="positions" title="9. Understand held positions and calculators">
      <p>A held fill can remain visible when its strike has no usable current quote: expiry payoff can use your entered fill, while current market value remains unavailable. Do not substitute a stale last trade for a current valuation.</p>
      <p>Automatic payoff tools assume supported standard 100-share terms. Adjusted or unidentified contracts can be refused; a readable matching root is not proof of the actual deliverable. A held unsupported contract is kept on record with a reason rather than silently deleted. An expired unsupported income position can show <strong>SETTLEMENT UNRESOLVED</strong>; approximate marks and returns are not a settled outcome.</p>
      <p>Use the <Link href="/options-calculator">options calculator</Link> and <Link href="/options-income-guide">income guide</Link> for those separate workflows. A comparison choice does not place or manage their orders.</p>
    </Lesson>
    <Lesson id="practice" title="Practice with sample contracts">
      <p className="options-tutorial-note"><strong>Made-up example, not TSM quotes.</strong> Assume 100 shares at a $100 stock reference. Scenario date: November 2, 2026; expiry: December 18, 2026. These fixed numbers teach the controls and never request market data.</p>
      <p>Open the sample table, sort by displayed spread, then compare an alternative. Notice that the narrowest-spread row does not have the largest maximum profit. Neither metric establishes the best return.</p>
      <OptionCandidateComparison original={demoOriginal} originalStrike={110} originalExpiry="2026-12-18" originalSymbol="SAMPLE-CALL-110"
        alternatives={demoAlternatives} strategyKey="covered_call" today="2026-11-02" selectedIndex={practiceSelection} onSelect={setPracticeSelection} />
      <p role="status" aria-label="Practice selection">Practice selection: {practiceSelection === null ? 'Original reference' : `Alternative ${practiceSelection + 1}`}. This changes only the lesson example.</p>
    </Lesson>
    <Lesson id="troubleshooting" title="Troubleshooting and final review">
      <dl><dt>Why does the screen say WAIT or no preference?</dt><dd>Choose an objective and review the card’s actual blockers: holdings, full debit/cash reserve, assignment acceptance, quote quality, source age, expiry/holding fit, active preferences or the optional calendar filter. WAIT is not overridden by a sorted row.</dd>
        <dt>Why are there no alternatives?</dt><dd>Read expiry coverage and rejection reasons. Bounds may conflict with the holding buffer, delta may be unknown, quotes may fail, or selected reads may have failed. Clearing a research preference does not repair a failed feed.</dd>
        <dt>What should I do after a timeout, 429 or server error?</dt><dd>Use the affected section’s Retry. Loading, unavailable, empty and failed are different states. Cached data beside a failure is labeled as not current. Hidden/offline polling pauses; rate limits can delay retries. Repeated clicks do not guarantee a fresh provider response.</dd>
        <dt>What should I verify before using a comparison?</dt><dd>Current two-sided broker quotes and size, correct deliverable, stock/option price basis, issuer event dates, account permissions, assignment funding and total portfolio risk. Archive research and a goal-fit highlight are not order approval.</dd></dl>
    </Lesson>
    <Lesson id="references" title="Related guides and sources">
      <div className="options-tutorial-links"><Link href="/option-trading-guide">Option trading basics</Link><Link href="/dark-pool-guide">Dark-pool background</Link><Link href="/options-income-guide">Options income workflow</Link></div>
      <p>Market-structure context: <a href="https://syndication.finra.org/content/where-do-stocks-trade" target="_blank" rel="noreferrer">FINRA: where stocks trade</a> and <a href="https://www.investor.gov/introduction-investing/investing-basics/glossary/alternative-trading-systems-atss" target="_blank" rel="noreferrer">Investor.gov: ATSs and dark pools</a>. Assignment context: <a href="https://www.optionseducation.org/referencelibrary/faq/options-assignment" target="_blank" rel="noreferrer">OIC: options assignment</a>. These sources do not validate the app’s settings or forecast returns.</p>
    </Lesson>
  </main>;
}
export default function OptionsDarkPoolTutorialPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (!hasAdvancedAccess(session)) { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);
  if (!authed) return null;
  return <OptionsDarkPoolTutorialContent />;
}
