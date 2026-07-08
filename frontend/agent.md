# Frontend — Engineering Agent Behavior

How to behave when working on `frontend/`. The UI is what users interact with — every change
here is immediately visible to real users on lausing.com.

---

## Mindset for This Frontend

**The API client is the contract.** `api.ts` defines the shape of every API call and response.
When a backend endpoint changes (new field, renamed field, different status codes), `api.ts` must
be updated in the same commit or the frontend silently breaks. Always check `api.ts` first when
adding or changing backend endpoints.

**Type safety is load-bearing, not cosmetic.** TypeScript compilation errors mean real bugs —
don't suppress them or use `any`. The improvements.tsx `Record<Tier, string>` errors, for example,
catch missing tier registrations before they reach production.

---

## Before Touching Auth Code

Read the login redirect loop documentation in CLAUDE.md before touching `_app.tsx`,
`api.ts` `request()`, or `lib/auth.ts`. The invariants are:
1. JWT cleared only when locally expired — not on any 401
2. dataFreshness() poll gated on `username` being set
3. Auth state initialized synchronously from localStorage — no async gap

Any change that violates these creates a login loop that affects all users immediately.

---

## Working on `api.ts`

When adding a new endpoint:
1. Add the TypeScript type for the response shape
2. Add the function to the exported `api` object following the existing pattern
3. Keep the URL path consistent with what the backend route is registered as
4. Test in the browser with network devtools — confirm the right URL and method are sent

When a backend response changes shape:
1. Update the TypeScript type first
2. Update any places in pages that destructure the response
3. TypeScript will tell you if you missed any consumer

---

## Working on Pages

**`improvements.tsx` is 1.2MB** — don't open it in full, read only the section you need.
The tier structure is at the top; items are in a long flat array. Use `grep` to find the right section.

When adding UI to a page, prefer inline logic over creating new components unless the
component is used in 2+ places. Don't design an abstraction for a one-time use.

**Test in the browser after every change** — TypeScript passing doesn't mean the UI works.
Check the golden path, then the edge cases:
- Empty state (no data)
- Loading state (data fetching)
- Error state (API down)

---

## Frontend Build Checklist

Before triggering a rebuild, confirm:
- [ ] TypeScript compiles locally: `cd frontend && npx tsc --noEmit`
- [ ] The feature works in the browser (local dev if possible, or describe what to test)
- [ ] `frontend/.env.production` exists on EC2: `ssh ... cat frontend/.env.production`
- [ ] Using `DOCKER_BUILDKIT=0` (NOT `docker compose build`)
- [ ] Build is running synchronously (NOT in background)

---

## Hard Refresh After Deploy

After a frontend deploy, users may see a cached version. If they report seeing old content:
1. Ask them to hard refresh: Ctrl+Shift+R (Windows/Linux) or Cmd+Shift+R (Mac)
2. If still broken: check that the container rebuilt with the new image (`docker ps` shows new age)
3. If the image is stale: the BuildKit cache bug struck — rebuild with `DOCKER_BUILDKIT=0`

---

## Common Issues

| Symptom | Likely cause | Fix |
|---|---|---|
| New tier not showing in improvements | Tier not in TIER_LABEL or TIER_COLOR | Add to all four places |
| -99/12 shown in DE score bar | Score < 0 not handled | ScoreBar early return for negative score |
| Login redirects to /login after deploy | 401 from dataFreshness() polling before login | Gate poll on `username` |
| Stale content after rebuild | BuildKit cache | Rebuild with `DOCKER_BUILDKIT=0` |
| API call using wrong URL | api.ts path mismatch | Check proxy.py route prefix |
