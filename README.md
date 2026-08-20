# Policy Desk — insurance product knowledge bank

A working demo of a searchable knowledge bank for a **multi-tied financial consultant**: every
product on the panel in one place, findable by free-text search and by the filters an adviser
actually thinks in — client need, funding source, underwriting route, distribution status.

Open `index.html` in any browser. No build step, no dependencies, no server.

> The insurers and products in this demo are **fictitious** and every figure is a placeholder.
> The point of the demo is the data model and the search/filter behaviour, not the terms.

## What the demo shows

| | |
|---|---|
| **Search** | One box over product name, insurer, code, product type, client needs, riders, features and watch-outs. Every word you type has to land somewhere (AND), matches are highlighted, and name/insurer hits rank above body hits. |
| **Faceted filters** | Category, product type, client need, insurer, client segment, funding source (Cash / CPF OA / CPF MediSave / SRS), currency, underwriting route, distribution status. Counts next to each option are live and account for every *other* filter, so a count is what you will actually get. |
| **Active filters** | Shown as removable chips above the results, with a single "Clear all". |
| **Product record** | Key terms, what the plan does well, watch-outs, riders, needs it fits, document links, and a last-verified date with a reviewer name. |
| **Compare** | Pin up to three products and put them side by side on the same 15 rows. |
| **Bank at a glance** | The default right-hand panel: what the bank holds by category, what was verified most recently, and which records have gone stale. |
| **Shareable views** | Search text and filters are written to the URL — send a colleague `#q=SRS&funding=SRS` and they land on the same shortlist. |
| **Keyboard** | `/` focuses search, `↑ ↓` walk the results, `Esc` closes the record. |
| **Themes** | Light and dark, following the OS by default, with a manual toggle. |

## The data model

Everything is driven by the `PRODUCTS` array in `index.html`. One object per product:

```js
{
  id:"mer-wl-01", code:"MER-WL-310",
  name:"Meridian Heritage Whole Life", insurer:"Meridian Life",
  category:"Life protection",                    // top-level shelf
  type:"Whole life (participating)",             // product type
  needs:["Critical illness cover","Legacy planning"],   // what a client came in for
  segments:["Mass affluent","Family"],
  funding:["Cash","SRS"],                        // Cash / CPF OA / CPF MediSave / SRS / Company-paid
  currency:["SGD"],
  premiumTerm:["10 years","To age 70"], coverageTerm:"To age 100",
  entryAge:"0 – 65", sumAssured:"S$50,000 – S$5m", minPremium:"S$1,800 / yr",
  uw:"Full underwriting",                        // Full / Simplified / Guaranteed issue
  status:"Open",                                 // Open | Repricing | Closed
  statusNote:"Repricing effective 1 Sep 2026.",  // optional, shown on the record
  summary:"One paragraph in the adviser's own words.",
  features:["..."],                              // what it does well
  watch:["..."],                                 // the things that lose a case if missed
  riders:["..."], docs:["Product summary"], verified:"2026-07-28"
}
```

The two fields that make this a *knowledge* bank rather than a product list are `watch` and
`verified`: the trap you would otherwise only learn from a rejected case, and the date someone
last checked the record against the insurer's circular.

## Extending it

**Add a product** — append an object to `PRODUCTS`. Nothing else to touch; the filter rail,
counts and comparison table all derive from the data.

**Add a filter** — add the field to your products and one line to `FACETS`:

```js
{ key:"channel", label:"Submission channel", get: p => p.channel }
```

A new filter group appears, with live counts, chips and URL persistence for free.

**Point it at real data** — replace the inline `PRODUCTS` array with a `fetch()` of your own
JSON, or render the array server-side. The rest of the app does not care where the data
came from.

## If this became the real thing

The demo is deliberately one file. The things a production version needs, roughly in order:

1. **A source of truth** — products in a database, not a file, with an edit UI for whoever
   maintains the bank.
2. **Verification workflow** — a record goes stale on a schedule; someone is asked to re-check
   it against the insurer circular and re-stamp `verified`.
3. **Change history** — repricings and withdrawals are the events advisers get burned by, so
   every field change wants a timestamp and an author.
4. **Document storage** — the product summary, benefit illustration and underwriting guide
   attached to the record instead of linked by name.
5. **Access control** — a consultant sees the panel they are authorised to sell; compliance
   sees everything and signs off changes.
6. **Client-facing output** — turn a pinned comparison into a PDF that carries the required
   disclaimers.
