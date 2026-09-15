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

// A month is almost never wholly clean across a large fleet, so a bare
// "missing" badge said nothing useful. Show how much of it is covered instead,
// with the shortfall visible rather than named.


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

// The encoding colours live in the stylesheet as validated tokens; read them
// rather than restating them, so the palette has exactly one definition.
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// Decorative washes for the stat tiles. These carry no meaning, every tile is
// titled, so they are free to be soft and colourful.
const TILE_TINTS = ['--accent-wash', '--eligible-wash', '--violet-wash',
                    '--gold-wash', '--rose-wash', '--eligible-wash'];

let CONTEXT = null;

/* ------------------------------------------------------------ navigation */

const goto_ = (name) => document.querySelector(`nav button[data-screen="${name}"]`).click();
el('brandHome').onclick = () => goto_('overview');
el('heroConnect').onclick = () => goto_('upload');


document.querySelectorAll('nav button').forEach((b) => {
    b.onclick = () => {
        document.querySelectorAll('nav button').forEach((x) => x.classList.remove('active'));
        document.querySelectorAll('.screen').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        el('screen-' + b.dataset.screen).classList.add('active');
        if (b.dataset.screen === 'energy') loadEnergy();
        if (b.dataset.screen === 'ledger') { loadLedger(); loadVisitors(); }
        if (b.dataset.screen === 'fleet') loadFleet();
        if (b.dataset.screen === 'upload') loadFiles();
    };
});

/* --------------------------------------------------- The flow diagram */

function drawFlow(d) {
    const svg = el('river');
    const gen = num(d.generation_kwh);
    if (gen <= 0) {
        svg.innerHTML = `<text x="500" y="125" text-anchor="middle" class="flow-sub">`
            + `No data yet, connect your solar data to fill this in.</text>`;
        return;
    }
    const green = token('--eligible'), accent = token('--accent'), violet = token('--pregrid');
    const box = (x, w, fill, stroke, title, value, sub) => `
        <g class="flow-node">
            <rect x="${x}" y="70" width="${w}" height="110" rx="14" fill="${fill}"
                  stroke="${stroke}" stroke-width="1.5"/>
            <text x="${x + w / 2}" y="100" text-anchor="middle" class="flow-cap">${title}</text>
            <text x="${x + w / 2}" y="137" text-anchor="middle" class="flow-big">${value}</text>
            <text x="${x + w / 2}" y="160" text-anchor="middle" class="flow-sub">${sub}</text>
        </g>`;
    const arrow = (x, label) => `
        <g>
            <path d="M${x},125 L${x + 58},125" stroke="${token('--ink-3')}" stroke-width="2"
                  marker-end="url(#arrowhead)"/>
            <text x="${x + 29}" y="110" text-anchor="middle" class="flow-sub">${label}</text>
        </g>`;
    const ef = d.emission_factor === null || d.emission_factor === undefined
        ? '' : `× ${Number(d.emission_factor)}`;
    svg.innerHTML = `
        <defs><marker id="arrowhead" markerWidth="7" markerHeight="7" refX="6" refY="3.5"
                      orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="${token('--ink-3')}"/></marker></defs>
        ${box(40, 270, token('--accent-wash'), accent, 'ENERGY GENERATED',
              fmt(d.generation_kwh) + ' kWh', fmt(d.generation_mwh, 1) + ' MWh')}
        ${arrow(318, ef)}
        ${box(385, 260, token('--violet-wash'), violet, 'EMISSION REDUCTION',
              d.net_reduction_tco2e === null ? '' : fmt(d.net_reduction_tco2e, 2),
              'tonnes of CO₂ avoided')}
        ${arrow(655, '1 : 1')}
        ${box(725, 235, token('--eligible-wash'), green, 'CREDITS (VCUS)',
              fmt(d.vcu_issued, 2), `${esc(d.currency)} ${fmt(d.total_revenue, 0)} indicative`)}`;
    svg.querySelectorAll('.flow-node').forEach((g) => { g.onclick = () => openMonths(); });
}

/* ------------------------------------------------------------- overview */

async function loadOverview() {
    // Not 'reference' or 'assumptions': those are filled from /api/context by
    // renderContext(), and blanking them here would leave them blank for good.
    busy('kpis'); busy('monthsTable', 6); busy('calc');
    const [summary, flow, calc, months] = await Promise.all([
        api('/api/summary'), api('/api/flow'), api('/api/calculation'), api('/api/drill/months'),
    ]);

    const cur = flow.currency || 'USD';
    // Capacity has a card of its own now, so the line above no longer repeats it.
    el('contextLine').textContent =
        `${fmt(summary.days_with_data)} days of readings · `
        + `${summary.window_start || ''} to ${summary.window_end || ''}`;

    // Each card leads where a reader would expect: capacity to the site list,
    // energy to the energy screen, the credit figures to the month breakdown.
    el('kpis').innerHTML = [
        ['Installed capacity', fmt(summary.installed_kwp, 0), 'kWp',
         `across ${fmt(summary.site_count)} sites`, 'screen:fleet'],
        ['Energy generated', fmt(summary.generation_kwh), 'kWh',
         'everything the sites produced', 'screen:energy'],
        ['CO₂ avoided', summary.net_reduction_tco2e === null ? '' : fmt(summary.net_reduction_tco2e, 2), 'tonnes',
         'energy × the Armenian grid factor', 'months'],
        ['Credits earned', fmt(summary.vcu_issued, 2), 'VCUs',
         'one tonne avoided is one credit', 'months'],
        [`Indicative value`, `${cur} ${fmt(summary.total_revenue, 0)}`, '',
         `at ${cur} ${fmt(calc.vcu_price_per_tco2e, 0)} per tonne`, 'screen:ledger'],
    ].map(([label, value, unit, foot, go], i) => `
        <div class="kpi-card clickable" data-go="${go}"
             style="--tint:${token(TILE_TINTS[i % TILE_TINTS.length])}">
            <div class="label">${label}</div>
            <div class="value">${value}<span class="unit">${unit}</span></div>
            <div class="foot">${foot}</div>
        </div>`).join('');

    el('kpis').querySelectorAll('[data-go]').forEach((c) => {
        const go = c.dataset.go;
        c.onclick = () => (go.startsWith('screen:') ? goto_(go.slice(7)) : openMonths());
    });

    const loaded = num(summary.generation_kwh) > 0;
    el('emptyState').hidden = loaded;
    el('hasData').hidden = !loaded;
    if (!loaded) return;

    drawFlow(flow);

    el('calc').innerHTML = calc.steps.map((s) => `
        <div class="step">
            <div class="label">${esc(s.label)}</div>
            <div class="formula">${esc(s.formula)}</div>
            <div class="subst">${esc(s.substituted)}</div>
            <div class="result">${esc(s.result)}</div>
            ${s.warning ? `<div class="unverified">${esc(s.warning)}</div>` : ''}

        </div>`).join('');

    el('monthsTable').innerHTML = `
        <thead><tr>
            <th>Month</th><th class="num">Generated kWh</th>
            <th class="num">tCO₂e</th><th class="num">VCU</th><th class="num">Revenue</th>
        </tr></thead>
        <tbody>${months.map((m) => `
            <tr class="clickable" data-month="${m.month}">
                <td>${monthName(m.month)}</td>
                <td class="num">${fmt(m.generation_kwh)}</td>
                <td class="num">${m.net_reduction_tco2e === null ? '' : fmt(m.net_reduction_tco2e, 2)}</td>
                <td class="num">${fmt(m.vcu_issued, 2)}</td>
                <td class="num">${cur} ${fmt(m.total_revenue, 0)}</td>
            </tr>`).join('') || `<tr><td colspan="8" class="empty">No data loaded yet.</td></tr>`}
        </tbody>`;
    el('monthsTable').querySelectorAll('[data-month]').forEach((tr) => {
        tr.onclick = () => openSites(tr.dataset.month);
    });
}

function renderContext() {
    el('banner').textContent = CONTEXT.banner;
    el('fleetName').textContent = CONTEXT.fleet_name ? `${CONTEXT.fleet_name} pilot` : '';



    const active = (CONTEXT.emission_factors || []).find((f) => f.active);
    const others = (CONTEXT.emission_factors || []).filter((f) => !f.active);
    const price = (CONTEXT.prices || [])[0];

    const factorBlock = active ? `
        <div class="input-card">
            <div class="input-head">
                <div>
                    <div class="input-label">Grid emission factor</div>
                    <div class="input-value">${esc(String(active.value_tco2e_per_mwh))}
                        <span class="input-unit">tCO₂e per MWh</span></div>
                </div>
                ${active.verified
                    ? `<span class="pill ok" title="Checked against the source document${
                          active.verified_by ? ' by ' + esc(active.verified_by) : ''}">checked</span>`
                    : '<span class="pill missing">not yet checked</span>'}
            </div>
            <div class="input-why">Every MWh of solar generated displaces a MWh the Armenian grid
                would otherwise have supplied. This is how much CO₂ that MWh would have emitted,
                which is what makes the generation worth a credit.</div>
            <div class="input-src">
                ${esc(active.source)}<br>
                Applies to: ${esc(active.project_types || '')}<br>
                ${active.published_valid_to
                    ? `Publication's stated validity ends ${esc(active.published_valid_to)}; applied beyond it as the most recent approved baseline for Armenia.`
                    : ''}
                ${active.source_url
                    ? `<br><a href="${esc(active.source_url)}" target="_blank" rel="noopener">View the source document</a>`
                    : ''}
            </div>
        </div>` : `<div class="empty">No emission factor on file, so no credits are claimed.</div>`;

    const priceBlock = price ? `
        <div class="input-card">
            <div class="input-head">
                <div>
                    <div class="input-label">VCU price</div>
                    <div class="input-value">${esc(String(Number(price.value)))}
                        <span class="input-unit">${esc(price.currency)} per tonne</span></div>
                </div>
                <span class="pill grey">indicative</span>
            </div>
            <div class="input-why">One VCU is one tonne of CO₂ avoided. This is what a tonne is
                assumed to sell for, and it only affects the revenue figure, never the number
                of credits.</div>
            <div class="input-src">${esc(price.source || '')} · as of ${esc(price.as_of)}</div>
        </div>` : `<div class="empty">No VCU price set, so no revenue is shown.</div>`;

    const otherBlock = others.length ? `
        <details class="more">
            <summary>The other margins published in the same table (${others.length})</summary>
            <div class="scroll"><table><thead><tr><th>Margin</th><th class="num">tCO₂e/MWh</th>
                <th>Applies to</th></tr></thead><tbody>${others.map((f) => `
                <tr><td>${esc(f.factor_type.replace(/_/g, ' '))}</td>
                    <td class="num">${esc(String(f.value_tco2e_per_mwh))}</td>
                    <td>${esc(f.project_types || '')}</td></tr>`).join('')}
            </tbody></table></div>
            <div class="input-src" style="margin-top:8px">Recorded so it is visible that they were
                considered. Only the one above is applied, it is the row for solar generation.</div>
        </details>` : '';

    el('reference').innerHTML = factorBlock + priceBlock + otherBlock;
}

/* ------------------------------------------------------- Energy browser */

let energyGroup = 'month', energySite = '';

document.querySelectorAll('#energyGroup button').forEach((b) => {
    b.onclick = () => {
        document.querySelectorAll('#energyGroup button').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        energyGroup = b.dataset.group;
        loadEnergy();
    };
});
el('energySite').onchange = () => { energySite = el('energySite').value; loadEnergy(); };

const BUCKET_LABEL = {
    month: (v) => monthName(v),
    day: (v) => dayName(v),
    site: (v) => v,
    region: (v) => v,
};

async function loadEnergy() {
    busy('energyKpis'); busy('energyTable', 8);
    let d;
    try {
        d = await api(`/api/energy?group=${energyGroup}`
            + (energySite ? `&site=${encodeURIComponent(energySite)}` : '')
            + (activeRegion ? `&region=${encodeURIComponent(activeRegion)}` : ''));
    } catch (err) { failed('energyKpis', err); return failed('energyTable', err, 8); }

    // Populate the site filter once.
    const sel = el('energySite');
    if (sel.options.length <= 1) {
        try {
            const sites = await api('/api/fleet');
            sites.forEach((s) => sel.add(new Option(s.site, s.site_code)));
        } catch { /* the filter is a convenience; the table still works */ }
    }

    const t = d.totals || {};
    el('energyKpis').innerHTML = [
        ['Energy generated', fmt(t.generation_kwh), 'kWh', `${t.site_count || 0} site(s) in view`],
        ['Installed capacity', fmt(t.installed_kwp, 0), 'kWp', 'from the plant information columns'],
        ['Days of data', fmt(t.days_with_data), '', 'readings actually present in the files'],
    ].map(([label, value, unit, foot], i) => `
        <div class="kpi-card" style="--tint:${token(TILE_TINTS[i % TILE_TINTS.length])}">
            <div class="label">${label}</div>
            <div class="value">${value}<span class="unit">${unit}</span></div>
            <div class="foot">${foot}</div>
        </div>`).join('');

    drawMap();
    const head = { month: 'Month', day: 'Day', site: 'Site', region: 'Region' }[d.group];
    const label = BUCKET_LABEL[d.group];
    el('energyTable').innerHTML = `
        <thead><tr>
            <th>${head}</th><th class="num">Generated kWh</th><th class="num">kWp</th>
            <th class="num">kWh/kWp/day</th><th class="num">Days</th><th class="num">Sites</th>
        </tr></thead>
        <tbody>${(d.rows || []).map((r) => `
            <tr>
                <td>${esc(label(r.bucket))}</td>
                <td class="num">${fmt(r.generation_kwh)}</td>
                <td class="num">${r.installed_kwp === null ? '' : fmt(r.installed_kwp, 0)}</td>
                <td class="num">${r.specific_yield_per_day === null ? '' : fmt(r.specific_yield_per_day, 2)}</td>
                <td class="num">${fmt(r.days_with_data)}</td>
                <td class="num">${fmt(r.site_count)}</td>
            </tr>`).join('') || `<tr><td colspan="6" class="empty">Nothing to show yet.</td></tr>`}
        </tbody>`;
}

/* ------------------------------------------------------ 5.4 Fleet table */

let fleetRows = [], fleetSort = { key: 'site', dir: 1 };

async function loadFleet() {
    busy('fleetKpis'); busy('fleetTable', 10);
    try {
        fleetRows = await api('/api/fleet');
        renderFleetKpis();
        renderFleet();
    } catch (err) { failed('fleetKpis', err); failed('fleetTable', err, 10); }
}

function renderFleetKpis() {
    const kwp = fleetRows.reduce((a, r) => a + num(r.installed_kwp), 0);
    const kwh = fleetRows.reduce((a, r) => a + num(r.generation_kwh), 0);
    const vcu = fleetRows.reduce((a, r) => a + num(r.vcu_issued), 0);
    const regions = new Set(fleetRows.map((r) => r.region).filter(Boolean)).size;
    el('fleetKpis').innerHTML = [
        ['Sites', fmt(fleetRows.length), '', `${fmt(regions)} province(s)`],
        ['Installed capacity', fmt(kwp, 0), 'kWp', 'total across the fleet'],
        ['Energy generated', fmt(kwh), 'kWh', 'everything these sites produced'],
        ['Credits earned', fmt(vcu, 2), 'VCUs', 'one tonne avoided is one credit'],
    ].map(([label, value, unit, foot], i) => `
        <div class="kpi-card" style="--tint:${token(TILE_TINTS[i % TILE_TINTS.length])}">
            <div class="label">${label}</div>
            <div class="value">${value}<span class="unit">${unit}</span></div>
            <div class="foot">${foot}</div>
        </div>`).join('');
}

function renderFleet() {
    const cols = [
        ['site', 'Site', 'text'],
        ['region', 'Region', 'text'],
        ['installed_kwp', 'kWp', 'num1'],
        ['grid_connection_date', 'Grid connection', 'text'],
        ['generation_kwh', 'Total kWh', 'num'],
        ['specific_yield_per_day', 'kWh/kWp/day', 'num2'],
        ['vcu_issued', 'VCU', 'num'],
    ];
    if (CONTEXT && !CONTEXT.real_names) {
        el('privacyNote').innerHTML = 'Sites are shown by code rather than by client name. '
            + 'The codes are stable, so the same site is the same code on every screen.';
    }
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
            <th class="num">Days</th><th>Status</th></tr></thead>
        <tbody>${rows.map((r) => `
            <tr class="clickable" data-site="${esc(r.site_code)}">
                <td><strong>${esc(r.site)}</strong></td>
                <td>${esc(r.region || '')}</td>
                <td class="num">${fmt(r.installed_kwp, 1)}</td>
                <td>${esc(r.grid_connection_date || '')}</td>
                <td class="num">${fmt(r.generation_kwh)}</td>
                <td class="num">${r.specific_yield_per_day === null ? '' : fmt(r.specific_yield_per_day, 2)}</td>
                <td class="num">${fmt(r.vcu_issued, 2)}</td>
                <td class="num">${fmt(r.days_with_data)}</td>
                <td>${esc(r.plant_status || '')}</td>
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
        <thead><tr><th>Month</th><th class="num">Generated kWh</th><th class="num">tCO₂e</th>
            <th class="num">VCU</th></tr></thead>
        <tbody>${months.map((m) => `
            <tr class="clickable" data-month="${m.month}">
                <td>${monthName(m.month)}</td><td class="num">${fmt(m.generation_kwh)}</td>
                <td class="num">${m.net_reduction_tco2e === null ? '' : fmt(m.net_reduction_tco2e, 2)}</td>
                <td class="num">${fmt(m.vcu_issued, 2)}</td></tr>`).join('')}
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
        <thead><tr><th>Site</th><th class="num">Generated kWh</th><th class="num">tCO₂e</th>
            <th class="num">VCU</th><th class="num">Days</th></tr></thead>
        <tbody>${sites.map((s) => `
            <tr class="clickable" data-site="${esc(s.site_code)}">
                <td><strong>${esc(s.site)}</strong>${s.region ? ` <span class="src-note">${esc(s.region)}</span>` : ''}</td>
                <td class="num">${fmt(s.generation_kwh)}</td>
                <td class="num">${fmt(s.net_reduction_tco2e, 2)}</td>
                <td class="num">${fmt(s.vcu_issued, 2)}</td>
                <td class="num">${fmt(s.days_with_data)}</td></tr>`).join('')}
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
        <p class="sub">Every day ${esc(site)} reported. Click a day to see the spreadsheet
            cells behind it.</p>
        <div class="scroll"><table>
        <thead><tr><th>Date</th><th class="num">kWh</th><th class="num">kWh/kWp</th>
            <th class="num">Devices</th><th>From file</th></tr></thead>
        <tbody>${days.map((d) => `
            <tr class="clickable" data-day="${d.reading_date}">
                <td>${dayName(d.reading_date)}</td>
                <td class="num">${fmt(d.generation_kwh, 1)}</td>
                <td class="num">${d.specific_yield === null ? '' : fmt(d.specific_yield, 2)}</td>
                <td class="num">${d.device_count ? fmt(d.device_count) : ''}</td>
                <td class="src-note">${esc(d.filename || '')}</td>
            </tr>`).join('') || `<tr><td colspan="5" class="empty">No readings for this site.</td></tr>`}
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
            That is what makes this day a gap, nothing was interpolated to fill it.</div>`;
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
                <td class="mono">${esc(r.device_sn || '')}</td>
                <td class="num">${fmt(r.value, 2)} ${esc(r.unit || '')}</td>
                <td class="mono">${esc(r.source_col || '')}${esc(String(r.source_row))}</td>
                <td>#${r.file_id} ${esc(r.filename)}<div class="hash">${esc(r.sha256)}</div></td>
                <td>${r.is_winner ? '<span class="pill ok">used</span>' : '<span class="pill grey">superseded</span>'}</td>
            </tr>`).join('')}
        </tbody></table></div>`;
}

/* --------------------------------------------------------------- The map */

let MAP = null, activeRegion = '';

// Province names as the address column writes them, mapped to the boundary
// file's spelling. Armenian place names transliterate several ways.
const REGION_ALIASES = {
    'erevan': 'Yerevan', 'yerevan': 'Yerevan', 'jerevan': 'Yerevan',
    'vayots dzor': 'Vayots Dzor', "vayots' dzor": 'Vayots Dzor', 'vayotsdzor': 'Vayots Dzor',
    'gegharkunik': 'Gegharkunik', 'geghark\'unik': 'Gegharkunik',
    'aragatsotn': 'Aragatsotn', 'armavir': 'Armavir', 'ararat': 'Ararat',
    'kotayk': 'Kotayk', 'lori': 'Lori', 'shirak': 'Shirak',
    'syunik': 'Syunik', 'syunik\'': 'Syunik', 'tavush': 'Tavush',
};
const canonicalRegion = (name) => REGION_ALIASES[String(name || '').trim().toLowerCase()] || name;

async function drawMap() {
    if (!MAP) {
        try { MAP = await api('/static/armenia.json'); }
        catch { el('map').innerHTML = ''; return; }
    }
    let rows;
    try { rows = await api('/api/regions'); } catch { return; }

    const byProvince = {};
    let max = 0;
    rows.forEach((r) => {
        const key = canonicalRegion(r.region);
        byProvince[key] = (byProvince[key] || 0) + num(r.generation_kwh);
        max = Math.max(max, byProvince[key]);
    });

    // One hue, light to dark, because this is magnitude and not identity.
    const RAMP = ['#e9f1fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95'];
    const shade = (v) => {
        if (!v) return '#f2f2ef';
        const i = Math.min(RAMP.length - 1, Math.max(1, Math.ceil((v / max) * (RAMP.length - 1))));
        return RAMP[i];
    };

    const svg = el('map');
    svg.setAttribute('viewBox', MAP.viewBox);
    svg.innerHTML = Object.entries(MAP.provinces).map(([name, d]) => {
        const v = byProvince[name] || 0;
        const on = activeRegion && canonicalRegion(activeRegion) === name;
        return `<path d="${d}" fill="${shade(v)}"
            stroke="${on ? token('--accent-ink') : '#ffffff'}" stroke-width="${on ? 3 : 1.5}"
            class="province${v ? ' has-data' : ''}" data-region="${esc(name)}"
            ><title>${esc(name)}: ${v ? fmt(v) + ' kWh' : 'no sites'}</title></path>`;
    }).join('');

    svg.querySelectorAll('.province.has-data').forEach((pth) => {
        pth.onclick = () => {
            const name = pth.dataset.region;
            activeRegion = (activeRegion && canonicalRegion(activeRegion) === name) ? '' : name;
            loadEnergy();
        };
    });

    el('mapLegend').innerHTML = `<span class="legend-label">less</span>`
        + RAMP.slice(1).map((c) => `<i style="background:${c}"></i>`).join('')
        + `<span class="legend-label">more energy</span>`;

    el('regionTable').innerHTML = `
        <thead><tr><th>Province</th><th class="num">Generated kWh</th><th class="num">Sites</th>
            <th class="num">kWp</th><th class="num">VCU</th></tr></thead>
        <tbody>${rows.map((r) => `
            <tr class="clickable${activeRegion === r.region ? ' row-on' : ''}" data-region="${esc(r.region)}">
                <td><strong>${esc(r.region)}</strong></td>
                <td class="num">${fmt(r.generation_kwh)}</td>
                <td class="num">${fmt(r.site_count)}</td>
                <td class="num">${r.installed_kwp === null ? '' : fmt(r.installed_kwp, 0)}</td>
                <td class="num">${fmt(r.vcu_issued, 2)}</td>
            </tr>`).join('') || `<tr><td colspan="5" class="empty">No regions yet.</td></tr>`}
        </tbody>`;
    el('regionTable').querySelectorAll('[data-region]').forEach((tr) => {
        tr.onclick = () => {
            activeRegion = activeRegion === tr.dataset.region ? '' : tr.dataset.region;
            loadEnergy();
        };
    });
    el('mapSub').textContent = activeRegion
        ? `Showing ${activeRegion} only. Click it again to see the whole fleet.`
        : 'Every province with sites, shaded by how much they generated. Click one to see only that province.';
}

/* ------------------------------------------------- All data (Gold layer) */

async function loadLedger() {
    busy('ledgerTable', 12);
    let d;
    try { d = await api('/api/ledger' + (ledgerSite ? `?site=${encodeURIComponent(ledgerSite)}` : '')); }
    catch (err) { return failed('ledgerTable', err, 12); }

    const sel = el('ledgerSite');
    if (sel.options.length <= 1) {
        try {
            (await api('/api/fleet')).forEach((s) => sel.add(new Option(s.site, s.site_code)));
        } catch { /* the filter is a convenience */ }
    }

    const cols = [
        ['site', 'Site', 0], ['region', 'Region', 0], ['month', 'Month', 0],
        ['installed_kwp', 'kWp', 1], ['days_with_data', 'Days', 0],
        ['generation_kwh', 'Generated kWh', 1], ['generation_mwh', 'MWh', 3],
        ['emission_factor', 'Factor', 4], ['net_reduction_tco2e', 'tCO₂e', 4],
        ['vcu_issued', 'VCU', 4], ['vcu_price_per_tco2e', 'Price', 2],
        ['total_revenue', 'Revenue', 2],
    ];
    el('ledgerTable').innerHTML = `
        <thead><tr>${cols.map(([k, label, dp]) =>
            `<th class="${dp === 0 ? '' : 'num'}">${label}</th>`).join('')}</tr></thead>
        <tbody>${(d.rows || []).map((r) => `
            <tr>${cols.map(([k, label, dp]) => {
                const v = r[k];
                if (v === null || v === undefined) return '<td class="num"></td>';
                if (dp === 0) return `<td>${k === 'month' ? esc(monthName(v)) : esc(v)}</td>`;
                return `<td class="num">${fmt(v, dp)}</td>`;
            }).join('')}</tr>`).join('')
            || `<tr><td colspan="${cols.length}" class="empty">Nothing loaded yet.</td></tr>`}
        </tbody>`;
}

let ledgerSite = '';
el('ledgerSite').onchange = () => { ledgerSite = el('ledgerSite').value; loadLedger(); };
el('downloadCsv').onclick = () => {
    window.location = '/api/ledger.csv' + (ledgerSite ? `?site=${encodeURIComponent(ledgerSite)}` : '');
};

/* --------------------------------------------- Who has opened the portal */

const shortUA = (ua) => {
    if (!ua) return '';
    const os = /iPhone|iPad/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android'
             : /Macintosh/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows'
             : /Linux/.test(ua) ? 'Linux' : '';
    const br = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome'
             : /Safari\//.test(ua) ? 'Safari' : /Firefox\//.test(ua) ? 'Firefox' : '';
    return [os, br].filter(Boolean).join(' · ');
};
const when = (iso) => iso ? new Date(iso).toLocaleString(undefined,
    { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';

async function loadVisitors() {
    busy('visitorKpis'); busy('linksTable', 5); busy('visitorsTable', 5);
    let d;
    try { d = await api('/api/visitors'); }
    catch (err) {
        failed('visitorKpis', err); failed('linksTable', err, 5);
        return failed('visitorsTable', err, 5);
    }
    if (!d.enabled) {
        el('visitorKpis').innerHTML =
            `<div class="empty">Visit tracking is switched off (TRACK_VISITS=false).</div>`;
        el('linksTable').innerHTML = ''; el('visitorsTable').innerHTML = '';
        return;
    }

    el('visitorKpis').innerHTML = [
        ['People', fmt(d.people), '', 'distinct browsers that opened it'],
        ['Visits', fmt(d.visits), '', 'page opens in total'],
        ['Last 7 days', fmt(d.people_7d), 'people', `${fmt(d.visits_7d)} visits`],
        ['Last opened', when(d.last_seen) || 'never', '', d.first_seen ? `first ${when(d.first_seen)}` : ''],
    ].map(([label, value, unit, foot], i) => `
        <div class="kpi-card" style="--tint:${token(TILE_TINTS[i % TILE_TINTS.length])}">
            <div class="label">${label}</div>
            <div class="value" style="font-size:${String(value).length > 9 ? 17 : 27}px">${esc(value)}<span class="unit">${unit}</span></div>
            <div class="foot">${esc(foot)}</div>
        </div>`).join('');

    el('linksTable').innerHTML = (d.links || []).length ? `
        <thead><tr><th>Shared link</th><th class="num">People</th><th class="num">Opens</th>
            <th>First opened</th><th>Last opened</th></tr></thead>
        <tbody>${d.links.map((l) => `
            <tr><td><strong>${esc(l.tag)}</strong></td>
                <td class="num">${fmt(l.people)}</td>
                <td class="num">${fmt(l.visits)}</td>
                <td>${when(l.first_opened)}</td>
                <td>${when(l.last_opened)}</td></tr>`).join('')}
        </tbody>` : `<tbody><tr><td class="empty">No labelled links opened yet. Send one as
            <span class="mono">?from=their-name</span> and it will appear here.</td></tr></tbody>`;

    el('visitorsTable').innerHTML = `
        <thead><tr><th>Who</th><th class="num">Visits</th><th>First seen</th>
            <th>Last seen</th><th>Device</th><th>Came from</th></tr></thead>
        <tbody>${(d.visitors || []).map((v) => `
            <tr><td>${v.tag ? `<strong>${esc(v.tag)}</strong>` : `<span class="mono">${esc(String(v.visitor_id).slice(0, 8))}</span>`}</td>
                <td class="num">${fmt(v.visits)}</td>
                <td>${when(v.first_seen)}</td>
                <td>${when(v.last_seen)}</td>
                <td>${esc(shortUA(v.user_agent))}</td>
                <td class="src-note">${esc(v.referrer || '')}</td></tr>`).join('')
            || `<tr><td colspan="6" class="empty">Nobody has opened the portal yet.</td></tr>`}
        </tbody>`;
}

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
                ${fact('Sheet', `${esc(r.sheet_name || '')} (header row ${esc(String(r.header_row))})`)}
                ${fact('Period', `${esc(r.period_start || '')} → ${esc(r.period_end || '')}`)}
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
                <td>${esc(f.period_start || '')} → ${esc(f.period_end || '')}</td>
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
