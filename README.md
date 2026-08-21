# finexis advisory — panel demos

Two standalone pages over the same product panel. Neither has a build step, dependencies or a
server: open the file in any browser.

| File | What it is |
|---|---|
| `index.html` | **Policy Desk** — a searchable knowledge bank. What each product *is*. |
| `coverage-bench.html` | **Coverage Bench** — a comparison bench. What each product *does to a number*. |

`index.html` is the source of truth for the sourced product facts; `bench-data.json` holds Coverage
Bench's estimates; `build-panel.mjs` merges the two so the pages cannot drift apart.

---

# Policy Desk — insurance product knowledge bank

A working demo of a searchable knowledge bank for a **multi-tied financial consultant**: every
product on the panel in one place, findable by free-text search and by the filters an adviser
actually thinks in — client need, product type, insurer, distribution status.

Open `index.html` in any browser. No build step, no dependencies, no server.

## What's in the catalogue

72 products across the 15 insurers confirmed on finexis advisory's published product-provider
panel:

Singlife · Manulife · HSBC Life · Tokio Marine Life · China Taiping · China Life · Etiqa · FWD ·
Raffles Health Insurance · Allianz · Transamerica Life (Bermuda) · Friends Provident
International · MSIG · Sompo · HL Assurance

AIA, Prudential, Great Eastern and Income are **not** included — they could not be confirmed on
the panel from public sources. Add them the same way as anything else if they belong there.

### What is sourced, and what is estimated

This matters, because estimated figures sitting under real brand names are how a knowledge bank
misleads the person using it.

| Sourced from the insurer's own page (20 Aug 2026) | Estimated |
|---|---|
| Insurer and product name | Entry age |
| Product type and category | Sum assured / benefit |
| Distribution status and availability changes | Premium basis |
| The headline figures under *What it does well* | Coverage term |
| The `source` link on every record | Underwriting route |
| | CPF / SRS funding eligibility |

Every estimated field is named in that record's `est` array and renders with an `est.` marker in
the record panel and the comparison table. Each record's header shows how many of the six
estimable fields are estimates, the **Data confidence** filter buckets the whole catalogue by it,
and the opening panel links straight to the records with nothing sourced yet.

Examples of what *is* real: Raffles Key Rider withdrawn 1 Apr 2026 and Raffles Choice Rider
launched the same day with a 5% co-payment capped at S$6,000 a policy year; Raffles Cancer Guard
covering up to S$250,000 a year of non-CDL cancer drug treatment; Singlife Multipay Critical
Illness II paying up to 900% of the sum assured; China Taiping i-Secure Legacy (II) at up to 4x
cover over 161 conditions; China Life SaveReward 101 Series III at 2.08% p.a. (SGD) or 3.40% p.a.
(RMB); Etiqa ePROTECT mortgage charging premiums for 90% of the term.

**This is a demo, not advice, and not a substitute for a product summary.**

## What the demo shows

| | |
|---|---|
| **Search** | One box over product name, insurer, type, client needs, riders, features and notes. Every word you type has to land somewhere (AND), matches are highlighted, and name/insurer hits rank above body hits. |
| **Faceted filters** | Category, product type, client need, insurer, client segment, funding, currency, underwriting, distribution status, data confidence. Counts next to each option are live and account for every *other* filter, so a count is what you will actually get. |
| **Product record** | Key terms with estimate markers, what it does well, notes and things to confirm, riders, needs it fits, and a link to the insurer page it came from. |
| **Compare** | Pin up to three products and put them side by side on the same 15 rows. |
| **Bank at a glance** | The default right-hand panel: holdings by category, largest panels by insurer, and how much of the bank still needs a product-summary read. |
| **Shareable views** | Search text and filters are written to the URL — send a colleague `#q=cancer&insurer=FWD` and they land on the same shortlist. |
| **Keyboard** | `/` focuses search, `↑ ↓` walk the results, `Esc` closes the record. |
| **Themes** | Light and dark, following the OS by default, with a manual toggle. |

## The data model

Everything is driven by the `PRODUCTS` array in `index.html`. One object per product:

```js
{
  id:"slf-multipay-ii",
  name:"Singlife Multipay Critical Illness II", insurer:"Singlife",
  category:"Life protection",                    // top-level shelf
  type:"Multi-pay critical illness",             // product type
  needs:["Critical illness cover","Income replacement"],   // what a client came in for
  segments:["Mass affluent","Family"],
  funding:["Cash"], currency:["SGD"],
  premiumBasis:"Regular premium", coverageTerm:"To age 85",
  entryAge:"17 – 60", sumAssured:"S$50,000 – S$1m", uw:"Full underwriting",
  est:["entryAge","sumAssured","premiumBasis","coverageTerm","uw"],  // which of those are guesses
  status:"Open",                                 // Open | Superseded | Withdrawn | Confirm
  statusNote:"...",                              // optional, shown on the record
  summary:"One paragraph in the adviser's own words.",
  features:["Up to 900% of sum assured in total payouts", ...],   // sourced claims
  notes:["Waiting periods between claims are the thing clients get wrong — confirm them", ...],
  riders:[...],
  source:"https://singlife.com/en/critical-illness-insurance/multipay-critical-illness-ii",
  sourceName:"singlife.com", sourced:"2026-08-20"
}
```

The two fields that make this a *knowledge* bank rather than a product list are `notes` (the trap
you would otherwise learn from a rejected case) and `est` (what nobody has verified yet).

## Working with it

**Turn an estimate into a real term** — the core maintenance loop. Read the product summary, put
the real value in the field, delete that key from `est`. The marker disappears, the record's
"terms estimated" count drops, and the Data confidence filter moves it up a bucket.

**Add a product** — append an object to `PRODUCTS`. Nothing else to touch; the filter rail,
counts and comparison table all derive from the data.

**Add a filter** — add the field to your products and one line to `FACETS`:

```js
{ key:"channel", label:"Submission channel", get: p => p.channel }
```

A new filter group appears, with live counts, chips and URL persistence for free.

**Point it at real data** — replace the inline `PRODUCTS` array with a `fetch()` of your own
JSON, or render it server-side. The rest of the app does not care where the data came from.

## If this became the real thing

1. **A source of truth** — products in a database with an edit UI for whoever maintains the bank.
2. **Verification workflow** — a record goes stale on a schedule; someone re-checks it against
   the insurer circular and re-stamps it. `est` is the backlog that workflow burns down.
3. **Change history** — repricings and withdrawals are the events advisers get burned by, so
   every field change wants a timestamp and an author.
4. **Document storage** — product summary, benefit illustration and underwriting guide attached
   to the record rather than linked by name.
5. **Access control** — a consultant sees the panel they are authorised to sell; compliance sees
   everything and signs off changes.
6. **Client-facing output** — turn a pinned comparison into a PDF carrying the required
   disclaimers.

---

# Coverage Bench — panel comparison bench

Open `coverage-bench.html` in any browser.

Policy Desk answers *what is this product*. Coverage Bench answers *what does it do to a number* —
put four products side by side and run an event through them.

## Four families, four engines

The panel is not one kind of thing, and one engine cannot compare it. Each product carries a
`family`, and the family picks the arithmetic:

| Family | Lines | What it holds | The engine | The event |
|---|---|---|---|---|
| `hospital` | 15 | Integrated Shield Plans and their riders, Allianz IPMI, Allianz Summit | pro-ration → deductible → co-payment, inside the annual limit | a hospital bill in a chosen ward |
| `lumpsum` | 31 | term, whole life, CI, multi-pay, mortgage cover, personal accident, CareShield / ElderShield | a multiple of the cover being modelled | death · TPD · CI · accident · disability income |
| `accumulation` | 25 | endowments, lifetime income, retirement, ILPs, offshore savings | premiums in over the premium term against the illustrated value out | a horizon year |
| `general` | 7 | travel, motor, home, domestic helper | annual premium against the headline benefit limit | a claim against that limit |

**72 panel products, 78 bench lines.** Singlife Shield and Raffles Shield each split into their ward
tiers, because a bench that cannot tell Plan 1 from Standard is not a bench.

Scoring is **inside the family, never across it**. A travel policy would win every premium contest
against a whole life plan, and the answer would mean nothing. The mixed view lists by insurer; pick
a family and it ranks by fit.

## What is sourced, and what is estimated

Coverage Bench is a calculator, so an invented number under a real brand name comes back out looking
like an answer. Every unverified figure carries an `est.` marker wherever it appears, and each
record links to the insurer page it came from.

| Sourced | Estimated |
|---|---|
| Insurer, product name, category, product type | **Every premium curve, at every age** |
| Client needs, distribution status | Payout multiples per claim event |
| Entry age, premium basis, coverage term, underwriting (where not already in that record's `est`) | Illustrated maturity values |
| Ward, annual limit, co-insurance and rider co-payment caps for the Shield plans | Deductibles where the insurer's own figure could not be read |
| Regulatory illustration rates — participating 3.00% / 4.25%, ILP 4% / 8% | Benefit limits not stated on the insurer's page |
| The `source` link on every record | |

Shield structure read on 21 Aug 2026: Singlife Shield Plan 1 private / S$2m / S$3,500 deductible,
Plan 2 Class A / S$1.2m / S$2,000, Plan 3 Class B1 / S$500k / S$2,000; Raffles Shield Private
S$1.5m on panel (S$600k off panel), A S$600k, B S$300k, and the S$10,000 high-deductible option.
Riders sold from **1 April 2026** may no longer cover the deductible and their co-payment cap rose
to a minimum of **S$6,000** — `settle()` already applies the deductible before any rider cap, so the
model is right for post-April-2026 riders.

## Working with it

**Turn an estimate into a real quote** — the maintenance loop. Edit the product under *Manage the
panel*, type the figures off the insurer's premium table, save. The `est.` markers clear, the
product picks up a *yours* pill, and its source link survives, because the provenance did not change
even though the numbers did.

**Regenerate the panel** after editing `index.html` or `bench-data.json`:

```
node build-panel.mjs
```

It merges the two, validates every line (unique id, a source link, a 7-anchor premium curve, no
premium ever marked as sourced) and rewrites the `PANEL` array in `coverage-bench.html` between the
`PANEL:BEGIN` / `PANEL:END` markers. Idempotent — running it twice produces no diff. The published
page stays standalone; the script is a regeneration tool, not a runtime dependency.

**Add a product** — add the record to `PRODUCTS` in `index.html`, add its bench fields to
`bench-data.json`, re-run the script. The build fails loudly if either half is missing.

**Classification guard.** A client need that only one family can answer must not pick up members
from another — that is how a savings plan ends up filed under a protection need, or a travel policy
under hospital bills. `build-panel.mjs` carries a `NEED_FAMILY` map and warns on every stray:

```
  ! "MSIG MaidPlus" is general, but carries the accumulation-only need "Retirement income"
```

It warns rather than throws, because some needs legitimately span families — *Legacy planning*
covers both whole life and offshore savings. Add a need to the map only when one family owns it.

**This is a demo, not advice, and not a substitute for a benefit illustration.**
