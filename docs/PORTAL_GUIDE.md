# CCredits portal — how to use it

The portal turns Sungrow exports into credit numbers you can defend. Every
figure on screen traces back to a cell in a spreadsheet, and you can click your
way there in four steps.

There is no login. The banner across the top — **"Pilot data. Not verified."** —
is on every screen deliberately. Leave it there until someone has actually
verified the numbers.

---

## The four screens

| Screen | What it is for |
|---|---|
| **Overview** | The headline numbers, the river, the calculation, the assumptions |
| **Fleet** | One row per site, sortable — the fastest way to find a broken site |
| **Data quality** | Coverage, gaps, suspect days, overlapping uploads |
| **Upload** | Drop files in, see exactly what the parser read |

---

## 1. Start by uploading

Go to **Upload** and drop your Sungrow exports on the drop zone, or click it to
choose files. You can drop several at once, in any order.

The portal reads three shapes of export and works out which is which by itself:

- **Plant list** — the fleet master: plant name, installed capacity, grid
  connection date, address, status. No readings, just the site register.
- **Plant daily yield** — identifiers down the left, one column per day across
  the top. Read at plant grain.
- **Device daily report** — one row per inverter per day. Read at inverter
  grain, and summed to a site total per day.

`.xlsx`, `.xlsm`, `.xls`, `.csv` and `.tsv` all work. Title rows and blurb above
the real header are skipped automatically.

### Read the parse result before moving on

This is the screen that proves the app read your files rather than being told
what they say. For each file you get:

| Field | What to check |
|---|---|
| **Layout / grain** | Did it read it the way you expected — plant or inverter? |
| **Sheet, header row** | Did it find the real header, not a title row? |
| **Period** | Do the dates match the export you asked for? |
| **Rows read** | Does this match the number of sites or device-days? |
| **Values kept / blank** | A high blank count means the export has holes |
| **Blank by metric** | Which column the holes are in |
| **SHA-256** | The fingerprint of the file, stored with it |

Any column the parser recognised but did not use is listed as a note. Anything
it could not read at all is a note too — nothing is dropped silently.

### What happens if you upload the same file twice

Nothing. The portal hashes every file, notices it has seen those exact bytes
before, and tells you which upload it already is. It does not load it again.

### What happens if a new file covers dates you already loaded

It loads, and the portal flags the overlap. The newer upload wins for the days
that overlap; the older readings are kept and marked superseded, not deleted.
You can see both in the drill-down, where the losing row is labelled
`superseded`. Overlaps are listed on the **Data quality** screen.

This is how you correct data: upload the corrected export. Nothing is ever
edited in place, and the old version stays there to be looked at.

---

## 2. Read the Overview

### The headline cards

Energy generated, eligible energy, I-REC issuable, carbon reduction, indicative
revenue, coverage. Click any of them to open the drill-down.

### The river

The wide bar on the left is every kWh read from your files. It splits into the
green **Eligible** stream and one grey stream per exclusion reason:

| Grey stream | What it means |
|---|---|
| **Quality suspect** | The day broke a quality rule — zero, implausible, or two files disagreeing |
| **Data gap** | No row existed for that site on that day |
| **Before grid connection** | The site was not yet connected |
| **Site inactive** | The site's status is not a running one |

The green stream flows on to the credit box. Click any band to see the days
behind it.

The point of the river is that nothing disappears between generation and
credits. Eligible plus all the grey streams equals generated, exactly.

### The calculation panel

The formula with this period's real numbers substituted in, one step at a time,
each labelled with the SQL view that produced it. The emission factor is shown
with its vintage, type and source document next to the number it produced — not
as a bare constant.

### Stated assumptions

Five decisions that change the headline number, on screen rather than buried.
Each shows the environment variable that controls it, so if someone disagrees
with a threshold you can change it and reload rather than argue about it. See
[ENVIRONMENT.md](ENVIRONMENT.md).

---

## 3. Drill down to the cell

This is the part worth demonstrating. Click a credit number and keep clicking:

```
Credits  ›  March 2025  ›  SANNOVA Ararat 1  ›  05 Mar 2025  ›  cell D6
```

1. **Credits → months.** Every month in the loaded window, with its eligible
   energy, I-RECs, carbon and worst quality flag.
2. **Month → sites.** Which sites made up that month, sorted by contribution.
3. **Site → days.** Every day in the window — *including the days with no data*,
   which is the point. Each day shows its kWh, its specific yield, its flag and
   the rule that flagged it.
4. **Day → cells.** The actual spreadsheet cells: the value, the metric, the
   device, the **cell reference** (`D6`), the **filename**, and the file's
   **SHA-256**. Rows superseded by a later upload appear here too, marked.

Every level is a click, nothing is a page load. The breadcrumb at the top walks
back up. `Esc` closes the drawer.

If a day has no cells behind it, the drawer says so plainly — that is what makes
it a gap, and nothing was invented to fill it.

---

## 4. Find a broken site (Fleet)

The **Fleet** screen is one row per site: capacity, grid connection date, days
covered, total and eligible kWh, specific yield, quality score, I-RECs.

Every column sorts. **Sort by specific yield** — kWh per kWp per day. Sites
cluster tightly around the seasonal norm, so anything far below the pack is
shaded, faulty or offline, and anything far above is a data problem. It is the
fastest diagnostic in the app.

Click any row to drill into that site's days.

---

## 5. Show the gaps on purpose (Data quality)

Coverage percentage, then a bar of ok / suspect / missing across all site-days.

- **Gaps** — every run of missing days, by site, with its length. Never
  interpolated, never quietly zeroed.
- **Suspect days** — each with the rule that flagged it, in words.
- **Overlapping files** — where a later upload covered ground an earlier one
  already had.

Show this screen in a demo rather than hiding it. A system that displays its own
gaps is the one people believe.

---

## What the rules actually are

Applied per site per day, in this order:

1. **Before grid connection** — excluded. The site was not yet earning.
2. **Site inactive** — excluded, if the plant status is not a running one.
3. **Missing** — no row exists. Excluded as a data gap; never interpolated.
4. **Suspect** — excluded, for any of:
   - negative generation
   - specific yield above the implausibility threshold (8 kWh/kWp/day by default)
   - two files disagreeing about the same site-day by more than 0.5%
   - zero generation, if `ZERO_DAY_POLICY=suspect`
5. **Otherwise eligible.**

Eligible kWh becomes whole I-RECs per site per month, with the fractional
remainder **carried forward** to the next month rather than rounded away. A site
generating 1.5 MWh then 1.7 MWh issues 1 then 2, not 1 then 1.

Carbon is eligible MWh × the emission factor valid for that month, with project
emissions and leakage shown as explicit zeros so the formula reads complete.

Monthly and fleet aggregates carry the **worst** child quality flag, never the
average. One missing day makes the month "missing".

---

## Things worth knowing

- **Nothing is stored downstream of Bronze.** Silver and Gold are SQL views. A
  new upload, or a changed emission factor, recomputes everything on the next
  page load. There are no stale results to refresh.
- **Bronze is immutable.** No row is ever updated or deleted. Corrections arrive
  as new files.
- **Dates must be loadable to be visible.** The "loaded window" is the span of
  all uploaded files. Sites are expected to have data for every day in it, which
  is what makes a gap show up as a gap.
- **If you upload only a device report**, its site will have no installed
  capacity, so specific yield and the implausibility rule cannot be computed for
  it. Upload the plant list too.

---

## Demo path that works

1. **Upload** — drop all the exports at once, and read the parse summary out
   loud. That is the moment it stops looking like a mock-up.
2. **Overview** — the river. Point at a grey stream and say why that energy does
   not count.
3. **Drill** — click the credit number down to a cell reference and a SHA-256.
   That is the whole product in one gesture.
4. **Fleet** — sort by specific yield, find the bad site.
5. **Data quality** — show the gaps, deliberately.
6. **Assumptions** — end on the five open decisions. Stating them makes the
   numbers more credible, not less.
