'use strict';

/* ---------------------------------------------------------------- helpers */

const api = async (path) => {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
};
const num = (v) => (v === null || v === undefined || v === '' ? 0 : Number(v));
const fmt = (v, d = 0) => num(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = (id) => document.getElementById(id);
const monthName = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
const dayName = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
const pill = (flag) => `<span class="pill ${esc(flag)}">${esc(flag)}</span>`;

const busy = (id, cols) => {
    const node = el(id);
    node.innerHTML = node.tagName === 'TABLE'
        ? `<tbody><tr><td colspan="${cols || 8}" class="loading">Loading…</td></tr></tbody>`
        : `<div class="loading">Loading…</div>`;
};
const failed = (id, err, cols) => {
    const node = el(id);
    const msg = `Could not load this: ${esc(err.message)}`;
    node.innerHTML = node.tagName === 'TABLE'
        ? `<tbody><tr><td colspan="${cols || 8}" class="err" style="padding:22px;text-align:center">${msg}</td></tr></tbody>`
        : `<div class="err" style="padding:22px;text-align:center">${msg}</div>`;
};

const REASON_LABEL = {
    quality_suspect: 'Quality suspect',
    data_gap: 'Data gap',
    before_grid_connection: 'Before grid connection',
    site_inactive: 'Site inactive',
};

// The encoding colours live in the stylesheet as validated tokens; read them
// rather than restating them, so the palette has exactly one definition.
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const REASON_COLOR = {
    quality_suspect: '--suspect',
    data_gap: '--gap',
    before_grid_connection: '--pregrid',
    site_inactive: '--inactive',
};
// Decorative washes for the stat tiles. These carry no meaning — every tile is
// titled — so they are free to be soft and colourful.
const TILE_TINTS = ['--accent-wash', '--eligible-wash', '--violet-wash',
                    '--gold-wash', '--rose-wash', '--eligible-wash'];

let CONTEXT = null;

/* ------------------------------------------------------------ navigation */

document.querySelectorAll('nav button').forEach((b) => {
    b.onclick = () => {
        document.querySelectorAll('nav button').forEach((x) => x.classList.remove('active'));
        document.querySelectorAll('.screen').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        el('screen-' + b.dataset.screen).classList.add('active');
        if (b.dataset.screen === 'fleet') loadFleet();
        if (b.dataset.screen === 'quality') loadQuality();
        if (b.dataset.screen === 'upload') loadFiles();
    };
});

/* ------------------------------------------------------ 5.2 The river */

function drawRiver(d) {
    const svg = el('river');
    const W = 1000, H = 340, top = 40, bot = H - 30, usable = bot - top;
    const generation = num(d.generation_kwh);
    const eligible = num(d.eligible_kwh);
    const exclusions = (d.exclusions || []).map((e) => ({
        reason: e.exclusion_reason,
        kwh: num(e.excluded_kwh),
        days: num(e.day_count),
        sites: num(e.site_count),
    })).filter((e) => e.kwh > 0);

    if (generation <= 0) {
        svg.innerHTML = `<text x="500" y="170" text-anchor="middle" class="flow-sub">`
            + `No data loaded yet — upload a Sungrow export to fill this in.</text>`;
        return;
    }

    // Bands need a little height to hang a two-line label off, and a little
    // air between them, or the small exclusion streams collide.
    const GAP = 16, MIN_BAND = 15;
    const slack = usable - exclusions.length * GAP;
    const scale = Math.max(slack, usable * 0.5) / generation;
    const x0 = 150, x1 = x0 + 44, x2 = 560, x3 = 830;
    const parts = [];

    // Left node: everything generated.
    const cEligible = token('--eligible');
    parts.push(`<defs>
        <linearGradient id="genGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stop-color="#6f6e69"/>
            <stop offset="100%" stop-color="#4a4946"/>
        </linearGradient>
    </defs>`);
    parts.push(`<g class="flow-node" data-drill="months">
        <rect x="${x0}" y="${top}" width="44" height="${usable}" rx="5" fill="url(#genGrad)"/>
        <text x="${x0 - 12}" y="${top + usable / 2 - 6}" text-anchor="end" class="flow-label">Generated</text>
        <text x="${x0 - 12}" y="${top + usable / 2 + 11}" text-anchor="end" class="flow-sub">${fmt(generation)} kWh</text>
    </g>`);

    // Ribbons: eligible first (green), then each exclusion reason in grey.
    const ribbon = (yA, yB, h, fill, opacity) => {
        const c = (x1 + x2) / 2;
        return `<path d="M${x1},${yA} C${c},${yA} ${c},${yB} ${x2},${yB} L${x2},${yB + h} C${c},${yB + h} ${c},${yA + h} ${x1},${yA + h} Z"
                 fill="${fill}" opacity="${opacity}"/>`;
    };

    let cursorLeft = top;
    let cursorRight = top;
    const eligibleH = Math.max(eligible * scale, MIN_BAND);

    parts.push(ribbon(cursorLeft, cursorRight, eligibleH, cEligible, 0.30));
    parts.push(`<g class="flow-node" data-drill="months">
        <rect x="${x2}" y="${cursorRight}" width="42" height="${eligibleH}" rx="5" fill="${cEligible}"/>
        <text x="${x2 + 52}" y="${cursorRight + eligibleH / 2 - 5}" class="flow-label">Eligible</text>
        <text x="${x2 + 52}" y="${cursorRight + eligibleH / 2 + 11}" class="flow-sub">${fmt(eligible)} kWh</text>
    </g>`);
    const eligibleMid = cursorRight + eligibleH / 2;
    cursorLeft += eligibleH;
    cursorRight += eligibleH + GAP;

    exclusions.forEach((e) => {
        const h = Math.max(e.kwh * scale, MIN_BAND);
        const c = token(REASON_COLOR[e.reason] || '--ink-3');
        parts.push(ribbon(cursorLeft, cursorRight, h, c, 0.30));
        parts.push(`<g class="flow-node" data-reason="${esc(e.reason)}">
            <rect x="${x2}" y="${cursorRight}" width="42" height="${h}" rx="5" fill="${c}"/>
            <text x="${x2 + 52}" y="${cursorRight + h / 2 - 5}" class="flow-label">${esc(REASON_LABEL[e.reason] || e.reason)}</text>
            <text x="${x2 + 52}" y="${cursorRight + h / 2 + 11}" class="flow-sub">${fmt(e.kwh)} kWh · ${fmt(e.days)} site-days</text>
        </g>`);
        cursorLeft += h;
        cursorRight += h + GAP;
    });

    // Eligible energy continues on to the credits.
    parts.push(`<path d="M${x2 + 42},${eligibleMid - eligibleH / 2} L${x3 - 8},${eligibleMid - 28}
                 L${x3 - 8},${eligibleMid + 28} L${x2 + 42},${eligibleMid + eligibleH / 2} Z"
                 fill="${cEligible}" opacity="0.20"/>`);
    parts.push(`<g class="flow-node" data-drill="months">
        <rect x="${x3 - 8}" y="${eligibleMid - 28}" width="126" height="56" rx="10"
              fill="${token('--eligible-wash')}" stroke="${cEligible}" stroke-width="1.5"/>
        <text x="${x3 + 55}" y="${eligibleMid - 5}" text-anchor="middle" class="flow-label"
              style="font-weight:700;font-size:14px">${fmt(d.vcu_issued)} VCU</text>
        <text x="${x3 + 55}" y="${eligibleMid + 13}" text-anchor="middle" class="flow-sub">${d.net_reduction_tco2e === null ? 'no factor' : fmt(d.net_reduction_tco2e, 1) + ' tCO₂e'}</text>
    </g>`);

    parts.push(`<text x="${x0}" y="${top - 15}" class="flow-cap">All energy read from the files</text>`);
    parts.push(`<text x="${x2}" y="${top - 15}" class="flow-cap">Split by rule</text>`);
    parts.push(`<text x="${x3 - 8}" y="${top - 15}" class="flow-cap">Credits</text>`);

    svg.innerHTML = parts.join('');
    svg.querySelectorAll('[data-drill="months"]').forEach((g) => { g.onclick = () => openMonths(); });
    svg.querySelectorAll('[data-reason]').forEach((g) => { g.onclick = () => openReason(g.dataset.reason); });
}

/* ------------------------------------------------------------- overview */

async function loadOverview() {
    // Not 'reference' or 'assumptions': those are filled from /api/context by
    // renderContext(), and blanking them here would leave them blank for good.
    busy('kpis'); busy('monthsTable', 8); busy('calc');
    const [summary, river, calc, months] = await Promise.all([
        api('/api/summary'), api('/api/river'), api('/api/calculation'), api('/api/drill/months'),
    ]);

    const cur = river.currency || 'USD';
    el('kpis').innerHTML = [
        ['Energy generated', fmt(summary.generation_kwh), 'kWh', `${summary.site_count} sites · ${summary.window_start || '—'} to ${summary.window_end || '—'}`],
        ['Eligible energy', fmt(summary.eligible_kwh), 'kWh', `${fmt(summary.excluded_kwh)} kWh excluded with a reason`],
        ['Emission reduction', summary.net_reduction_tco2e === null ? '—' : fmt(summary.net_reduction_tco2e, 2), 'tCO₂e', 'Eligible MWh × emission factor'],
        ['VCUs issuable', fmt(summary.vcu_issued), '', `${fmt(summary.carry_forward_tco2e, 3)} tCO₂e carried forward`],
        ['Indicative revenue', `${cur} ${fmt(summary.total_revenue, 0)}`, '', 'VCUs at the reference price below'],
        ['Coverage', fmt(summary.coverage_pct, 1), '%', `${fmt(summary.days_missing)} of ${fmt(summary.site_days)} site-days missing`],
    ].map(([label, value, unit, foot], i) => `
        <div class="kpi-card clickable" data-open="months"
             style="--tint:${token(TILE_TINTS[i % TILE_TINTS.length])}">
            <div class="label">${label}</div>
            <div class="value">${value}<span class="unit">${unit}</span></div>
            <div class="foot">${foot}</div>
        </div>`).join('');
    el('kpis').querySelectorAll('[data-open="months"]').forEach((c) => { c.onclick = () => openMonths(); });

    drawRiver(river);

    el('calc').innerHTML = calc.steps.map((s) => `
        <div class="step">
            <div class="label">${esc(s.label)}</div>
            <div class="formula">${esc(s.formula)}</div>
            <div class="subst">${esc(s.substituted)}</div>
            <div class="result">${esc(s.result)}</div>
            ${s.warning ? `<div class="unverified">⚠ ${esc(s.warning)}</div>` : ''}
            <div class="src">${esc(s.source)}</div>
        </div>`).join('') + (calc.emission_factor !== null && calc.emission_factor !== undefined ? `
        <div class="step" style="border-left-color:transparent">
            <div class="formula">Emission factor ${esc(String(calc.emission_factor))} tCO₂e/MWh
                — ${esc((calc.factor_type || '').replace(/_/g, ' '))}, vintage ${esc(calc.factor_vintage)},
                published validity ${esc(calc.factor_valid_from)} → ${esc(calc.factor_valid_to || 'open')}</div>
            <div class="src">${esc(calc.factor_project_types || '')}<br>${esc(calc.factor_source)}
                ${calc.factor_verified ? '' : ' <span class="pill missing">unverified</span>'}</div>
        </div>` : `
        <div class="step" style="border-left-color:transparent">
            <div class="unverified">⚠ No emission factor applies to this period, so no
                emission reduction and no VCUs are claimed. Nothing stands in for it.</div>
        </div>`);

    el('monthsTable').innerHTML = `
        <thead><tr>
            <th>Month</th><th class="num">Generated kWh</th><th class="num">Eligible kWh</th>
            <th class="num">Excluded kWh</th><th class="num">tCO₂e</th><th class="num">VCU</th>
            <th class="num">Revenue</th><th>Quality</th>
        </tr></thead>
        <tbody>${months.map((m) => `
            <tr class="clickable" data-month="${m.month}">
                <td>${monthName(m.month)}</td>
                <td class="num">${fmt(m.generation_kwh)}</td>
                <td class="num">${fmt(m.eligible_kwh)}</td>
                <td class="num">${fmt(m.excluded_kwh)}</td>
                <td class="num">${m.net_reduction_tco2e === null ? '—' : fmt(m.net_reduction_tco2e, 2)}</td>
                <td class="num">${fmt(m.vcu_issued)}</td>
                <td class="num">${cur} ${fmt(m.total_revenue, 0)}</td>
                <td>${pill(m.worst_flag)}</td>
            </tr>`).join('') || `<tr><td colspan="8" class="empty">No data loaded yet.</td></tr>`}
        </tbody>`;
    el('monthsTable').querySelectorAll('[data-month]').forEach((tr) => {
        tr.onclick = () => openSites(tr.dataset.month);
    });
}

function renderContext() {
    el('banner').textContent = CONTEXT.banner;
    el('fleetName').textContent = CONTEXT.fleet_name ? `${CONTEXT.fleet_name} pilot` : '';

    el('assumptions').innerHTML = CONTEXT.assumptions.map((a) => `
        <div class="assumption">
            <div class="a-label">${esc(a.label)}</div>
            <div class="a-value">${esc(a.value)}</div>
            <div class="a-note">${esc(a.note)}</div>
            <div class="a-env">${esc(a.env)}</div>
        </div>`).join('');

    const factors = CONTEXT.emission_factors.map((f) => `
        <tr${f.active ? '' : ' style="opacity:.62"'}>
            <td>${esc(f.factor_type.replace(/_/g, ' '))}
                ${f.active ? '<span class="pill info">applied</span>' : '<span class="pill grey">reference</span>'}</td>
            <td class="num">${esc(String(f.value_tco2e_per_mwh))}</td>
            <td>${esc(f.valid_from)} → ${esc(f.valid_to || 'open')}</td>
            <td>${f.verified
                ? `<span class="pill ok">verified</span>`
                : `<span class="pill missing">unverified</span>`}</td></tr>
        <tr><td colspan="4" class="src-note">${esc(f.project_types || '')}<br>${esc(f.source)}</td></tr>`).join('')
        || `<tr><td colspan="4" class="empty">No emission factor on file. No reduction is claimed.</td></tr>`;
    const prices = CONTEXT.prices.map((p) => `
        <tr><td>${esc(p.instrument.toUpperCase())}</td><td class="num">${esc(String(p.value))} ${esc(p.currency)}</td>
            <td>${esc(p.as_of)}</td><td>${esc(p.source || '')}</td></tr>`).join('')
        || `<tr><td colspan="4" class="empty">No VCU price set, so no revenue is shown.</td></tr>`;
    el('reference').innerHTML = `
        <h3>Emission factors</h3>
        <div class="scroll"><table><thead><tr><th>Margin</th><th class="num">tCO₂e/MWh</th><th>Published validity</th><th>Checked</th></tr></thead>
        <tbody>${factors}</tbody></table></div>
        <h3 style="margin-top:16px">Prices</h3>
        <div class="scroll"><table><thead><tr><th>Instrument</th><th class="num">Price</th><th>As of</th><th>Source</th></tr></thead>
        <tbody>${prices}</tbody></table></div>`;
}

/* ------------------------------------------------------ 5.4 Fleet table */

let fleetRows = [], fleetSort = { key: 'plant_name', dir: 1 };

async function loadFleet() {
    busy('fleetTable', 10);
    try {
        fleetRows = await api('/api/fleet');
        renderFleet();
    } catch (err) { failed('fleetTable', err, 10); }
}

function renderFleet() {
    const cols = [
        ['plant_name', 'Site', 'text'],
        ['installed_kwp', 'kWp', 'num1'],
        ['grid_connection_date', 'Grid connection', 'text'],
        ['days_covered', 'Days covered', 'num'],
        ['generation_kwh', 'Total kWh', 'num'],
        ['eligible_kwh', 'Eligible kWh', 'num'],
        ['specific_yield_per_day', 'Specific yield', 'num2'],
        ['quality_score', 'Quality', 'score'],
        ['vcu_issued', 'VCU', 'num'],
    ];
    const rows = [...fleetRows].sort((a, b) => {
        const x = a[fleetSort.key], y = b[fleetSort.key];
        if (x === null) return 1;
        if (y === null) return -1;
        const cmp = typeof x === 'string' && isNaN(Number(x)) ? String(x).localeCompare(String(y)) : num(x) - num(y);
        return cmp * fleetSort.dir;
    });

    el('fleetTable').innerHTML = `
        <thead><tr>${cols.map(([k, label, type]) => `
            <th class="sortable ${type === 'text' ? '' : 'num'}" data-key="${k}">${label}
                <span class="arrow">${fleetSort.key === k ? (fleetSort.dir > 0 ? '▲' : '▼') : ''}</span></th>`).join('')}
            <th>Status</th></tr></thead>
        <tbody>${rows.map((r) => `
            <tr class="clickable" data-site="${esc(r.plant_name)}">
                <td>${esc(r.plant_name)}</td>
                <td class="num">${fmt(r.installed_kwp, 1)}</td>
                <td>${esc(r.grid_connection_date || '—')}</td>
                <td class="num">${fmt(r.days_covered)} / ${fmt(r.days_expected)}</td>
                <td class="num">${fmt(r.generation_kwh)}</td>
                <td class="num">${fmt(r.eligible_kwh)}</td>
                <td class="num">${r.specific_yield_per_day === null ? '—' : fmt(r.specific_yield_per_day, 2)}</td>
                <td class="num">${fmt(r.quality_score, 0)}%</td>
                <td class="num">${fmt(r.vcu_issued)}</td>
                <td>${esc(r.plant_status || '—')}</td>
            </tr>`).join('') || `<tr><td colspan="10" class="empty">No sites yet.</td></tr>`}
        </tbody>`;

    el('fleetTable').querySelectorAll('th.sortable').forEach((th) => {
        th.onclick = () => {
            const k = th.dataset.key;
            fleetSort = { key: k, dir: fleetSort.key === k ? -fleetSort.dir : 1 };
            renderFleet();
        };
    });
    el('fleetTable').querySelectorAll('[data-site]').forEach((tr) => {
        tr.onclick = () => openDays(tr.dataset.site, null);
    });
}

/* ------------------------------------------------- 5.6 Data quality */

async function loadQuality() {
    busy('qualityKpis'); busy('gapsTable', 4); busy('suspectsTable', 4); busy('overlapsTable', 4);
    let q;
    try {
        q = await api('/api/quality');
    } catch (err) {
        ['qualityKpis', 'gapsTable', 'suspectsTable', 'overlapsTable']
            .forEach((id) => failed(id, err, 4));
        return;
    }
    const total = num(q.site_days) || 1;
    el('qualityKpis').innerHTML = `
        <div class="kpi-card"><div class="label">Coverage</div>
            <div class="value">${fmt(q.coverage_pct, 1)}<span class="unit">%</span></div>
            <div class="foot">${fmt(q.site_days)} site-days in the loaded window</div>
            <div class="bar" style="margin-top:9px">
                <i class="ok" style="width:${(num(q.days_ok) / total) * 100}%"></i>
                <i class="suspect" style="width:${(num(q.days_suspect) / total) * 100}%"></i>
                <i class="missing" style="width:${(num(q.days_missing) / total) * 100}%"></i>
            </div></div>
        <div class="kpi-card"><div class="label">Days ok</div><div class="value">${fmt(q.days_ok)}</div></div>
        <div class="kpi-card"><div class="label">Days suspect</div><div class="value">${fmt(q.days_suspect)}</div>
            <div class="foot">Excluded, with the rule named</div></div>
        <div class="kpi-card"><div class="label">Days missing</div><div class="value">${fmt(q.days_missing)}</div>
            <div class="foot">Never interpolated</div></div>
        <div class="kpi-card"><div class="label">Superseded site-days</div><div class="value">${fmt(q.superseded_count)}</div>
            <div class="foot">Kept, not deleted</div></div>`;

    el('gapsTable').innerHTML = `
        <thead><tr><th>Site</th><th>From</th><th>To</th><th class="num">Days</th></tr></thead>
        <tbody>${(q.gaps || []).map((g) => `
            <tr class="clickable" data-site="${esc(g.plant_name)}">
                <td>${esc(g.plant_name)}</td><td>${dayName(g.gap_start)}</td>
                <td>${dayName(g.gap_end)}</td><td class="num">${fmt(g.days)}</td>
            </tr>`).join('') || `<tr><td colspan="4" class="empty">No gaps. Every expected site-day has data.</td></tr>`}
        </tbody>`;

    el('suspectsTable').innerHTML = `
        <thead><tr><th>Site</th><th>Date</th><th class="num">kWh</th><th>Rule that flagged it</th></tr></thead>
        <tbody>${(q.suspects || []).map((s) => `
            <tr class="clickable" data-site="${esc(s.plant_name)}">
                <td>${esc(s.plant_name)}</td><td>${dayName(s.reading_date)}</td>
                <td class="num">${fmt(s.generation_kwh, 1)}</td>
                <td style="color:var(--amber)">${esc(s.flag_reason || '')}</td>
            </tr>`).join('') || `<tr><td colspan="4" class="empty">No suspect days.</td></tr>`}
        </tbody>`;

    el('overlapsTable').innerHTML = `
        <thead><tr><th>New file</th><th>Overlaps</th><th>Period</th><th>Grain</th></tr></thead>
        <tbody>${(q.overlaps || []).map((o) => `
            <tr><td>#${o.new_file_id} ${esc(o.new_filename)}</td>
                <td>#${o.existing_file_id} ${esc(o.existing_filename)}</td>
                <td>${dayName(o.overlap_start)} → ${dayName(o.overlap_end)}</td>
                <td>${esc(o.grain)}</td></tr>`).join('')
            || `<tr><td colspan="4" class="empty">No overlapping uploads.</td></tr>`}
        </tbody>`;

    document.querySelectorAll('#gapsTable [data-site], #suspectsTable [data-site]').forEach((tr) => {
        tr.onclick = () => openDays(tr.dataset.site, null);
    });
}

/* -------------------------------------------- 5.3 Drill-down drawer */

let crumbs = [];

function openDrawer() { el('drawer').classList.add('open'); el('scrim').classList.add('open'); }
function closeDrawer() { el('drawer').classList.remove('open'); el('scrim').classList.remove('open'); }
el('drawerClose').onclick = closeDrawer;
el('scrim').onclick = closeDrawer;
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

function renderCrumbs() {
    el('crumbs').innerHTML = crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return `${i ? '<span class="sep">›</span>' : ''}<span class="crumb ${last ? 'current' : ''}" data-i="${i}">${esc(c.label)}</span>`;
    }).join('');
    el('crumbs').querySelectorAll('.crumb:not(.current)').forEach((c) => {
        c.onclick = () => { const step = crumbs[Number(c.dataset.i)]; crumbs = crumbs.slice(0, Number(c.dataset.i)); step.go(); };
    });
}

function drawerLoading() { el('drawerBody').innerHTML = `<div class="loading">Loading…</div>`; }

async function openMonths() {
    crumbs = [{ label: 'Credits', go: openMonths }];
    renderCrumbs(); openDrawer(); drawerLoading();
    const months = await api('/api/drill/months');
    el('drawerBody').innerHTML = `
        <p class="sub">Every month in the loaded window. Click one to see the sites inside it.</p>
        <div class="scroll"><table>
        <thead><tr><th>Month</th><th class="num">Eligible kWh</th><th class="num">tCO₂e</th>
            <th class="num">VCU</th><th>Quality</th></tr></thead>
        <tbody>${months.map((m) => `
            <tr class="clickable" data-month="${m.month}">
                <td>${monthName(m.month)}</td><td class="num">${fmt(m.eligible_kwh)}</td>
                <td class="num">${m.net_reduction_tco2e === null ? '—' : fmt(m.net_reduction_tco2e, 2)}</td>
                <td class="num">${fmt(m.vcu_issued)}</td>
                <td>${pill(m.worst_flag)}</td></tr>`).join('')}
        </tbody></table></div>`;
    el('drawerBody').querySelectorAll('[data-month]').forEach((tr) => {
        tr.onclick = () => openSites(tr.dataset.month);
    });
}

async function openSites(month) {
    crumbs = [{ label: 'Credits', go: openMonths }, { label: monthName(month), go: () => openSites(month) }];
    renderCrumbs(); openDrawer(); drawerLoading();
    const sites = await api(`/api/drill/months/${month}/sites`);
    el('drawerBody').innerHTML = `
        <p class="sub">Sites contributing to ${esc(monthName(month))}. Click one to see its days.</p>
        <div class="scroll"><table>
        <thead><tr><th>Site</th><th class="num">Generated</th><th class="num">Eligible</th>
            <th class="num">Excluded</th><th class="num">VCU</th><th class="num">Days</th><th>Quality</th></tr></thead>
        <tbody>${sites.map((s) => `
            <tr class="clickable" data-site="${esc(s.plant_name)}">
                <td>${esc(s.plant_name)}</td>
                <td class="num">${fmt(s.generation_kwh)}</td>
                <td class="num">${fmt(s.eligible_kwh)}</td>
                <td class="num">${fmt(s.excluded_kwh)}</td>
                <td class="num">${fmt(s.vcu_issued)}</td>
                <td class="num">${fmt(s.days_with_data)}/${fmt(s.days_expected)}</td>
                <td>${pill(s.worst_flag)}</td></tr>`).join('')}
        </tbody></table></div>`;
    el('drawerBody').querySelectorAll('[data-site]').forEach((tr) => {
        tr.onclick = () => openDays(tr.dataset.site, month);
    });
}

async function openDays(site, month) {
    const base = month
        ? [{ label: 'Credits', go: openMonths }, { label: monthName(month), go: () => openSites(month) }]
        : [];
    crumbs = [...base, { label: site, go: () => openDays(site, month) }];
    renderCrumbs(); openDrawer(); drawerLoading();
    const url = `/api/drill/sites/${encodeURIComponent(site)}/days` + (month ? `?month=${month}` : '');
    const days = await api(url);
    el('drawerBody').innerHTML = `
        <p class="sub">Every day in the window for ${esc(site)}, including the days with no data.
            Click a day to see the spreadsheet cells behind it.</p>
        <div class="scroll"><table>
        <thead><tr><th>Date</th><th class="num">kWh</th><th class="num">Eligible</th>
            <th class="num">kWh/kWp</th><th>Flag</th><th>Reason</th></tr></thead>
        <tbody>${days.map((d) => `
            <tr class="clickable" data-day="${d.reading_date}">
                <td>${dayName(d.reading_date)}</td>
                <td class="num">${fmt(d.generation_kwh, 1)}</td>
                <td class="num">${fmt(d.eligible_kwh, 1)}</td>
                <td class="num">${d.specific_yield === null ? '—' : fmt(d.specific_yield, 2)}</td>
                <td>${pill(d.flag)}</td>
                <td style="color:var(--muted)">${esc(d.flag_reason || REASON_LABEL[d.exclusion_reason] || '')}</td>
            </tr>`).join('')}
        </tbody></table></div>`;
    el('drawerBody').querySelectorAll('[data-day]').forEach((tr) => {
        tr.onclick = () => openReadings(site, tr.dataset.day, month);
    });
}

async function openReadings(site, day, month) {
    const base = month
        ? [{ label: 'Credits', go: openMonths }, { label: monthName(month), go: () => openSites(month) }]
        : [];
    crumbs = [...base, { label: site, go: () => openDays(site, month) }, { label: dayName(day), go: () => openReadings(site, day, month) }];
    renderCrumbs(); openDrawer(); drawerLoading();
    const d = await api(`/api/drill/sites/${encodeURIComponent(site)}/days/${day}/readings`);
    if (!d.readings.length) {
        el('drawerBody').innerHTML = `<div class="empty">No rows in any uploaded file for ${esc(site)} on ${esc(dayName(day))}.
            That is what makes this day a gap — nothing was interpolated to fill it.</div>`;
        return;
    }
    el('drawerBody').innerHTML = `
        <p class="sub">The bottom of the chain: the actual cells this day's number came from,
            with the file, its SHA-256, and the row and column in the sheet.</p>
        <div class="scroll"><table>
        <thead><tr><th>Metric</th><th>Device</th><th class="num">Value</th><th>Cell</th>
            <th>File</th><th>Used</th></tr></thead>
        <tbody>${d.readings.map((r) => `
            <tr>
                <td>${esc(r.metric_label || r.metric)}</td>
                <td class="mono">${esc(r.device_sn || '—')}</td>
                <td class="num">${fmt(r.value, 2)} ${esc(r.unit || '')}</td>
                <td class="mono">${esc(r.source_col || '')}${esc(String(r.source_row))}</td>
                <td>#${r.file_id} ${esc(r.filename)}<div class="hash">${esc(r.sha256)}</div></td>
                <td>${r.is_winner ? '<span class="pill ok">used</span>' : '<span class="pill grey">superseded</span>'}</td>
            </tr>`).join('')}
        </tbody></table></div>`;
}

async function openReason(reason) {
    crumbs = [{ label: REASON_LABEL[reason] || reason, go: () => openReason(reason) }];
    renderCrumbs(); openDrawer(); drawerLoading();
    const q = await api('/api/quality');
    const rows = reason === 'quality_suspect' ? (q.suspects || []) : [];
    const gaps = reason === 'data_gap' ? (q.gaps || []) : [];
    let body = `<p class="sub">Energy excluded from the credit calculation because of this rule.</p>`;
    if (rows.length) {
        body += `<div class="scroll"><table>
            <thead><tr><th>Site</th><th>Date</th><th class="num">kWh</th><th>Rule</th></tr></thead>
            <tbody>${rows.map((s) => `<tr class="clickable" data-site="${esc(s.plant_name)}" data-day="${s.reading_date}">
                <td>${esc(s.plant_name)}</td><td>${dayName(s.reading_date)}</td>
                <td class="num">${fmt(s.generation_kwh, 1)}</td>
                <td style="color:var(--amber)">${esc(s.flag_reason || '')}</td></tr>`).join('')}</tbody></table></div>`;
    } else if (gaps.length) {
        body += `<div class="scroll"><table>
            <thead><tr><th>Site</th><th>From</th><th>To</th><th class="num">Days</th></tr></thead>
            <tbody>${gaps.map((g) => `<tr class="clickable" data-site="${esc(g.plant_name)}">
                <td>${esc(g.plant_name)}</td><td>${dayName(g.gap_start)}</td>
                <td>${dayName(g.gap_end)}</td><td class="num">${fmt(g.days)}</td></tr>`).join('')}</tbody></table></div>`;
    } else {
        body += `<p class="sub">Open the Fleet or Data quality screen to see the sites this applies to.</p>`;
    }
    el('drawerBody').innerHTML = body;
    el('drawerBody').querySelectorAll('[data-site]').forEach((tr) => {
        tr.onclick = () => (tr.dataset.day ? openReadings(tr.dataset.site, tr.dataset.day, null) : openDays(tr.dataset.site, null));
    });
}

/* -------------------------------------------------- Reconciliation */

el('runRecon').onclick = async () => {
    const btn = el('runRecon');
    btn.disabled = true;
    el('reconResult').innerHTML = `<div class="loading">Re-reading the original files and
        recomputing every figure…</div>`;
    try {
        const d = await api('/api/reconcile');
        const badge = (st) => st === 'pass' ? '<span class="pill ok">pass</span>'
                            : st === 'fail' ? '<span class="pill missing">fail</span>'
                            : '<span class="pill grey">skipped</span>';
        el('reconResult').innerHTML = `
            <div class="${d.ok ? 'recon-ok' : 'unverified'}" style="margin-top:14px">
                ${d.ok
                    ? `✓ ${d.passed} checks passed. Every figure was reproduced independently and matched.`
                    : `⚠ ${d.failed} of ${d.passed + d.failed} checks did not pass. Details below.`}
                ${d.skipped ? ` ${d.skipped} skipped.` : ''}
            </div>
            <div class="scroll" style="margin-top:14px"><table>
            <thead><tr><th>Check</th><th>Result</th><th class="num">Views say</th>
                <th class="num">Recomputed</th></tr></thead>
            <tbody>${d.checks.map((k) => `
                <tr>
                    <td>${esc(k.title)}
                        <div class="src-note">${esc(k.detail || '')}</div>
                        ${(k.offenders || []).map((o) => `<div class="src-note" style="color:var(--gap-ink)">— ${esc(o)}</div>`).join('')}</td>
                    <td>${badge(k.status)}</td>
                    <td class="num mono">${esc(k.app || '—')}</td>
                    <td class="num mono">${esc(k.recomputed || '—')}</td>
                </tr>`).join('')}
            </tbody></table></div>`;
    } catch (err) {
        el('reconResult').innerHTML = `<div class="err" style="margin-top:14px">Could not run: ${esc(err.message)}</div>`;
    }
    btn.disabled = false;
};

/* ------------------------------------------------------ 5.1 Upload */

const drop = el('drop'), fileInput = el('fileInput');
drop.onclick = () => fileInput.click();
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); send(e.dataTransfer.files); };
fileInput.onchange = () => send(fileInput.files);

async function send(fileList) {
    if (!fileList || !fileList.length) return;
    const form = new FormData();
    [...fileList].forEach((f) => form.append('files', f));
    el('uploadResults').innerHTML = `<div class="loading">Parsing ${fileList.length} file(s)…</div>`;
    try {
        const token = localStorage.getItem('ccredits_admin_token') || '';
        const r = await fetch('/api/upload', {
            method: 'POST', body: form, headers: token ? { 'X-Admin-Token': token } : {},
        });
        if (r.status === 401) {
            const entered = prompt('This portal requires an upload token (ADMIN_TOKEN):');
            if (entered) { localStorage.setItem('ccredits_admin_token', entered); return send(fileList); }
            el('uploadResults').innerHTML = `<div class="err">Upload needs a token.</div>`;
            return;
        }
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        renderResults((await r.json()).results);
        await Promise.all([loadOverview(), loadFiles()]);
    } catch (err) {
        el('uploadResults').innerHTML = `<div class="err">Upload failed: ${esc(err.message)}</div>`;
    }
    fileInput.value = '';
}

function renderResults(results) {
    el('uploadResults').innerHTML = results.map((r) => {
        const blanks = Object.entries(r.blank_by_metric || {});
        const kept = Object.entries(r.kept_by_metric || {});
        const fact = (k, v) => `<div class="fact"><div class="k">${k}</div><div class="v">${v}</div></div>`;
        return `<div class="result ${esc(r.status)}">
            <div class="name">${esc(r.filename)} ${pill(r.status === 'loaded' ? 'ok' : r.status === 'duplicate' ? 'suspect' : 'missing')}
                <span style="color:var(--muted);font-weight:400;font-size:12.5px">${esc(r.message || '')}</span></div>
            ${r.status === 'loaded' ? `<div class="facts">
                ${fact('Layout', `${esc(r.layout)} · ${esc(r.grain)} grain`)}
                ${fact('Sheet', `${esc(r.sheet_name || '—')} (header row ${esc(String(r.header_row))})`)}
                ${fact('Period', `${esc(r.period_start || '—')} → ${esc(r.period_end || '—')}`)}
                ${fact('Rows read', fmt(r.row_count))}
                ${fact('Values kept', fmt(r.values_kept))}
                ${fact('Values blank', fmt(r.values_blank))}
                ${fact('Readings', fmt(r.reading_count))}
                ${fact('Sites', fmt(r.sites_upserted))}
                ${fact('SHA-256', `<span class="hash">${esc((r.sha256 || '').slice(0, 24))}…</span>`)}
            </div>
            ${kept.length ? `<div class="note" style="color:var(--muted)">Kept by metric: ${kept.map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(' · ')}</div>` : ''}
            ${blanks.length ? `<div class="note">Blank by metric: ${blanks.map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(' · ')}</div>` : ''}
            ${(r.overlaps || []).map((o) => `<div class="note">Overlaps #${o.file_id} ${esc(o.filename)} (${esc(o.overlap_start)} → ${esc(o.overlap_end)})</div>`).join('')}
            ` : ''}
            ${(r.notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join('')}
        </div>`;
    }).join('');
}

async function loadFiles() {
    busy('filesTable', 9);
    let files;
    try { files = await api('/api/files'); }
    catch (err) { return failed('filesTable', err, 9); }
    el('filesTable').innerHTML = `
        <thead><tr><th>#</th><th>File</th><th>Grain</th><th>Period</th>
            <th class="num">Rows</th><th class="num">Kept</th><th class="num">Blank</th>
            <th class="num">Readings</th><th>Uploaded</th></tr></thead>
        <tbody>${files.map((f) => `
            <tr><td>${f.file_id}</td>
                <td>${esc(f.filename)}<div class="hash">${esc(f.sha256)}</div></td>
                <td>${esc(f.grain)}</td>
                <td>${esc(f.period_start || '—')} → ${esc(f.period_end || '—')}</td>
                <td class="num">${fmt(f.row_count)}</td>
                <td class="num">${fmt(f.values_kept)}</td>
                <td class="num">${fmt(f.values_blank)}</td>
                <td class="num">${fmt(f.reading_count)}</td>
                <td>${new Date(f.uploaded_at).toLocaleString()}</td></tr>`).join('')
            || `<tr><td colspan="9" class="empty">Nothing loaded yet.</td></tr>`}
        </tbody>`;
}

/* ---------------------------------------------------------------- boot */

(async function boot() {
    try {
        CONTEXT = await api('/api/context');
        renderContext();
        await loadOverview();
    } catch (err) {
        document.querySelector('main').insertAdjacentHTML('afterbegin',
            `<div class="panel err">Could not reach the API: ${esc(err.message)}</div>`);
    }
})();
