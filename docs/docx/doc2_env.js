const K = require('./kit.js');
const { d, C, t, P, spacer, titleBlock, h1, h2, h3, bullet, numbered, mono, code, callout, table } = K;
const fs = require('fs');

const body = [];
const add = (...x) => body.push(...x);
const V = (name) => [t(name, { font: 'Consolas', size: 17, color: C.violetInk, bold: true })];
const def = (text) => [t(text, { font: 'Consolas', size: 16, color: C.ink3 })];

add(...titleBlock('CCredits · Guide 2 of 3', 'Configuring the environment',
  'Every tunable in CCredits is an environment variable, read once at startup by app/config.py. The app starts with none of them set. This guide says what each one does, where to put it, and which four actually change the headline number.',
  C.blue));

add(callout('Where these go.',
  'Locally: copy .env.example to .env and edit it. On Azure: Container App environment variables, with anything sensitive stored as a Container App secret and referenced with secretref. Changing one restarts the revision.',
  C.blue, C.wash), spacer(200));

// ------------------------------------------------------- where to find which
add(h1('1 · Where to find which variable', C.blue));
add(table(
  ['If you need to change…', 'Look in section', 'Variables'],
  [
    ['Which database it talks to', '2 · Core', V('DATABASE_URL')],
    ['Which port it listens on', '2 · Core', V('PORT, WEBSITES_PORT')],
    ['Where uploaded files are kept', '3 · Storage', V('STORAGE_BACKEND, AZURE_*')],
    ['Who is allowed to upload', '4 · Access', V('ADMIN_TOKEN')],
    ['What counts as a bad day', '5 · Assumptions', V('IMPLAUSIBLE_KWH_PER_KWP')],
    ['How zero and missing days are treated', '5 · Assumptions', V('ZERO_DAY_POLICY, MISSING_DAY_POLICY')],
    ['The emission factor', '6 · Emission factor', V('DEFAULT_EMISSION_FACTOR, ALLOW_EXPIRED_…')],
    ['The VCU price', '7 · Price', V('DEFAULT_VCU_PRICE_PER_TCO2E')],
    ['The banner or fleet name', '8 · Presentation', V('BANNER_TEXT, FLEET_NAME')],
  ], [34, 22, 44], C.blue));

add(h3('The authoritative list'));
add(P([t('Every variable is defined in one file — ', { size: 20, color: C.ink2 }),
        t('app/config.py', { font: 'Consolas', size: 18, color: C.violetInk }),
        t(' — with its default beside it, and mirrored with comments in ', { size: 20, color: C.ink2 }),
        t('.env.example', { font: 'Consolas', size: 18, color: C.violetInk }),
        t('. If this guide and that file ever disagree, the file is right.', { size: 20, color: C.ink2 })]));

// ------------------------------------------------------------------ 2. Core
add(h1('2 · Core', C.accent));
add(table(
  ['Variable', 'Default', 'What it does'],
  [
    [V('DATABASE_URL'), def('localhost/ccredits'), 'Postgres connection. The one you must get right.'],
    [V('PORT'), def('8000'), 'Port to bind. Container Apps routes to the target port instead; leave unset.'],
    [V('APP_ENV'), def('local'), 'Free-text label, echoed by /api/health.'],
    [V('LOG_LEVEL'), def('info'), 'debug · info · warning · error'],
    [V('AUTO_MIGRATE'), def('true'), 'Apply the schema on startup. The schema is idempotent.'],
  ], [30, 20, 50], C.accent));

add(h3('DATABASE_URL'));
add(P('Azure Database for PostgreSQL refuses plaintext connections, so the URL needs TLS:'));
add(...code([
  'postgresql://USER:PASS@HOST.postgres.database.azure.com:5432/ccredits?sslmode=require',
]));
add(P([t('Store it as a Container App secret rather than a plain variable. ', { size: 20, color: C.ink2 }),
        t('postgres://', { font: 'Consolas', size: 18, color: C.violetInk }),
        t(' URLs are rewritten to ', { size: 20, color: C.ink2 }),
        t('postgresql://', { font: 'Consolas', size: 18, color: C.violetInk }),
        t(' automatically, so either prefix works.', { size: 20, color: C.ink2 })]));

add(h3('PORT and WEBSITES_PORT'));
add(P('The Dockerfile listens on 8000 and honours PORT if the host injects one. Azure Container Apps does not inject it — set --target-port 8000 instead. Azure App Service needs WEBSITES_PORT=8000 so the platform knows where to route.'));

add(h3('AUTO_MIGRATE'));
add(P('Leaving this on is right for a pilot: the schema creates tables if absent and rebuilds the view graph on every start. Turn it off to make schema changes a deliberate step, then run the migration by hand:'));
add(...code(['python cli/load_bronze.py --migrate']));
add(callout('If the database is unreachable at startup,',
  'the app still starts and /api/health reports "degraded" with the reason, rather than crash-looping. That is how you tell a bad connection string from a bad deployment.',
  C.gold, C.washGold));

// --------------------------------------------------------------- 3. Storage
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('3 · Storage', C.violet));
add(P('The original uploaded bytes are kept unmodified alongside their SHA-256. That stored file is the evidence the lineage view ultimately points at, so it has to outlive the container that received it.'));
add(table(
  ['Variable', 'Default', 'What it does'],
  [
    [V('STORAGE_BACKEND'), def('local'), 'local · azure · s3'],
    [V('STORAGE_DIR'), def('./var/uploads'), 'Where originals go when the backend is local'],
    [V('UPLOAD_MAX_MB'), def('50'), 'Per-file upload limit; larger files are refused with a message'],
    [V('AZURE_STORAGE_ACCOUNT'), def('(empty)'), 'Storage account name. Enough on its own with a managed identity'],
    [V('AZURE_STORAGE_CONTAINER'), def('uploads'), 'Blob container name'],
    [V('AZURE_STORAGE_CONNECTION_STRING'), def('(empty)'), 'Alternative to managed identity. Store as a secret'],
    [V('S3_BUCKET, S3_REGION, …'), def('(empty)'), 'Only read when the backend is s3'],
  ], [34, 18, 48], C.violet));

add(h3('The arrangement worth using on Azure'));
add(P('Set only the account name and let the container app’s own identity authenticate. No storage key then exists in configuration at all:'));
add(...code([
  'STORAGE_BACKEND=azure',
  'AZURE_STORAGE_ACCOUNT=ccreditssa',
  'AZURE_STORAGE_CONTAINER=uploads',
], C.washViol));
add(P('Then grant that identity the Storage Blob Data Contributor role on the account — the deployment guide has the exact command. The app falls back to DefaultAzureCredential, which picks the identity up automatically.'));
add(callout('A container filesystem is ephemeral.',
  'STORAGE_BACKEND=local on a deployed container means the uploaded originals are lost on every redeploy. The parsed readings survive in the database, but the files the lineage points at do not.',
  C.red, C.washRed));

// ---------------------------------------------------------------- 4. Access
add(h1('4 · Access', C.gold));
add(table(
  ['Variable', 'Default', 'What it does'],
  [[V('ADMIN_TOKEN'), def('(empty)'), 'When set, uploads require a matching X-Admin-Token header'],
  ], [30, 20, 50], C.gold));
add(spacer(60));
add(P('Empty by default, which means anyone with the URL can upload. That is deliberate for a laptop demo and wrong for a link you send to an investor. Reading stays open either way — only writes are gated. The portal asks for the token once and remembers it in the browser.'));
add(...code(['ADMIN_TOKEN=$(openssl rand -hex 24)'], C.washGold));

// ----------------------------------------------------------- 5. Assumptions
add(h1('5 · The stated assumptions', C.green));
add(P('These are the decisions that change the headline number. They are environment variables so that a disagreement about a threshold is a configuration change rather than an argument, and all four are displayed on the Overview screen with the variable name beside them.'));
add(callout('Why this works.',
  'The thresholds are pushed into the silver.parameter table at startup, and the SQL views read them from there. The number on screen and the number the calculation used are therefore the same number by construction, not by discipline.',
  C.green, C.washGreen));
add(spacer(120));
add(table(
  ['Variable', 'Default', 'Effect'],
  [
    [V('IMPLAUSIBLE_KWH_PER_KWP'), def('8.0'), 'Daily specific yield above which a day is flagged suspect and excluded'],
    [V('ZERO_DAY_POLICY'), def('suspect'), 'suspect — a zero day is flagged and excluded. ok — zero is a real, eligible zero'],
    [V('MISSING_DAY_POLICY'), def('exclude'), 'exclude — a missing day is a gap. zero — it counts as a covered day that generated nothing'],
    [V('TRUST_GRID_CONNECTION_DATE'), def('true'), 'Whether the supplied grid connection date is taken as given'],
  ], [34, 16, 50], C.green));

add(h3('IMPLAUSIBLE_KWH_PER_KWP — the most arguable number in the app'));
add(P('Daily specific yield is kWh generated divided by kWp installed. Above this threshold a day is flagged suspect and excluded. 8.0 is a placeholder for Armenia: a summer day at a well-sited plant can approach 7, while a meter reset reads as hundreds.'));
add(P('Raising it lets more energy through and raises your headline. Lowering it is conservative. Either way the excluded energy stays visible in the river rather than disappearing.'));
add(P('A site with no installed capacity — because only a device report was uploaded for it — cannot have a specific yield, so this rule cannot fire for it.'));

add(h3('ZERO_DAY_POLICY'));
add(P('A winter zero and a broken inverter look identical in this dataset. "suspect" is the conservative reading: it costs nothing in energy, because zero is zero either way, but it lowers the quality score — which is the honest signal.'));

add(h3('MISSING_DAY_POLICY'));
add(P('Neither setting invents energy; a missing day contributes nothing under both. The difference is coverage. "exclude" reports the gap and lowers your coverage percentage. "zero" makes the fleet look fully covered. "exclude" is the answer you can defend.'));

// ------------------------------------------------------- 6. Emission factor
add(new d.Paragraph({ children: [new d.PageBreak()] }));
add(h1('6 · The emission factor', C.red));
add(P('This is the number most likely to be challenged, so it is the one number that is not allowed to arrive without a provenance.'));
add(h3('What is seeded by default'));
add(P('Leave the variables below empty and the app seeds Table 1 of CDM Standardized Baseline ASB0038-2018 — the grid emission factor for the electricity system of the Republic of Armenia, 2016 vintage — as published:'));
add(table(
  ['tCO₂e/MWh', 'Margin', 'Applicable to', 'Used?'],
  [
    [[t('0.4329', { bold: true, size: 18, color: C.greenInk })], 'Combined', 'Wind and solar generation, all three crediting periods', [t('APPLIED', { bold: true, size: 16, color: C.greenInk })]],
    ['0.4620', 'Operating', 'All project activities', 'reference'],
    ['0.3456', 'Build', 'All project activities', 'reference'],
    ['0.4038', 'Combined', 'All except wind and solar, first crediting period', 'reference'],
    ['0.3748', 'Combined', 'All except wind and solar, second and third periods', 'reference'],
  ], [16, 16, 52, 16], C.red));
add(spacer(70));
add(P('All five are recorded so a verifier can see the others were considered rather than missed. Only the row marked active drives a number. For a solar fleet that is the combined margin for wind and solar generation: 0.4329.'));

add(callout('The baseline has lapsed.',
  'ASB0038-2018 is published valid from 19 February 2018 to 18 February 2021. Pilot data from 2025 therefore falls outside it, and by default the app finds no applicable factor, claims no reduction and no VCUs, and says so on screen.',
  C.red, C.washRed));
add(spacer(120));
add(table(
  ['Variable', 'Default', 'What it does'],
  [
    [V('ALLOW_EXPIRED_EMISSION_FACTOR'), def('false'), 'Apply the lapsed factor anyway. Every figure derived from it is then labelled as outside its published validity, on screen'],
    [V('DEFAULT_EMISSION_FACTOR'), def('(empty)'), 'Override the seeded table with a single factor of your own'],
    [V('DEFAULT_EMISSION_FACTOR_SOURCE'), def('(empty)'), 'The document, edition and table it came from'],
    [V('DEFAULT_EMISSION_FACTOR_URL'), def('(empty)'), 'Link to that document'],
    [V('DEFAULT_EMISSION_FACTOR_VINTAGE'), def('unset'), "The factor's vintage year"],
    [V('EMISSION_FACTOR_VALID_FROM / _TO'), def('(empty)'), 'Override the published validity window'],
  ], [36, 16, 48], C.red));

add(h3('Verifying a factor'));
add(P('Every factor is stored with verified = false, and no code path sets it true — a person does, after opening the source document and checking the row. Until then the portal badges the factor "unverified" and puts a warning beside every number derived from it.'));
add(...code([
  'UPDATE gold.emission_factor',
  `   SET verified = true, verified_by = 'name', verified_at = now()`,
  ' WHERE active;',
], C.washRed));

add(h3('Replacing it when a newer baseline is approved'));
add(P('Close the old row’s validity and insert the new one. Every Gold view joins the factor on date, so historical months keep the factor that was valid then and the new one applies going forward. Reload the page; there is nothing to recompute.'));
add(...code([
  `UPDATE gold.emission_factor SET valid_to = DATE '2021-02-18' WHERE active;`,
  '',
  'INSERT INTO gold.emission_factor',
  '  (value_tco2e_per_mwh, factor_type, project_types, source, source_url,',
  '   vintage, valid_from, valid_to, active, verified)',
  `VALUES (<value>, 'combined_margin', '<who it applies to>',`,
  `        '<document, edition and table>', '<url>', '<vintage>',`,
  `        DATE '<from>', DATE '<to>', true, false);`,
]));

// ----------------------------------------------------------------- 7. Price
add(h1('7 · The VCU price', C.gold));
add(table(
  ['Variable', 'Default', 'What it does'],
  [
    [V('DEFAULT_VCU_PRICE_PER_TCO2E'), def('(empty)'), 'Price per tonne. No default'],
    [V('VCU_PRICE_SOURCE'), def('(empty)'), 'The quote, index or broker it came from'],
    [V('PRICE_CURRENCY'), def('USD'), 'Currency label'],
  ], [36, 16, 48], C.gold));
add(spacer(60));
add(P('With no price set, no revenue figure is shown at all. That is better than a number nobody can source. These seed the gold.price table on first run only; afterwards prices are dated rows you insert, and each month uses the price in force at its end.'));
add(...code([
  `INSERT INTO gold.price (instrument, value, currency, source, as_of)`,
  `VALUES ('vcu', <price>, 'USD', '<broker or index>', DATE '<as of>');`,
], C.washGold));

// -------------------------------------------------------- 8. Presentation
add(h1('8 · Presentation', C.ink));
add(table(
  ['Variable', 'Default', 'What it does'],
  [
    [V('BANNER_TEXT'), def('Pilot data. Not verified.'), 'The banner on every screen'],
    [V('FLEET_NAME'), def('SANNOVA'), 'Shown beside the product name in the header'],
  ], [30, 26, 44], C.ink3));
add(spacer(60));
add(P('Leave the banner until the numbers have actually been verified. Change it then, not before.'));

// --------------------------------------------------------- 9. Minimum sets
add(h1('9 · Minimum sets', C.green));
add(h3('Local development'));
add(P('None required if Postgres is on localhost with the default credentials. Otherwise one line:'));
add(...code(['DATABASE_URL=postgresql://user:pass@host:5432/ccredits']));
add(h3('Azure, for a demo'));
add(...code([
  'DATABASE_URL=secretref:db-url          # ?sslmode=require',
  'ADMIN_TOKEN=secretref:admin-token',
  'APP_ENV=production',
  'STORAGE_BACKEND=azure',
  'AZURE_STORAGE_ACCOUNT=<account>',
  'AZURE_STORAGE_CONTAINER=uploads',
], C.washGreen));
add(h3('To show a carbon number from 2025 data'));
add(P('Add the two below, and be ready to explain both on screen — the portal will label them for you:'));
add(...code([
  'ALLOW_EXPIRED_EMISSION_FACTOR=true     # ASB0038-2018 lapsed 2021-02-18',
  'DEFAULT_VCU_PRICE_PER_TCO2E=<price>',
  'VCU_PRICE_SOURCE=<where that price came from>',
], C.washGold));

add(h1('10 · Checking what took effect', C.accent));
add(...code([
  'curl https://<your-app>/api/health',
  '{"status":"ok","database":true,"env":"production"}',
]));
add(P('/api/context returns the banner, the assumptions exactly as displayed, and the emission factors and prices currently in force — useful for confirming a variable actually landed. The Overview screen shows the same thing without a terminal.'));
add(P('If health reports "degraded", the message names the problem. It is almost always DATABASE_URL or a firewall rule.'));

const opts = K.docOptions('CCredits — Configuring the environment', 'CCredits · Guide 2 of 3 · Configuring the environment', C.blue);
opts.sections[0].children = body;
d.Packer.toBuffer(new d.Document(opts)).then((b) => {
  fs.writeFileSync('CCredits-2-Environment-Variables.docx', b);
  console.log('wrote CCredits-2-Environment-Variables.docx');
});
