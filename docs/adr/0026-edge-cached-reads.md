# ADR 0026: Answer reviewed reads from the edge, and keep callers out of them

- Status: accepted
- Date: 2026-09-23
- Deciders: project maintainer

## Context

The serving function and the registry compute both scale to zero. A measurement
on 2026-09-22 recorded a steady-state registry read of about 0.2 s and a first
request after an idle period of 22.7 s, while the function and the database
resumed. Responses carried `max-age` but no `s-maxage`, so the shared cache
stored nothing reliably: every visitor invoked the function, and whoever arrived
first after a quiet period waited.

Keeping the database warm would spend the free compute allowance on nobody's
behalf.

## Decision

- Snapshot reads carry `s-maxage=86400` with a week of
  `stale-while-revalidate`. The bundled snapshot cannot change within a
  deployment, and a new deployment starts with an empty edge cache, so a stored
  answer can never outlive the snapshot it came from.
- Registry reads carry `s-maxage=60`, because a forward run changes them.
- Health, freshness, forward status, and the documentation pages are never
  stored by a shared cache: their answer is about the request, not the data.
- Content-hashed assets are `immutable` for a year; `index.html` keeps
  revalidating, because it names the current asset files.
- **A response a shared cache may store carries nothing about one caller.** The
  request identifier is set only on responses marked `no-store`, `no-cache`, or
  `private`.

## Alternatives considered

- **Keeping the database warm.** Spends the free compute allowance continuously
  to prevent a cold start that most visitors never trigger.
- **Caching operational reads too.** A cached health answer reports an earlier
  moment as the present, which is worse than a slow one.
- **Keeping the request identifier on every response.** A stored copy is served
  to everyone, so the identifier would belong to whoever filled the cache; a
  reader quoting it would send an operator to an unrelated invocation.

## Consequences

A cold start is paid by the first visitor after a deployment rather than by
every visitor, without spending compute to prevent it. Cached reads carry no
request identifier, so correlation is available exactly where an answer is about
the request. Every new route now chooses a cache class deliberately: a cacheable
route may not return per-caller state.

The first version of this decision omitted the identifier rule and broke the
deployment smoke check, which traced `/api/docs` and received the identifier of
whoever had filled the cache. That is why the rule is asserted on both sides.

## Verification

`tests/unit/test_cache_policy.py` asserts the directives for each route class,
that the documentation pages are private, that an uncacheable response echoes
the caller's identifier, and that a cacheable one carries none.
`scripts/smoke_deployment.py` asserts the same rule against the running
deployment, and a test pins its definition of shared cacheability to the API's.
Production was verified after release: a repeated snapshot read reports
`x-vercel-cache: HIT`, and the smoke check passes.
