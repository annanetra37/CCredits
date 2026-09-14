const K = require('./kit.js');
const { d, C, W, t, P, spacer, titleBlock, h1, h2, h3, bullet, numbered, mono, code, callout, table } = K;
const fs = require('fs');

const body = [];
const add = (...x) => body.push(...x);
const O = (name) => [t(name, { font: 'Consolas', size: 17, color: C.violetInk, bold: true })];
const sm = (text) => [t(text, { size: 17, color: C.ink2 })];

// A layer band: a wide coloured strip naming one layer and what it holds.
const layerBand = (label, subtitle, detail, fill, ink) => new d.Table({
  width: { size: W, type: d.WidthType.DXA },
  columnWidths: [2100, W - 2100],
  rows: [new d.TableRow({ children: [
    new d.TableCell({
      width: { size: 2100, type: d.WidthType.DXA },
      shading: { type: d.ShadingType.CLEAR, fill: ink },
      margins: { top: 150, bottom: 150, left: 160, right: 120 },
      borders: { top: { style: d.BorderStyle.NIL }, bottom: { style: d.BorderStyle.NIL },
                 left: { style: d.BorderStyle.NIL }, right: { style: d.BorderStyle.NIL } },
      children: [
        new d.Paragraph({ children: [t(label, { size: 24, bold: true, color: 'FFFFFF' })], spacing: { after: 30 } }),
        new d.Paragraph({ children: [t(subtitle, { size: 15, color: 'FFFFFF' })], spacing: { after: 0 } }),
      ],
    }),
    new d.TableCell({
      width: { size: W - 2100, type: d.WidthType.DXA },
      shading: { type: d.ShadingType.CLEAR, fill },
      margins: { top: 150, bottom: 150, left: 180, right: 160 },
      borders: { top: { style: d.BorderStyle.NIL }, bottom: { style: d.BorderStyle.NIL },
                 left: { style: d.BorderStyle.NIL }, right: { style: d.BorderStyle.NIL } },
      children: detail.map((line, i) => new d.Paragraph({
        children: Array.isArray(line) ? line : [t(line, { size: 19, color: C.ink2 })],
        spacing: { after: i === detail.length - 1 ? 0 : 60, line: 260 },
      })),
    }),
  ] })],
});

const arrow = (label) => new d.Paragraph({
  children: [t('▼   ' + label, { size: 17, color: C.ink3, bold: true })],
  spacing: { before: 70, after: 70 },
  indent: { left: 700 },
});

add(...titleBlock('CCredits · Guide 3 of 3', 'The data model',
  'Three schemas in one Postgres database, named so that anyone opening it understands the product before it is explained. Bronze holds what the files said. Silver holds the rules. Gold holds the numbers. This guide covers every table and view, and how each one is built from the one below it.',
  C.violet));

add(callout('One sentence version.',
  'Bronze is the only thing stored. Silver and Gold are SQL views, so a new upload or a changed threshold recomputes everything on the next query — there is nothing to refresh and nothing to go stale.',
  C.violet, C.washViol), spacer(240));

// ------------------------------------------------------------- the stack
add(h1('1 · The stack at a glance', C.violet));
add(layerBand('BRONZE', 'stored · immutable', [
  [t('source_file · reading · site · file_overlap', { font: 'Consolas', size: 18, bold: true, color: C.ink })],
  'One row per value read out of a spreadsheet cell, with the file, the row and the column it came from. Nothing is ever updated or deleted here.',
], 'F2EFE6', 'A07A2E'));
add(arrow('views, recomputed on every query'));
add(layerBand('SILVER', 'views · the rules', [
  [t('generation_daily · site_day_expected · data_quality · quality_flag', { font: 'Consolas', size: 16, bold: true, color: C.ink })],
  [t('eligibility · site_month · fleet_month', { font: 'Consolas', size: 16, bold: true, color: C.ink })],
  'Every rule is one readable CASE expression. This is the layer shown to a verifier.',
], 'EFF1F3', '6B7A8D'));
add(arrow('views, recomputed on every query'));
add(layerBand('GOLD', 'views · the numbers', [
  [t('factor_by_month · carbon · vcu · price_by_month · revenue', { font: 'Consolas', size: 16, bold: true, color: C.ink })],
  [t('fleet_summary · fleet_table · lineage', { font: 'Consolas', size: 16, bold: true, color: C.ink })],
  'Emission reductions, whole VCUs with carry-forward, indicative revenue — and the lineage that walks any of them back to a cell.',
], C.washGreen, C.greenInk));

add(spacer(200));
add(P('The schema names are the demo. Someone looking at the database should see bronze, silver and gold and understand the product before anyone explains it.'));

// ----------------------------------------------------------------- bronze
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('2 · Bronze — what the files said', 'A07A2E'));
add(P('Four tables. These are the only stored data in the system.'));

add(h3('bronze.source_file — one row per uploaded file'));
add(table(
  ['Column', 'Holds'],
  [
    [O('file_id'), 'Surrogate key, referenced by every reading'],
    [O('filename, bytes'), 'As uploaded'],
    [O('sha256'), 'Unique. This is what makes a repeat upload detectable'],
    [O('grain'), 'plant · inverter · unknown'],
    [O('sheet_name, header_row'), 'Where in the workbook the parser found the data'],
    [O('period_start, period_end'), 'The dates the file actually covers'],
    [O('row_count, values_kept, values_blank'), 'The parse summary shown on the upload screen'],
    [O('storage_path'), 'Where the original bytes were written'],
    [O('parse_notes'), 'Anything recognised but unused, or unreadable — as JSON'],
  ], [36, 64], 'A07A2E'));

add(h3('bronze.reading — long format, one row per cell'));
add(P('Exactly what the parser emits. This shape is what makes lineage possible: every value knows the file, the row and the column it came from.'));
add(table(
  ['Column', 'Holds'],
  [
    [O('reading_id'), 'Surrogate key'],
    [O('file_id'), 'Which upload this came from'],
    [O('source_row, source_col'), 'The cell, as a human counts them — row 3, column BK'],
    [O('plant_name, device_sn'), 'Who generated it'],
    [O('reading_date'), 'The day it refers to'],
    [O('metric, metric_label'), 'Canonical key, plus the raw header text as written in the file'],
    [O('value, unit'), 'The number and its unit'],
  ], [36, 64], 'A07A2E'));
add(callout('Immutable, deliberately.',
  'Nothing ever updates a row here. Re-uploading a period inserts a new file and new readings; which one wins is decided in Silver, and the loser stays visible as superseded.',
  'A07A2E', 'F7F2E6'));

add(h3('bronze.site — the fleet master'));
add(P('Derived from the plant information columns and upserted on every load. A load never overwrites a known value with a blank one, so a device report that omits capacity cannot wipe the capacity a plant list supplied.'));
add(P([t('plant_name (key) · installed_kwp · plant_type · grid_connection_date · address · plant_status · first_seen_file_id · last_seen_file_id', { font: 'Consolas', size: 16, color: C.violetInk })]));

add(h3('bronze.file_overlap'));
add(P('Records that a new upload covered dates an earlier one already had. The overlap is recorded rather than resolved by deletion; Silver decides which reading wins.'));

// ----------------------------------------------------------------- silver
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('3 · Silver — the rules', '6B7A8D'));
add(P('Views only. Every rule lives in a readable SQL statement, because this is the layer that gets shown to a verifier. They build on each other in the order below.'));

add(h3('3.1 · silver.file_site_day'));
add(P('Each file’s own account of a site-day. Inverter-grain files are summed across devices; plant-grain files pass the plant row straight through.'));
add(...code(['bronze.reading + bronze.source_file   →   one row per (file, site, day)']));

add(h3('3.2 · silver.generation_daily'));
add(P('One row per site per day. Where two files cover the same site-day, the most recently uploaded one wins; the loser is kept and exposed as silver.superseded_site_day. This is how supersession works without ever deleting anything.'));
add(...code(['DISTINCT ON (plant_name, reading_date)  ORDER BY uploaded_at DESC']));

add(h3('3.3 · silver.site_day_expected'));
add(P('A row per site per day across the whole loaded window, whether or not data exists. Without this, gaps are invisible — a missing day simply would not appear. This is the view that makes absence visible.'));
add(...code(['bronze.site  CROSS JOIN  generate_series(window_start, window_end)']));

add(h3('3.4 · silver.data_quality'));
add(P('One boolean per rule, each its own CASE expression that can be read aloud.'));
add(table(
  ['Flag', 'True when'],
  [
    [O('is_missing'), 'No row exists for that site on that day'],
    [O('is_zero'), 'Generation is exactly zero'],
    [O('is_negative'), 'Generation is below zero'],
    [O('is_implausible'), 'Specific yield exceeds the configured threshold'],
    [O('is_before_grid_connection'), 'The day precedes the site’s grid connection date'],
    [O('is_duplicate'), 'Two files disagree about this site-day by more than 0.5%'],
    [O('is_site_inactive'), 'The plant status is not a running one'],
  ], [38, 62], '6B7A8D'));
add(callout('Why is_duplicate means disagreement, not presence.',
  'Two files covering one site-day is normal — the later one wins and the earlier is kept. It is only a quality problem when the two sources disagree about the number. An earlier version flagged every day of a site that appeared in two exports, excluding a whole quarter for no reason.',
  C.blue, C.wash));

add(h3('3.5 · silver.quality_flag'));
add(P('Collapses those booleans into one flag per site-day — ok, suspect or missing — plus the reason in words. A missing day stays missing: it is never interpolated and never quietly becomes a zero.'));

add(h3('3.6 · silver.eligibility — the bridge'));
add(P('The view that connects energy to credits. Per site per day, generation is split into eligible and excluded, and every excluded kWh carries a reason.'));
add(...code([
  'generation_kwh   =   eligible_kwh   +   excluded_kwh',
  '',
  'exclusion_reason ∈ { before_grid_connection, site_inactive,',
  '                     data_gap, quality_suspect }',
], C.washGreen));
add(P('Rules are applied in that order and the first match decides. This is what the river on the Overview screen draws, and the tests assert that the two sides balance exactly.'));

add(h3('3.7 · silver.site_month and silver.fleet_month'));
add(P('Monthly aggregates that carry the worst child quality, never the average. One missing day makes the month "missing" — an average would let a good month hide a bad week.'));

// ------------------------------------------------------------------- gold
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('4 · Gold — the numbers', C.greenInk));

add(h3('4.1 · gold.factor_by_month'));
add(P('Resolves which published emission factor applies to each month. Only the row marked active can drive a number; the other published margins are recorded as reference. If the month falls outside the factor’s published validity, this view returns none — and no reduction is claimed.'));

add(h3('4.2 · gold.carbon'));
add(P('Eligible MWh times the factor, joined on date so the factor’s version travels with the result instead of being baked into it. Project emissions and leakage are explicit zero columns so the formula reads complete.'));
add(...code([
  'eligible_MWh  ×  emission_factor  −  project_emissions  −  leakage  =  tCO2e',
], C.washGreen));

add(h3('4.3 · gold.vcu'));
add(P('A verified carbon unit is one whole tonne, so a month issues whole tonnes and the fraction is carried forward rather than rounded away. The ledger is the running total: each month issues the difference between this month’s floor and last month’s.'));
add(table(
  ['Column', 'Holds'],
  [
    [O('net_reduction_tco2e'), 'This month’s reduction'],
    [O('cumulative_tco2e'), 'Running total for the site'],
    [O('carry_in_tco2e'), 'The fraction brought forward from last month'],
    [O('vcu_issued'), 'Whole units issuable this month'],
    [O('carry_forward_tco2e'), 'The fraction carried on to the next'],
  ], [36, 64], C.greenInk));

add(h3('4.4 · gold.price_by_month and gold.revenue'));
add(P('The VCU price in force at each month’s end, and units times price. With no price on file, no revenue figure is produced at all.'));

add(h3('4.5 · gold.fleet_summary and gold.fleet_table'));
add(P('The headline row on the Overview screen, and one row per site for the Fleet screen — coverage, specific yield, quality score, VCUs.'));

add(h3('4.6 · gold.lineage — the one that matters'));
add(P('Takes any gold figure and returns the chain back to the evidence. It was built at the same time as the calculations, not afterwards.'));
add(...code([
  'gold.vcu  →  silver.eligibility  →  bronze.reading  →  bronze.source_file',
  '',
  'month     →  site-days           →  cells           →  filename + sha256',
], C.washViol));

// -------------------------------------------------------------- recompute
add(h1('5 · Why it is views all the way up', C.accent));
add(bullet([t('A new upload changes every number automatically. ', { bold: true, size: 20, color: C.ink }), t('There is no pipeline to re-run and no job to schedule.', { size: 20, color: C.ink2 })]));
add(bullet([t('A changed threshold changes every number automatically. ', { bold: true, size: 20, color: C.ink }), t('Thresholds live in silver.parameter, which the views read, so the number on screen is the number the SQL used.', { size: 20, color: C.ink2 })]));
add(bullet([t('A new emission factor applies from its own date. ', { bold: true, size: 20, color: C.ink }), t('Historical months keep the factor that was valid then, because the join is on date.', { size: 20, color: C.ink2 })]));
add(bullet([t('Nothing can go stale, ', { bold: true, size: 20, color: C.ink }), t('because nothing downstream of Bronze is stored.', { size: 20, color: C.ink2 })]));
add(spacer(120));
add(callout('The cost of that choice, and how it was paid.',
  'Recomputing on every read is only viable if the views are cheap. They were not at first: a correlated subquery in data_quality re-aggregated all of Bronze once per site-day, and the summary endpoint took eight seconds on 676 readings. Resolving that per-day work once, and resolving the factor and price once per month rather than per row, brought it to under half a second. Any new view should be measured the same way.',
  C.gold, C.washGold));

add(h1('6 · Dependency map', C.ink));
add(P('Read upwards: each view is built from the ones above it.'));
add(table(
  ['View', 'Built from', 'Layer'],
  [
    [O('file_site_day'), sm('bronze.reading + source_file'), sm('Silver')],
    [O('generation_daily'), sm('file_site_day'), sm('Silver')],
    [O('site_day_expected'), sm('bronze.site + loaded_window'), sm('Silver')],
    [O('data_quality'), sm('site_day_expected + generation_daily + site_day_sources + rules'), sm('Silver')],
    [O('quality_flag'), sm('data_quality + rules'), sm('Silver')],
    [O('eligibility'), sm('quality_flag'), sm('Silver')],
    [O('site_month / fleet_month'), sm('eligibility'), sm('Silver')],
    [O('factor_by_month'), sm('site_month + gold.emission_factor'), sm('Gold')],
    [O('carbon'), sm('site_month + factor_by_month'), sm('Gold')],
    [O('vcu'), sm('carbon'), sm('Gold')],
    [O('revenue'), sm('vcu + price_by_month'), sm('Gold')],
    [O('fleet_summary / fleet_table'), sm('site_month + vcu + revenue'), sm('Gold')],
    [O('lineage'), sm('vcu + eligibility + bronze.reading + source_file'), sm('Gold')],
  ], [28, 56, 16], C.ink3));

add(h1('7 · Where to read the source', C.violet));
add(table(
  ['File', 'Contains'],
  [
    [O('app/sql/000_reset_views.sql'), 'Drops the view graph so a migration can change a column’s shape'],
    [O('app/sql/001_bronze.sql'), 'The four Bronze tables and silver.parameter'],
    [O('app/sql/002_silver.sql'), 'Every rule, as SQL'],
    [O('app/sql/003_gold.sql'), 'Reference tables, the credit maths, and lineage'],
    [O('app/ingest/parser.py'), 'The one parser, imported by both the upload endpoint and the CLI'],
    [O('app/ingest/loader.py'), 'Duplicate detection, overlap flagging, site upsert'],
    [O('tests/test_pipeline.py'), 'The numbers, asserted against a real database'],
  ], [36, 64], C.violet));
add(spacer(80));
add(P('The SQL files are the specification. They are written to be read aloud, and they are the same statements that produced every number on the screen.'));

const opts = K.docOptions('CCredits — The data model', 'CCredits · Guide 3 of 3 · The data model', C.violet);
opts.sections[0].children = body;
d.Packer.toBuffer(new d.Document(opts)).then((b) => {
  fs.writeFileSync('CCredits-3-Data-Model.docx', b);
  console.log('wrote CCredits-3-Data-Model.docx');
});
