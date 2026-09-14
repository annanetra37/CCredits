const K = require('./kit.js');
const { d, C, t, P, spacer, titleBlock, h1, h2, h3, bullet, numbered, mono, code, callout, table } = K;
const fs = require('fs');

const body = [];
const add = (...x) => body.push(...x);

add(...titleBlock('CCredits · Guide 1 of 3', 'Using the portal',
  'Someone drags in the Sungrow exports; the portal parses them, computes verified carbon units, and lets anyone click a credit number all the way down to the spreadsheet cell it came from. This guide covers the four screens and a demo path that works.',
  C.green));

add(callout('Before you start.',
  'There is no login. The banner across the top of every screen reads "Pilot data. Not verified." Leave it there until someone has actually verified the numbers — it is the first thing that makes the rest credible.',
  C.gold, C.washGold), spacer(200));

add(h2('The four screens', C.ink));
add(table(
  ['Screen', 'What it is for'],
  [
    [[t('Overview', { bold: true, size: 18, color: C.ink })], 'The headline numbers, the river, the calculation, the reference data and the stated assumptions'],
    [[t('Fleet', { bold: true, size: 18, color: C.ink })], 'One row per site, every column sortable — the fastest way to find a broken site'],
    [[t('Data quality', { bold: true, size: 18, color: C.ink })], 'Coverage, gaps, suspect days and overlapping uploads, shown rather than hidden'],
    [[t('Upload', { bold: true, size: 18, color: C.ink })], 'Drop files in and see exactly what the parser read out of them'],
  ], [22, 78], C.green));

// ---------------------------------------------------------------- 1. Upload
add(h1('1 · Start by uploading', C.green));
add(P('Go to Upload and drop your Sungrow exports on the drop zone, or click it to choose files. Several at once is fine, in any order — the portal works out what each one is.'));

add(h3('The three shapes it reads'));
add(table(
  ['Export', 'Shape', 'Read as'],
  [
    ['Plant list', 'One row per site: name, capacity, grid connection date, address, status', 'Fleet master. No readings.'],
    ['Plant daily yield', 'Identifiers down the left, one column per day across the top', 'Plant grain'],
    ['Device daily report', 'One row per inverter per day', 'Inverter grain, summed to a site total per day'],
  ], [22, 50, 28], C.green));
add(spacer(60));
add(P([t('.xlsx', { font: 'Consolas', size: 18, color: C.violetInk }), t(', ', { size: 20 }),
        t('.xlsm', { font: 'Consolas', size: 18, color: C.violetInk }), t(', ', { size: 20 }),
        t('.xls', { font: 'Consolas', size: 18, color: C.violetInk }), t(', ', { size: 20 }),
        t('.csv', { font: 'Consolas', size: 18, color: C.violetInk }), t(' and ', { size: 20 }),
        t('.tsv', { font: 'Consolas', size: 18, color: C.violetInk }),
        t(' all work. Title rows and blurb above the real header are skipped automatically.', { size: 20, color: C.ink2 })]));

add(h3('Read the parse result before you move on'));
add(P('This screen is the first proof that the app read your files rather than being told what they say. For each file you get:'));
add(table(
  ['Field', 'What to check'],
  [
    ['Layout / grain', 'Did it read the file the way you expected — plant or inverter?'],
    ['Sheet, header row', 'Did it find the real header, not a title row?'],
    ['Period', 'Do the dates match the export you asked for?'],
    ['Rows read', 'Does this match the number of sites, or of device-days?'],
    ['Values kept / blank', 'A high blank count means the export itself has holes'],
    ['Blank by metric', 'Which column those holes are in'],
    ['SHA-256', 'The fingerprint of the file, stored with it and shown again at the bottom of every drill-down'],
  ], [28, 72], C.green));
add(spacer(80));
add(P('Any column the parser recognised but did not use is listed as a note. Anything it could not read at all is a note too. Nothing is dropped silently.'));

add(h3('Uploading the same file twice'));
add(P('Nothing happens. The portal hashes every file, recognises those exact bytes, and tells you which upload it already is. It does not load it again.'));

add(h3('Uploading a file that covers dates you already have'));
add(P('It loads, and the overlap is flagged. The newer upload wins for the days that overlap; the older readings are kept and marked superseded, never deleted. You can see both in the drill-down, where the losing row is labelled.'));
add(callout('This is how you correct data.',
  'Upload the corrected export. Nothing is ever edited in place, and the old version stays there to be looked at.',
  C.blue, C.wash));

// ---------------------------------------------------------------- 2. Overview
add(h1('2 · Read the Overview', C.blue));

add(h3('The headline cards'));
add(P('Energy generated, eligible energy, emission reduction, VCUs issuable, indicative revenue, coverage. Click any of them to open the drill-down.'));

add(h3('The river'));
add(P('The neutral bar on the left is every kWh read from your files. It splits into the green Eligible stream and one coloured stream per exclusion reason, each directly labelled:'));
add(table(
  ['Stream', 'Colour', 'What it means'],
  [
    ['Quality suspect', [t('■ gold', { color: C.gold, bold: true, size: 18 })], 'The day broke a quality rule — zero, implausible, or two files disagreeing'],
    ['Data gap', [t('■ red', { color: C.red, bold: true, size: 18 })], 'No row existed for that site on that day'],
    ['Before grid connection', [t('■ violet', { color: C.violet, bold: true, size: 18 })], 'The site was not yet connected'],
    ['Site inactive', [t('■ blue', { color: C.blue, bold: true, size: 18 })], "The site's status is not a running one"],
  ], [28, 16, 56], C.blue));
add(spacer(70));
add(P('The green stream flows on to the VCU box. Click any band to see the days behind it.'));
add(callout('The point of the river.',
  'Nothing disappears between generation and credits. Eligible plus every exclusion stream equals generated, exactly — and the tests assert it.',
  C.green, C.washGreen));

add(h3('The calculation panel'));
add(P('The formula with this period’s real numbers substituted in, one step at a time, each labelled with the SQL view that produced it: eligible energy, emission reduction, VCUs issuable, indicative revenue. The emission factor is shown with its margin, vintage, applicability and published validity — never as a bare constant.'));

add(h3('Reference data'));
add(P('Every published margin from the standardized baseline is listed, with the one actually in use marked "applied" and the rest marked "reference". A verifier can see that the others were considered rather than missed. Anything nobody has checked against the source document is badged "unverified", and so is every number derived from it.'));

add(h3('Stated assumptions'));
add(P('Five decisions that change the headline number, on screen rather than buried. Each shows the environment variable that controls it, so a disagreement about a threshold is a configuration change rather than an argument. Guide 2 covers all of them.'));

// ---------------------------------------------------------------- 3. Drill
add(h1('3 · Drill down to the cell', C.violet));
add(P('This is the part worth demonstrating. Click a credit number and keep clicking:'));
add(...code([
  'Credits  >  March 2025  >  SANNOVA Kotayk 3  >  01 Mar 2025  >  cell BK6',
], C.washViol));
add(numbered([t('Credits to months. ', { bold: true, size: 20, color: C.ink }), t('Every month in the loaded window, with its eligible energy, emission reduction, VCUs and worst quality flag.', { size: 20, color: C.ink2 })]));
add(numbered([t('Month to sites. ', { bold: true, size: 20, color: C.ink }), t('Which sites made up that month, sorted by contribution.', { size: 20, color: C.ink2 })]));
add(numbered([t('Site to days. ', { bold: true, size: 20, color: C.ink }), t('Every day in the window — including the days with no data, which is the point. Each day shows its kWh, its specific yield, its flag and the rule that flagged it.', { size: 20, color: C.ink2 })]));
add(numbered([t('Day to cells. ', { bold: true, size: 20, color: C.ink }), t('The actual spreadsheet cells: the value, the metric, the device, the cell reference, the filename and the file’s SHA-256. Rows superseded by a later upload appear here too, marked.', { size: 20, color: C.ink2 })]));
add(spacer(60));
add(P('Every level is a click, nothing is a page load. The breadcrumb walks back up. Esc closes the drawer.'));
add(callout('A day with no cells behind it.',
  'The drawer says so plainly. That is what makes it a gap — and nothing was invented to fill it.',
  C.red, C.washRed));

// ---------------------------------------------------------------- 4. Fleet
add(h1('4 · Find a broken site', C.gold));
add(P('The Fleet screen is one row per site: capacity, grid connection date, days covered, total and eligible kWh, specific yield, quality score, VCUs.'));
add(P([t('Every column sorts. ', { size: 20, color: C.ink2 }),
        t('Sort by specific yield', { size: 20, bold: true, color: C.goldInk }),
        t(' — kWh per kWp per day. Sites cluster tightly around the seasonal norm, so anything far below the pack is shaded, faulty or offline, and anything far above is a data problem. It is the fastest diagnostic in the app.', { size: 20, color: C.ink2 })]));
add(P('Click any row to drill into that site’s days.'));

// ---------------------------------------------------------------- 5. Quality
add(h1('5 · Show the gaps on purpose', C.red));
add(P('Coverage percentage, then a bar of ok / suspect / missing across all site-days.'));
add(bullet([t('Gaps', { bold: true, size: 20, color: C.ink }), t(' — every run of missing days, by site, with its length. Never interpolated, never quietly zeroed.', { size: 20, color: C.ink2 })]));
add(bullet([t('Suspect days', { bold: true, size: 20, color: C.ink }), t(' — each with the rule that flagged it, in words.', { size: 20, color: C.ink2 })]));
add(bullet([t('Overlapping files', { bold: true, size: 20, color: C.ink }), t(' — where a later upload covered ground an earlier one already had.', { size: 20, color: C.ink2 })]));
add(spacer(60));
add(callout('Show this screen in a demo.',
  'A system that displays its own gaps is the one people believe. Hiding them buys nothing and costs the only thing that matters.',
  C.green, C.washGreen));

// ---------------------------------------------------------------- rules
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('6 · What the rules actually are', C.accent));
add(P('Applied per site per day, in this order. The first one that matches decides.'));
add(table(
  ['#', 'Rule', 'Outcome'],
  [
    ['1', 'Before grid connection', 'Excluded — the site was not yet earning'],
    ['2', 'Site inactive', 'Excluded, if the plant status is not a running one'],
    ['3', 'Missing', 'Excluded as a data gap. Never interpolated'],
    ['4', 'Suspect', 'Excluded — negative generation; specific yield above the implausibility threshold; two files disagreeing by more than 0.5%; or zero generation, if that policy is set'],
    ['5', 'Otherwise', [t('Eligible', { bold: true, size: 18, color: C.greenInk })]],
  ], [6, 26, 68], C.accent));

add(h3('From eligible energy to VCUs'));
add(P('Eligible kWh becomes megawatt-hours, multiplied by the emission factor valid for that month, with project emissions and leakage shown as explicit zeros so the formula reads complete. The resulting tonnes become whole VCUs per site per month, and the fractional remainder is carried forward to the next month rather than rounded away.'));
add(...code([
  'eligible_MWh  x  emission_factor  -  project_emissions  -  leakage   =  tCO2e',
  'floor( cumulative tCO2e )  -  already issued                         =  VCUs',
]));
add(P('A site reducing 2.16 tonnes then 3.03 tonnes issues 2 then 3, not 2 then 3 with the fractions lost.'));

add(h3('Aggregates carry the worst child quality'));
add(P('Never the average. One missing day makes the month "missing". That is deliberate: an average would let a good month hide a bad week.'));

add(h1('7 · Things worth knowing', C.ink));
add(bullet([t('Nothing is stored downstream of Bronze. ', { bold: true, size: 20, color: C.ink }), t('Silver and Gold are SQL views. A new upload, or a changed emission factor, recomputes everything on the next page load. There are no stale results to refresh.', { size: 20, color: C.ink2 })]));
add(bullet([t('Bronze is immutable. ', { bold: true, size: 20, color: C.ink }), t('No row is ever updated or deleted. Corrections arrive as new files.', { size: 20, color: C.ink2 })]));
add(bullet([t('Dates must be loadable to be visible. ', { bold: true, size: 20, color: C.ink }), t('The loaded window is the span of all uploaded files. Sites are expected to have data for every day in it, which is what makes a gap show up as a gap.', { size: 20, color: C.ink2 })]));
add(bullet([t('Upload the plant list too. ', { bold: true, size: 20, color: C.ink }), t('A device report alone gives a site no installed capacity, so specific yield and the implausibility rule cannot be computed for it.', { size: 20, color: C.ink2 })]));
add(bullet([t('No factor means no claim. ', { bold: true, size: 20, color: C.ink }), t('If no emission factor applies to the loaded period, the portal reports no reduction and no VCUs, and says why. It will not substitute a guess.', { size: 20, color: C.ink2 })]));

add(h1('8 · A demo path that works', C.green));
add(numbered([t('Upload ', { bold: true, size: 20, color: C.ink }), t('— drop all the exports at once and read the parse summary out loud. That is the moment it stops looking like a mock-up.', { size: 20, color: C.ink2 })], 1));
add(numbered([t('Overview ', { bold: true, size: 20, color: C.ink }), t('— the river. Point at a coloured stream and say why that energy does not count.', { size: 20, color: C.ink2 })], 1));
add(numbered([t('Drill ', { bold: true, size: 20, color: C.ink }), t('— click the credit number down to a cell reference and a SHA-256. That is the whole product in one gesture.', { size: 20, color: C.ink2 })], 1));
add(numbered([t('Fleet ', { bold: true, size: 20, color: C.ink }), t('— sort by specific yield and find the bad site.', { size: 20, color: C.ink2 })], 1));
add(numbered([t('Data quality ', { bold: true, size: 20, color: C.ink }), t('— show the gaps, deliberately.', { size: 20, color: C.ink2 })], 1));
add(numbered([t('Assumptions ', { bold: true, size: 20, color: C.ink }), t('— end on the open decisions. Stating them makes the numbers more credible, not less.', { size: 20, color: C.ink2 })], 1));

const opts = K.docOptions('CCredits — Using the portal', 'CCredits · Guide 1 of 3 · Using the portal', C.green);
opts.sections[0].children = body;
d.Packer.toBuffer(new d.Document(opts)).then((b) => {
  fs.writeFileSync('CCredits-1-Portal-Guide.docx', b);
  console.log('wrote CCredits-1-Portal-Guide.docx');
});
