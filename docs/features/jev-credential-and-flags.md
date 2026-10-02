# M22 — where the OpenRouter key goes, and why there is no box for it

Built 2026-10-02. No Jev client exists yet; this is the credential plumbing and the two
independent controls around it.

## There is no input field, deliberately

The Jev design says: *"Use a worker-only `OPENROUTER_API_KEY` secret. Do not put keys in browser
storage, public feature flags, task payloads or logs."*

A key typed into the admin page would travel through the **browser**, the **API gateway**, and
every **request log** on the way. So the credential is a server-side file and the UI gets a
**read-only status indicator** instead of a field.

The admin page at `/admin-ai-features` now shows:

> **OpenRouter credential:** configured / not configured / unknown — could not check

An unreachable probe shows **unknown**, never "not configured" — the latter would send someone
to re-enter a key that is already there.

## Where it actually goes

```
/home/ec2-user/Stock_Trading_App/.env.jev      # chmod 600
OPENROUTER_API_KEY=sk-or-...
```

Then recreate **one** service: `docker compose -f docker/docker-compose.yml up -d
--force-recreate news-intelligence`.

### Why not the shared `.env`

Every Python service loads `env_file: ../.env`, so putting it there hands a text-classification
credential to the trading engine, the gateway and the ranking service — none of which can use
it. `.env.jev` is loaded by **news-intelligence alone**, with `required: false` so the stack
still starts when it is absent. **Absent is the normal state.**

Verified in production: the variable is present only in `news-intelligence`, and absent in
market-data, api-gateway and signal-engine.

## Two independent controls

| Control | Where | Default | What it does |
|---|---|---|---|
| `OPENROUTER_API_KEY` | `.env.jev`, server-side | unset | Makes a provider request **possible** |
| `jev_enabled` | `/admin-ai-features` toggle | **off** | Decides whether one is ever **made** |

A configured key with the flag off makes **zero** provider calls. Absence of the flag reads as
off, so an unreadable Redis cannot enable anything.

## The status endpoint returns a boolean and nothing else

`GET /news/jev/credential-status` → `{"configured": bool, "source": ..., "note": ...}`.

No prefix, no length, no masked form. Each of those leaks something about a secret, and none is
needed to answer the only question an operator has: *is it set?*

## A failure worth recording

The first attempt at this **deployed broken, silently**. `.gitignore`'s `.env.*` rule matches
`.env.jev.example` too, and the existing negations only covered the two older examples — so
`git add -A` skipped it without a word, the commit reported success, and the deploy then failed
on `cp: cannot stat '.env.jev.example'`. The file existed only on the author's machine.

"Present on disk" and "committed" are different facts, and only the second one deploys. There is
now a negation and a test asserting the example is not ignored.

## Verification

9 tests: the key reaches one service and not twelve; the stack starts without the file; the key
is absent from the shared examples; the example is committed and carries no value; a configured
key does not by itself enable Jev; the status endpoint exposes only a boolean; no input field is
bound to the credential; and an unreachable probe shows unknown.

Two of those tests were initially wrong in my own favour — one scanned raw source for the word
"mask" and matched its own docstring explaining why masking is avoided; the other asserted no
`<input>` within 2,000 characters and caught an unrelated control. Both now check the real
contract.
