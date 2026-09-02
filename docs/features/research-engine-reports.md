## Feature Reference: Research Tab on the Stock Detail Page (Built 2026-07-29)

**User ask**: "I don't see the research tab under stock detail page" — the stock detail page
(`frontend/src/pages/stock/[symbol].tsx`) never had a real tab system at all (unlike e.g.
`intelligence.tsx`), only a small "Research Intelligence" sidebar card linking out to a
separate `/research/[symbol]` URL — easy to miss, and not what "tab" implies.

**Implementation — reuses the existing `/research/[symbol]` page component directly, rather
than re-implementing report rendering a second time**: `ResearchPage` (the default export of
`frontend/src/pages/research/[symbol].tsx`) reads its symbol from `router.query.symbol` — the
exact same dynamic route param name `stock/[symbol].tsx` already has on its own URL. This
meant `<ResearchPage />` could be imported and rendered directly inside a new "Research" tab
with **zero prop-passing** — no refactor of either page's internals, no shared-component
extraction needed.

**The stock detail page's entire existing body is a single `return ( <div className="space-y-4">
... 4000+ lines ... </div> )`** with no tab abstraction to hook into. Rather than risk a large,
error-prone edit re-indenting thousands of lines, the fix wraps the ENTIRE pre-existing return
value verbatim inside a new `{pageTab === 'Overview' && ( ...unchanged... )}` conditional,
adding a small tab-bar `<div>` and a sibling `{pageTab === 'Research' && <ResearchPage />}`
right before it — the existing "Overview" content is byte-for-byte unchanged, just
conditionally rendered.

**Verification**: `npx tsc --noEmit` clean on the first attempt (confirming the JSX braces
balanced correctly despite the large wrap), full 89-test Vitest suite unaffected, and — the
real proof this actually works, since typecheck alone doesn't catch a hooks-order/duplicate-
`useRouter` conflict from rendering one page's component inside another — a full `next build`
compiled clean, with `/research/[symbol]`'s own bundle shrinking from ~12kB to 193B (Next.js
correctly deduping the now-shared component into a common chunk) and `/stock/[symbol]` growing
only ~200B. Confirmed the tab bar's actual labels ("Overview"/"Research") are present in the
real compiled `stock/[symbol]-*.js` chunk, not just correct-looking in source.

**Deliberately not touched**: the small "Research Intelligence" sidebar card on the Overview
tab (recommendation badge, alignment vs. AI signal, conviction score) — that's a genuinely
different, complementary summary (cross-referencing the signal against the research verdict),
not a duplicate of the new full-report tab, so it stays exactly as it was.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'Overview.,.Research' /app/.next/static/chunks/pages/stock/\[symbol\]-*.js"
```
If the Research tab shows a blank/error state that `/research/SYMBOL` directly does not, check
the browser console for a hooks-related React warning first — that would indicate the
component-reuse approach hit an edge case this build-time check didn't catch.

---

