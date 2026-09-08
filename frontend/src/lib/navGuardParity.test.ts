/**
 * AUD-ADMINPAGE-GUARDGAP + AUD-NAV-ROTATIONEXPLAINER-DEADLINK — nav visibility vs page guards.
 *
 * This repo has now hit the same bug from BOTH directions, which is why it is worth a
 * structural test rather than five one-off fixes:
 *
 *   TOO LOOSE — 4 pages in the `adminOnly: true` Admin group had no admin check.
 *     horizon-compare.tsx had no guard AT ALL (no getSession, no redirect), so it rendered a
 *     full admin analytics page for any logged-in user and for an unauthenticated visitor
 *     until its API call happened to 401. conditional-orders / paper-gates / signal-quality
 *     were login-only. Nav-hiding is a VISIBILITY restriction; the URL stays reachable.
 *
 *   TOO STRICT — watchlist-rotation-explainer.tsx sat in the Learning group
 *     (minTier: 'advanced') but required role=admin, so isGroupVisible() advertised it to
 *     every advanced-tier user and the page then bounced them to the dashboard with no
 *     explanation. A dead link the nav itself created.
 *
 * The invariant: a page's own guard must match the strictness of the nav group that links to
 * it. Reading the real _app.tsx nav tree and the real page sources means a NEW page inherits
 * this check automatically instead of relying on a reviewer noticing.
 */
import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';

const PAGES_DIR = path.join(__dirname, '..', 'pages');
const APP_SRC = fs.readFileSync(path.join(PAGES_DIR, '_app.tsx'), 'utf8');

type Group = { label: string; adminOnly: boolean; minTier: string | null; hrefs: string[] };

/** Parse the NAV_GROUPS literal out of _app.tsx by brace-matching each group object. */
function parseNavGroups(): Group[] {
  const groups: Group[] = [];
  const re = /\{\s*\n\s*label: '([^']+)',/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(APP_SRC)) !== null) {
    // Walk braces from this group's opening `{` to find its full extent.
    let depth = 0;
    let i = m.index;
    for (; i < APP_SRC.length; i++) {
      if (APP_SRC[i] === '{') depth++;
      else if (APP_SRC[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    const block = APP_SRC.slice(m.index, i);
    if (!block.includes('items:')) continue; // a nav ITEM, not a group
    const tier = /minTier: '([^']+)'/.exec(block);
    groups.push({
      label: m[1],
      adminOnly: /adminOnly: true/.test(block),
      minTier: tier ? tier[1] : null,
      hrefs: [...block.matchAll(/href: '(\/[^']*)'/g)].map((h) => h[1]),
    });
  }
  return groups;
}

const GROUPS = parseNavGroups();

function pageSource(href: string): string | null {
  const base = href.replace(/^\//, '');
  for (const cand of [`${base}.tsx`, path.join(base, 'index.tsx')]) {
    const p = path.join(PAGES_DIR, cand);
    if (fs.existsSync(p)) return fs.readFileSync(p, 'utf8');
  }
  return null;
}

describe('nav parsing sanity', () => {
  it('found the real nav groups', () => {
    const labels = GROUPS.map((g) => g.label);
    expect(labels).toContain('Admin');
    expect(labels).toContain('Learning');
  });

  it('identified the gating metadata', () => {
    expect(GROUPS.find((g) => g.label === 'Admin')!.adminOnly).toBe(true);
    expect(GROUPS.find((g) => g.label === 'Learning')!.minTier).toBe('advanced');
  });

  it('found a realistic number of gated pages', () => {
    // Guards against a regex that silently matches nothing, which would make every test below
    // vacuously pass — the exact failure mode that lets this bug class survive a test suite.
    expect(GROUPS.find((g) => g.label === 'Admin')!.hrefs.length).toBeGreaterThan(15);
    expect(GROUPS.find((g) => g.label === 'Learning')!.hrefs.length).toBeGreaterThan(5);
  });
});

describe('AUD-ADMINPAGE-GUARDGAP: every adminOnly nav page enforces admin', () => {
  const admin = GROUPS.find((g) => g.label === 'Admin')!;
  const targets = admin.hrefs.filter((h) => pageSource(h) !== null);

  it.each(targets)('%s checks for admin, not just login', (href) => {
    const src = pageSource(href)!;
    const checksAdmin = /isAdmin\s*\(/.test(src) || /role\s*!==\s*'admin'/.test(src)
      || /role\s*===\s*'admin'/.test(src);
    expect(checksAdmin).toBe(true);
  });

  it.each(targets)('%s redirects rather than merely hiding content', (href) => {
    const src = pageSource(href)!;
    expect(/router\.replace\(/.test(src)).toBe(true);
  });

  it.each(targets)('%s reads the session at all', (href) => {
    // horizon-compare.tsx had NO getSession() call anywhere.
    expect(/getSession\s*\(/.test(pageSource(href)!)).toBe(true);
  });
});

describe('AUD-NAV-ROTATIONEXPLAINER-DEADLINK: advanced-tier pages are not secretly admin-only', () => {
  const learning = GROUPS.find((g) => g.label === 'Learning')!;
  const targets = learning.hrefs.filter((h) => pageSource(h) !== null);

  it.each(targets)('%s gates on tier, not on role=admin', (href) => {
    const src = pageSource(href)!;
    // An admin-only check on a minTier:'advanced' page makes the nav item a dead link for
    // exactly the users the nav shows it to.
    expect(/role\s*!==\s*'admin'/.test(src)).toBe(false);
  });

  it('watchlist-rotation-explainer specifically uses hasAdvancedAccess', () => {
    const src = pageSource('/watchlist-rotation-explainer')!;
    expect(src).toContain('hasAdvancedAccess');
    expect(/role\s*!==\s*'admin'/.test(src)).toBe(false);
  });

  it.each(targets)('%s still guards access somehow', (href) => {
    const src = pageSource(href)!;
    expect(/hasAdvancedAccess\s*\(/.test(src)).toBe(true);
  });
});

describe('the two helpers stay semantically distinct', () => {
  const AUTH = fs.readFileSync(path.join(__dirname, 'auth.ts'), 'utf8');

  it('hasAdvancedAccess admits advanced tier OR admin', () => {
    expect(AUTH).toContain("session?.tier === 'advanced' || session?.role === 'admin'");
  });

  it('isAdmin is role-only and must not drift into a tier check', () => {
    const fn = AUTH.slice(AUTH.indexOf('export function isAdmin'));
    const body = fn.slice(0, fn.indexOf('}'));
    expect(body).toContain("role === 'admin'");
    expect(body).not.toContain('tier');
  });
});
