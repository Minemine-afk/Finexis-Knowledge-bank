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
