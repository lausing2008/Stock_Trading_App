# Frontend — Domain Knowledge & Coding Standards

Next.js 13+ app with TypeScript. Single API client (`lib/api.ts`) handles all service calls.
41 route files spanning trading intelligence, portfolio management, and admin tooling (verify
with `find frontend/src/pages -name "*.tsx" -o -name "*.ts" | grep -v /api/` if this drifts —
this count has grown steadily every session and this doc will likely be stale again soon).

---

## Architecture

```
frontend/
├── src/
│   ├── pages/          — Next.js pages (41 route files; each = a route)
│   │   ├── research/index.tsx, research/[symbol].tsx  — nested route
│   │   └── stock/[symbol].tsx                          — dynamic route
│   ├── lib/
│   │   ├── api.ts      — All API calls (~82KB; single source of truth for endpoint shapes)
│   │   ├── auth.ts     — JWT decode + session management
│   │   ├── ai.ts       — Claude AI integration
│   │   ├── alerts.ts   — Alert management utilities
│   │   └── settings.ts — User settings
│   └── components/     — Shared UI components
└── .env.production     — GITIGNORED; must exist on EC2; contains API_GATEWAY_URL
```

**Note on `board.tsx`/`forecast.tsx`/`screener.tsx`:** CLAUDE.md's "Connectivity Audit Invariants"
section (2026-06-18) claims these were deleted as unreferenced dead code. As of 2026-07-04 all
three still exist and are actively referenced/linked from other pages (`board.tsx` in particular
has ongoing feature commits well after the claimed deletion date) — that CLAUDE.md note is stale.
`StrategyBuilder.tsx` genuinely is gone from `components/`, so only 3 of the original 4 "deleted"
files are actually still alive. Don't trust that note without re-verifying with `find`/`grep -rn`.

---

## API Client (`lib/api.ts`)

**All API calls go through `api.ts`** — never use `fetch()` directly in pages.
The `request()` function handles: auth headers, 401 logic, error normalization.

### 401 handling — critical invariant
```typescript
// Only clear JWT if locally expired — never on any 401
const raw = localStorage.getItem('stockai_jwt');
let expired = true;
if (raw) {
  try {
    const p = JSON.parse(atob(raw.split('.')[1] + '=='));
    expired = p.exp < Date.now() / 1000;
  } catch {}
}
if (expired) {
  localStorage.removeItem('stockai_jwt');
  window.location.href = '/login';
  throw new Error('TokenExpired');
}
throw new Error('Unauthorized'); // valid token, server rejected — don't log out
```

**Never add a handler that both preserves the token AND redirects to /login.** This causes
a redirect loop: login.tsx sees valid token → redirects to / → API returns 401 → /login → loop.

### Adding a new API call
```typescript
// In api.ts, add to the exported api object:
newEndpoint: (param: string) =>
  request<ResponseType>(`/endpoint/${param}`, {
    method: 'POST',
    body: JSON.stringify({ key: value })
  }),
```

---

## Auth Flow (`_app.tsx`)

Auth state is read synchronously from localStorage at first render (lazy init):
```typescript
// Read JWT from localStorage synchronously — no async gap before doCheck() runs
const [username, setUsername] = useState(() => {
  const session = getSession(); // reads + decodes JWT synchronously
  return session?.username ?? null;
});
```

**dataFreshness() poll is gated on `username`:**
```typescript
useEffect(() => {
  if (!username) return; // never poll when unauthenticated
  const id = setInterval(() => api.dataFreshness(), 60_000);
  return () => clearInterval(id);
}, [username]);
```

---

## Improvements Tracker (`pages/improvements.tsx`)

Largest page file (~1.5MB and growing every session — check `ls -la` if this matters, don't trust
a static number here). TypeScript type safety requires all four to be updated together:

```typescript
type Tier = 1 | 2 | ... | N  // add N here

const TIER_LABEL: Record<Tier, string> = {
  ...,
  N: 'Tier N — Description',  // add here
}

const TIER_COLOR: Record<Tier, string> = {
  ...,
  N: '#hexcolor',  // add here
}

// Item objects:
{ id: 'TN-SLUG', tier: N as const, severity: 'medium', defaultStatus: 'todo' as const,
  file: 'path/to/file', effort: '2h', impact: '...', title: '...', what: '...', fix: '...' }
```

**The render loop is automatic** — driven by `Object.keys(TIER_LABEL)`. No hardcoded tier array.

**Do not hardcode "current highest tier" in this doc — it goes stale within days.** This file
previously said "Current highest tier: 215. Next new tier: 216." when the actual highest tier
had already reached 232 — following that stale instruction literally would have created a
duplicate/colliding tier ID. Always check the live value instead:
```bash
grep -oE "^\s+[0-9]+:\s*'Tier" frontend/src/pages/improvements.tsx | grep -oE '[0-9]+' | sort -n | tail -1
```

### Item severity + color palette
| Severity | Meaning |
|---|---|
| `low` | Polish / minor improvement |
| `medium` | Meaningful improvement to accuracy or reliability |
| `high` | Significant impact on P&L, signal quality, or system stability |

| Category | Color |
|---|---|
| DE / paper trading gate | `#f59e0b` amber |
| ML / AI / prediction | `#8b5cf6` purple |
| Broker / execution | `#22d3ee` cyan |
| HK-specific | `#fb923c` orange |
| Minor fixes | `#64748b` slate |
| UI / frontend | `#38bdf8` sky |
| Auth / security | `#ef4444` red |
| Signal pipeline | `#10b981` emerald |

---

## Key Pages Reference

| Page | Route | What it shows |
|---|---|---|
| `index.tsx` | `/` | Main dashboard — market overview, top signals |
| `paper-portfolio.tsx` | `/paper-portfolio` | Paper trading portfolios + trade history |
| `opportunities.tsx` | `/opportunities` | AI-ranked stock opportunities |
| `signal-filters.tsx` | `/signal-filters` | Signal filter + alert subscription |
| `decide.tsx` | `/decide` | DE decision breakdown per candidate |
| `improvements.tsx` | `/improvements` | Improvement tracker roadmap |
| `admin-health.tsx` | `/admin-health` | Container health + system status |
| `regime.tsx` | `/regime` | Market regime visualization |
| `rankings.tsx` | `/rankings` | K-score leaderboard |
| `research/[symbol].tsx` | `/research/AAPL` | AI research report |
| `stock/[symbol].tsx` | `/stock/AAPL` | Stock detail: price, signals, TA, research |

---

## Build and Deploy

**Always use DOCKER_BUILDKIT=0.** BuildKit silently caches layers even with `--no-cache`.

```bash
# Requires frontend/.env.production on EC2 (gitignored — create manually)
# Contents: API_GATEWAY_URL=http://api-gateway:8000

DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest .
docker compose -f docker/docker-compose.yml up -d --force-recreate frontend
```

Run synchronously — never background. SSH timeout on a backgrounded build = unknown state.
