# tiny-pages

Public, static mini pages behind Traefik, plus the OpenRouter provider router that
feeds one of them. One folder per sub-project, no frameworks, no server-side code.

Live pages:

- <https://netzfilter.wfech.de/> — interactive mains-filter lab (X2/Y2 capacitors,
  common-mode and differential-mode interference, surge/burst pulses)
- <https://wannkannichballern.wfech.de/> — OpenRouter provider-router dashboard
  (expectation vs. measurement)
- <https://wannkannichballern.wfech.de/preis-uhr/> — DeepSeek peak/off-peak
  price clock (Europe/Berlin, Chinese public holidays off-peak)

`index.html` in the repository root is a local overview page: start a static
server here and open it to reach every page without deploying anything.

## The situation and the problem

The pages answer two questions that kept being guessed wrong:

1. **When is DeepSeek cheap?** DeepSeek bills peak and off-peak prices on a
   Beijing-time schedule (off-peak includes Chinese public holidays). Looking it
   up manually is error-prone, so the clock computes the current window, the next
   change and a 7-day grid from the official rules.
2. **Which OpenRouter provider is actually cheapest?** The same model is served
   by many providers with different input, output and cache-read prices. The
   cheapest list price is usually *not* the cheapest provider: with ~94 % cache
   reads the cache-read price dominates, and every provider switch re-bills the
   whole context uncached. Choosing by hand (or by the cheapest headline price)
   costs money and makes the bill impossible to verify.

## The solution

- A static **price clock** (`deepseek-offpeak/`) with no third-party scripts, no
  cookies, no storage and no network calls at runtime.
- A deterministic, cache-aware **provider router**
  (`openrouter-provider-router/`) that measures the real token profile from
  opencode's own database, fetches live endpoint prices (no API key needed for
  prices), projects the cost of every provider at *your* usage profile and picks
  one with hysteresis, so it does not flap on tiny price differences.
- A **read-only dashboard** that publishes a whitelisted `router.json`
  (no keys, no session ids, no paths, no account limits) so expectation and
  measurement can be compared on the live page. Nothing on the page can control
  anything.

## Repository layout

```
index.html                        local overview page (links every sub-project)
netzfilter-labor/                 site root of netzfilter.wfech.de
  index.html                      self-contained mains-interference simulator
deepseek-offpeak/                 site root of wannkannichballern.wfech.de
  index.html router.css           router dashboard
  router.js model.js charts.js explain.js trending.js watch.js
  preis-uhr/                      DeepSeek peak clock (index.html, app.js, peak.js, holidays.js)
  modelle/                        model overview page
  scripts/update_holidays.py      monthly refresh of preis-uhr/holidays.js
  test/                           node --test (peak logic, dashboard model/charts/explanations)
  router.json                     runtime data, published by the router (never committed)
deploy/                           nginx, Traefik, site/backend deploy, holiday timer
root/index.html                   fallback index for the default nginx server
openrouter-provider-router/       provider router + dashboard exporter (own README)
  scripts/models/                 decision engine, exporter, publisher, Windows runtime installer
  tests/                          pytest suite
  .opencode/plugins/              opencode plugin that applies the decision per request
```

## Quickstart (humans)

Requirements: Node.js 18+ for the pages, Python 3.11+ for the router.

```bash
# page logic tests
cd deepseek-offpeak && node --test

# router tests (offline, synthetic state)
cd ../openrouter-provider-router && python -m pytest tests/ -q
```

The deploy scripts in `deploy/` are written for one concrete environment
(an LXC behind Traefik). Read them before running; host, container id, key path
and TLS/edge setup must be adapted to your own environment.

## LLM quickstart (other opencode agents)

- This repository is a set of independent static sub-projects, one folder each.
  Do not assume a single application or a build step.
  `netzfilter-labor/index.html` is one self-contained file (inline CSS/JS, no
  dependencies).
- Entry points: `deepseek-offpeak/index.html` + `router.js` / `model.js` for the
  page; `openrouter-provider-router/scripts/models/or_provider_router.py`
  (`decide`) and `or_router_export.py` (`export`, `cycle`, `publish`) for the
  router.
- Tests: `node --test` in `deepseek-offpeak/`, `python -m pytest tests/ -q` in
  `openrouter-provider-router/`. Run them before claiming a change works.
- Runtime-only data is never committed: `deepseek-offpeak/router.json`,
  `deepseek-offpeak/models_trending.json`, the router state directory
  (`~/.local/share/opencode/or-router/`, override with `ORR_STATE_DIR`),
  `opencode.db`, `.env`, SSH keys.
- Deploying is environment-specific: `deploy/` targets a concrete LXC/Traefik
  setup. Never run a deploy blindly.
- The router is deterministic: same prices + same measured usage = same
  decision. If a change makes it non-deterministic, that is a regression.

## How this repository is maintained

This repository is a **published snapshot release**. Development happens in the
private Hive monorepo (Forgejo, source of truth); the public tree is produced
from it by an export script that copies `sub_projects/tiny-pages/`,
`scripts/models/*.py`, the opencode plugin and the tests, sanitizes host and
container identifiers, and refuses to publish on a leak hit. Every sync is one
snapshot commit on top of the existing history plus a release tag, so the public
log reads as releases and never leaks homelab details from intermediate work.

Consequences for contributors:

- Fixes and features are welcome as issues and pull requests here; accepted
  changes are ported upstream first and then appear in a later snapshot.
- The public repo is not the only copy of anything: never delete or rewrite it
  expecting the source to follow.
- Files that only exist here (`README.md`, `LICENSE`, `NOTICE`, `.gitignore`)
  are preserved by the export and never overwritten from upstream.

## What is *not* in this repository

- Router runtime data (`router.json`, `models_trending.json`) — published to the
  server by the router itself.
- The router state directory (`decision.json`, `decisions.jsonl`, `usage.jsonl`,
  `ranks.jsonl`, `keyusage.jsonl`, `prices_last.json`) — local, machine-specific.
- `opencode.db`, API keys, `.env` files, SSH keys, certificates.

## License

Apache License 2.0 — see `LICENSE`. Attribution notices: `NOTICE`.
