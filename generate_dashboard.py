#!/usr/bin/env python3
"""
HSD Triage Dashboard Generator

Reads triage_classification.json and produces a self-contained interactive
HTML dashboard with charts, searchable ticket table, and domain cluster view.

Usage:
  python3 generate_dashboard.py                          # default paths
  python3 generate_dashboard.py input.json               # custom input
  python3 generate_dashboard.py input.json output.html   # custom i/o
"""

import json
import sys
import os
from datetime import datetime

# ── Fixed-confidence / alignment badge classes (these are universal) ───────────
CONF_BADGE  = {'high': 'bg-success', 'medium': 'bg-warning text-dark', 'low': 'bg-danger'}
ALIGN_BADGE = {'missing': 'bg-secondary', 'aligned': 'bg-success',
               'partial': 'bg-warning text-dark', 'conflict': 'bg-danger'}

# ── Colour/badge palettes — cycled over whatever domains appear ──────────────
# Bootstrap badge classes for domain labels (cycles for N domains)
_BADGE_CYCLE = [
    'bg-primary', 'bg-warning text-dark', 'bg-info text-dark', 'bg-danger',
    'bg-success',  'bg-secondary',         'bg-dark',           'badge-purple',
    'badge-teal',  'badge-pink',
]
# Chart hex colours (cycles for N domains)
DOMAIN_COLORS = ['#4e79a7','#f28e2b','#e15759','#76b7b2',
                 '#59a14f','#edc948','#b07aa1','#ff9da7','#9c755f','#bab0ac']


def build_domain_badge_map(domains):
    """Return {domain: badge_class} for any list of domain names."""
    return {d: _BADGE_CYCLE[i % len(_BADGE_CYCLE)] for i, d in enumerate(domains)}


# ── Helpers ───────────────────────────────────────────────────────────────────
def esc(s):
    """Minimal HTML attribute escaping."""
    return (str(s)
            .replace('&', '&amp;')
            .replace('"', '&quot;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


def build_ticket_rows(tickets, domain_badge):
    rows = []
    for t in tickets:
        hsd_id    = t.get('hsd_id', '')
        title     = t.get('title') or t.get('list_title', '')
        domain    = t.get('probable_domain', '')
        conf      = t.get('confidence') or t.get('confidence_level', '')
        alignment = t.get('metadata_domain_alignment', '')
        signals   = t.get('key_evidence') or t.get('important_signals', [])

        dom_cls   = domain_badge.get(domain, 'bg-secondary')
        conf_cls  = CONF_BADGE.get(conf,      'bg-secondary')
        align_cls = ALIGN_BADGE.get(alignment, 'bg-secondary')
        chips = ' '.join(
            f'<span class="signal-chip" title="{esc(s)}">'
            f'{esc(s[:50])}{"…" if len(s) > 50 else ""}</span>'
            for s in signals[:3]
        )
        rows.append(
            f'<tr class="ticket-row" '
            f'data-domain="{esc(domain)}" '
            f'data-confidence="{esc(conf)}" '
            f'data-hsdid="{esc(hsd_id)}" '
            f'data-title="{esc(title)}" '
            f'onclick="showModal(\'{hsd_id}\')">'            
            f'<td><code class="hsd-id">{esc(hsd_id)}</code></td>'
            f'<td class="ticket-title" title="{esc(title)}">{esc(title)}</td>'
            f'<td><span class="badge {dom_cls} badge-wrap">{esc(domain)}</span></td>'
            f'<td><span class="badge {conf_cls}">{conf.upper()}</span></td>'
            f'<td><span class="badge {align_cls}">{esc(alignment)}</span></td>'
            f'<td class="signals-cell">{chips}</td>'
            f'</tr>'
        )
    return '\n'.join(rows)


def build_cluster_cards(clusters):
    cards = []
    for cl in clusters:
        domain    = cl.get('domain', '')
        tids      = cl.get('ticket_ids', [])
        tcount    = cl.get('ticket_count', len(tids))
        # accept either field name for the description
        patterns  = cl.get('common_patterns') or cl.get('justification', '')
        chips = ''.join(
            f'<span class="badge bg-primary me-1 mb-1 ticket-chip" '
            f'onclick="event.stopPropagation();filterByDomain(\'{esc(domain)}\');">'            
            f'{tid}</span>'
            for tid in tids
        )
        ambig = cl.get('ambiguous_tickets', [])
        ambig_html = ''
        if ambig:
            a = ''.join(f'<span class="badge bg-warning text-dark me-1">{tid}</span>' for tid in ambig)
            ambig_html = f'<div class="mt-2 small text-muted">Ambiguous: {a}</div>'
        short = patterns[:230] + ('…' if len(patterns) > 230 else '')
        cards.append(
            f'<div class="col-md-6 mb-3">'
            f'<div class="card h-100 cluster-card">'
            f'<div class="card-header d-flex justify-content-between align-items-center">'
            f'<span class="fw-semibold">{esc(domain)}</span>'
            f'<span class="badge bg-primary rounded-pill">{tcount} ticket{"s" if tcount != 1 else ""}</span>'
            f'</div>'
            f'<div class="card-body">'
            f'<p class="small text-muted mb-2">{esc(short)}</p>'
            f'<div class="mb-1"><span class="fw-semibold small">Tickets: </span>{chips}</div>'
            f'{ambig_html}'
            f'</div></div></div>'
        )
    return '\n'.join(cards)


def build_findings_html(findings):
    items = []
    for i, f in enumerate(findings):
        items.append(
            f'<li class="list-group-item finding-item d-flex gap-3">'
            f'<span class="badge bg-dark mt-1 flex-shrink-0">{i+1}</span>'
            f'<span>{esc(f)}</span></li>'
        )
    return '\n'.join(items)


# ── Main generator ────────────────────────────────────────────────────────────
def generate(input_path, output_path):
    with open(input_path) as fh:
        data = json.load(fh)

    summary  = data.get('summary', {})
    tickets  = data.get('ticket_analysis', [])
    clusters = data.get('domain_clusters', [])

    platform    = summary.get('platform',  'Unknown Platform')
    release_tag = summary.get('release_tag', '')
    total       = summary.get('total_tickets_analyzed', len(tickets))
    cluster_cnt = summary.get('total_domain_clusters', len(clusters))

    # ── Build colour/badge maps dynamically from whatever domains exist ──────
    # Accept either key name for domain distribution
    domain_dist = (summary.get('domain_distribution_by_ticket_count')
                   or summary.get('domain_distribution')
                   or {})
    # If the distribution map is missing/empty, compute it from tickets
    if not domain_dist:
        for t in tickets:
            d = t.get('probable_domain', '')
            if d:
                domain_dist[d] = domain_dist.get(d, 0) + 1
    domains = sorted(domain_dist.keys(), key=lambda d: -domain_dist[d])
    domain_badge = build_domain_badge_map(domains)

    domain_counts = [domain_dist[d] for d in domains]

    # ── Confidence breakdown — accept either field name ───────────────────────
    conf = {}
    for t in tickets:
        c = t.get('confidence') or t.get('confidence_level') or 'unknown'
        conf[c] = conf.get(c, 0) + 1

    # ── Domain badge map JS literal for the browser ──────────────────────────
    domain_badge_js = json.dumps(domain_badge)

    domain_options = '\n'.join(f'<option value="{esc(d)}">{esc(d)}</option>' for d in domains)
    generated_at   = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Embed JSON safely inside <script> (prevent </script> from closing tag early)
    data_json = json.dumps(data).replace('</', '<\\/')

    html = HTML_TEMPLATE
    for placeholder, value in {
        '__PLATFORM__':              esc(platform),
        '__RELEASE_TAG__':           esc(release_tag),
        '__TOTAL__':                 str(total),
        '__CLUSTER_COUNT__':         str(cluster_cnt),
        '__HIGH_CONF__':             str(conf.get('high',   0)),
        '__MED_CONF__':              str(conf.get('medium', 0)),
        '__LOW_CONF__':              str(conf.get('low',    0)),
        '__DOMAINS_JSON__':          json.dumps(domains),
        '__DOMAIN_COUNTS_JSON__':    json.dumps(domain_counts),
        '__DOMAIN_COLORS_JSON__':    json.dumps(DOMAIN_COLORS[:len(domains)]),
        '__DOMAIN_BADGE_MAP_JS__':   domain_badge_js,
        '__DATA_JSON__':             data_json,
        '__TICKET_ROWS__':           build_ticket_rows(tickets, domain_badge),
        '__CLUSTER_CARDS__':         build_cluster_cards(clusters),
        '__FINDINGS_HTML__':         build_findings_html(summary.get('key_findings', [])),
        '__DOMAIN_OPTIONS__':        domain_options,
        '__GENERATED_AT__':          generated_at,
    }.items():
        html = html.replace(placeholder, value)

    with open(output_path, 'w') as fh:
        fh.write(html)

    print(f'Dashboard written \u2192 {output_path}')
    print(f'  {total} tickets | {cluster_cnt} clusters | {platform}')


# ── HTML Template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HSD Triage — __PLATFORM__</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --blue:   #4e79a7;
      --orange: #f28e2b;
      --red:    #e15759;
      --green:  #59a14f;
    }
    body { background: #f0f2f5; font-family: 'Segoe UI', system-ui, sans-serif; }

    /* ── Navbar ── */
    .top-bar { background: #1a1d2e; }
    .top-bar .brand { font-size: 1.15rem; font-weight: 700; letter-spacing: -.01em; color: #fff; }
    .top-bar .meta  { color: #9ba3bc; font-size: .8rem; }
    .top-bar .platform-badge {
      background: #2e3347; color: #a0b0ff;
      border-radius: 6px; padding: 3px 10px; font-size: .8rem; font-weight: 600;
    }

    /* ── KPI cards ── */
    .kpi { border: none; border-radius: 10px; }
    .kpi .accent { width: 4px; border-radius: 2px; min-height: 48px; }
    .kpi-val { font-size: 2.4rem; font-weight: 800; line-height: 1; }
    .kpi-lbl { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; color: #6c757d; margin-top: 4px; }

    /* ── Charts ── */
    .chart-card { border: none; border-radius: 10px; }
    .chart-card .card-header { background: #fff; border-bottom: 1px solid #e9ecef;
      font-weight: 600; font-size: .9rem; border-radius: 10px 10px 0 0 !important; }
    .chart-wrap { position: relative; }

    /* ── Section headings ── */
    .section-title { font-weight: 700; font-size: 1rem; letter-spacing: -.01em;
      border-left: 4px solid var(--blue); padding-left: 10px; margin-bottom: 1.25rem; }

    /* ── Findings ── */
    .finding-item { border-left: none; border-right: none; line-height: 1.65; font-size: .88rem; }

    /* ── Cluster cards ── */
    .cluster-card { border: none; border-radius: 10px; transition: box-shadow .2s; }
    .cluster-card:hover { box-shadow: 0 6px 20px rgba(0,0,0,.12); }
    .cluster-card .card-header { background: #f8f9fb; font-size: .88rem;
      border-radius: 10px 10px 0 0 !important; }
    .ticket-chip { cursor: pointer; font-size: .75rem; }
    .ticket-chip:hover { opacity: .8; }

    /* ── Ticket table ── */
    .table-card { border: none; border-radius: 10px; overflow: hidden; }
    .table-card .card-header { background: #fff; border-bottom: 1px solid #e9ecef; }
    table thead th { background: #1a1d2e; color: #c8cde6; font-size: .8rem;
      font-weight: 600; letter-spacing: .04em; white-space: nowrap;
      cursor: pointer; user-select: none; }
    table thead th:hover { background: #252840; }
    table thead th .sort-icon { opacity: .4; font-size: .7rem; margin-left: 4px; }
    table thead th.sort-asc  .sort-icon { opacity: 1; }
    table thead th.sort-desc .sort-icon { opacity: 1; }
    .ticket-row { cursor: pointer; font-size: .83rem; }
    .ticket-row:hover { background: #eef2ff !important; }
    .ticket-title { max-width: 300px; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; }
    .hsd-id { color: var(--blue); font-weight: 700; font-size: .85rem; }
    .badge-wrap { white-space: normal; font-size: .72rem; line-height: 1.3; }
    .signal-chip { display: inline-block; background: #eef0f3; border-radius: 4px;
      padding: 1px 7px; font-size: .7rem; color: #4a5568; margin: 1px;
      max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      vertical-align: middle; }
    .signals-cell { max-width: 300px; }

    /* ── Modal ── */
    .modal-hsd { font-size: 1.4rem; font-weight: 800; color: var(--blue); font-family: monospace; }
    .signal-pill { display: inline-block; background: #e7f0fd; border: 1px solid #b6d0f7;
      color: #1a4480; border-radius: 20px; padding: 3px 11px; font-size: .75rem; margin: 2px; }

    /* ── Progress bars (open/closed) ── */
    .prog-bar { height: 8px; border-radius: 4px; }

    /* ── Footer ── */
    .footer { background: #1a1d2e; color: #6c757d; font-size: .78rem; padding: 1rem 0; margin-top: 3rem; }

    /* ── Extra badge colours for auto-assigned domains ── */
    .badge-purple { background-color: #6f42c1 !important; color: #fff; }
    .badge-teal   { background-color: #20c997 !important; color: #000; }
    .badge-pink   { background-color: #d63384 !important; color: #fff; }

    /* ── Misc ── */
    .no-results { display: none; }
  </style>
</head>
<body>

<!-- ════════════════════ TOP BAR ════════════════════ -->
<div class="top-bar px-4 py-3 d-flex justify-content-between align-items-center">
  <div class="brand">🔍 HSD Triage Dashboard</div>
  <div class="text-end">
    <div class="platform-badge mb-1">__PLATFORM__</div>
    <div class="meta">__RELEASE_TAG__ &nbsp;·&nbsp; Generated __GENERATED_AT__</div>
  </div>
</div>

<div class="container-fluid px-4 py-4">

  <!-- ════════ KPI ROW ════════ -->
  <div class="row g-3 mb-4">

    <div class="col-6 col-lg-3">
      <div class="card kpi shadow-sm h-100">
        <div class="card-body d-flex gap-3 align-items-center">
          <div class="accent bg-primary"></div>
          <div>
            <div class="kpi-val text-primary">__TOTAL__</div>
            <div class="kpi-lbl">Total Tickets</div>
          </div>
        </div>
      </div>
    </div>

    <div class="col-6 col-lg-3">
      <div class="card kpi shadow-sm h-100">
        <div class="card-body d-flex gap-3 align-items-center">
          <div class="accent bg-success"></div>
          <div>
            <div class="kpi-val text-success">__CLUSTER_COUNT__</div>
            <div class="kpi-lbl">Domain Clusters</div>
          </div>
        </div>
      </div>
    </div>

    <div class="col-6 col-lg-3">
      <div class="card kpi shadow-sm h-100">
        <div class="card-body d-flex gap-3 align-items-center">
          <div class="accent bg-danger"></div>
          <div class="w-100">
            <div class="d-flex justify-content-between align-items-baseline mb-1">
              <span class="kpi-val text-success" style="font-size:1.6rem">__HIGH_CONF__</span>
              <span class="kpi-val text-warning" style="font-size:1.6rem">__MED_CONF__</span>
              <span class="kpi-val text-danger"  style="font-size:1.6rem">__LOW_CONF__</span>
            </div>
            <div class="d-flex justify-content-between">
              <span class="kpi-lbl">High</span>
              <span class="kpi-lbl">Med</span>
              <span class="kpi-lbl">Low</span>
            </div>
            <div class="kpi-lbl text-center mt-1">Confidence</div>
          </div>
        </div>
      </div>
    </div>

  </div><!-- /KPI row -->

  <!-- ════════ CHARTS ROW ════════ -->
  <div class="row g-3 mb-4">

    <!-- Domain donut -->
    <div class="col-md-6">
      <div class="card chart-card shadow-sm h-100">
        <div class="card-header">Domain Distribution</div>
        <div class="card-body d-flex align-items-center justify-content-center">
          <div class="chart-wrap w-100" style="height:270px">
            <canvas id="domainChart"></canvas>
          </div>
        </div>
      </div>
    </div>

    <!-- Confidence -->
    <div class="col-md-6">
      <div class="card chart-card shadow-sm h-100">
        <div class="card-header">Confidence Level</div>
        <div class="card-body d-flex align-items-center justify-content-center">
          <div class="chart-wrap w-100" style="height:270px">
            <canvas id="confidenceChart"></canvas>
          </div>
        </div>
      </div>
    </div>

  </div><!-- /charts row -->

  <!-- ════════ KEY FINDINGS ════════ -->
  <div class="row mb-4">
    <div class="col-12">
      <div class="section-title">Key Findings</div>
      <div class="card shadow-sm" style="border:none;border-radius:10px">
        <ul class="list-group list-group-flush" style="border-radius:10px">
          __FINDINGS_HTML__
        </ul>
      </div>
    </div>
  </div>

  <!-- ════════ DOMAIN CLUSTERS ════════ -->
  <div class="row mb-4">
    <div class="col-12">
      <div class="section-title">Domain Clusters</div>
      <div class="row g-3">
        __CLUSTER_CARDS__
      </div>
    </div>
  </div>

  <!-- ════════ TICKET TABLE ════════ -->
  <div class="row mb-4">
    <div class="col-12">
      <div class="section-title">Ticket Analysis</div>
      <div class="card table-card shadow-sm">
        <div class="card-header d-flex flex-wrap gap-2 align-items-center py-2">
          <input id="searchBox" type="search" class="form-control form-control-sm"
            style="max-width:260px" placeholder="🔍  Search ID or title…" oninput="applyFilters()">
          <select id="domainFilter" class="form-select form-select-sm" style="max-width:220px"
            onchange="applyFilters()">
            <option value="">All Domains</option>
            __DOMAIN_OPTIONS__
          </select>
          <select id="confFilter" class="form-select form-select-sm" style="max-width:140px"
            onchange="applyFilters()">
            <option value="">All Confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <button class="btn btn-sm btn-outline-secondary" onclick="clearFilters()">Clear</button>
          <span id="rowCount" class="ms-auto text-muted small"></span>
        </div>
        <div class="table-responsive">
          <table class="table table-hover mb-0" id="ticketTable">
            <thead>
              <tr>
                <th onclick="sortTable(0)">HSD ID <span class="sort-icon">⇅</span></th>
                <th onclick="sortTable(1)">Title <span class="sort-icon">⇅</span></th>
                <th onclick="sortTable(2)">Domain <span class="sort-icon">⇅</span></th>
                <th onclick="sortTable(3)">Confidence <span class="sort-icon">⇅</span></th>
                <th>Key Signals</th>
              </tr>
            </thead>
            <tbody id="tableBody">
              __TICKET_ROWS__
            </tbody>
          </table>
          <div id="noResults" class="text-center text-muted py-4 no-results">No tickets match the current filters.</div>
        </div>
      </div>
    </div>
  </div>

</div><!-- /container -->

<!-- ════════ DETAIL MODAL ════════ -->
<div class="modal fade" id="detailModal" tabindex="-1">
  <div class="modal-dialog modal-lg modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <div>
          <div id="m-hsd-id" class="modal-hsd"></div>
          <div id="m-title" class="text-muted small mt-1" style="max-width:580px"></div>
        </div>
        <button type="button" class="btn-close ms-3" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="d-flex flex-wrap gap-3 mb-4">
          <div>
            <div class="text-muted small mb-1">Domain</div>
            <span id="m-domain" class="badge fs-6"></span>
          </div>
          <div>
            <div class="text-muted small mb-1">Confidence</div>
            <span id="m-confidence" class="badge fs-6"></span>
          </div>
        </div>

        <h6 class="fw-bold">Justification</h6>
        <p id="m-justification" class="text-muted mb-4" style="line-height:1.7"></p>

        <h6 class="fw-bold">Important Signals</h6>
        <div id="m-signals" class="mb-4"></div>

        <div id="m-secondary-section" style="display:none">
          <h6 class="fw-bold">Secondary Domains</h6>
          <div id="m-secondary" class="mb-2"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ════════ FOOTER ════════ -->
<div class="footer text-center">
  HSD Triage Dashboard &nbsp;·&nbsp; __PLATFORM__ / __RELEASE_TAG__ &nbsp;·&nbsp; Generated __GENERATED_AT__
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
/* ── Embedded data ── */
const DATA = __DATA_JSON__;

/* ── Lookup maps — auto-generated from data, work for any domain set ── */
const DOMAIN_BADGE = __DOMAIN_BADGE_MAP_JS__;
const CONF_BADGE = {high:'bg-success', medium:'bg-warning text-dark', low:'bg-danger'};

const ticketMap = {};
DATA.ticket_analysis.forEach(t => { ticketMap[t.hsd_id] = t; });

/* ════════ CHARTS ════════ */

/* Domain donut */
new Chart(document.getElementById('domainChart'), {
  type: 'doughnut',
  data: {
    labels:   __DOMAINS_JSON__,
    datasets: [{ data: __DOMAIN_COUNTS_JSON__, backgroundColor: __DOMAIN_COLORS_JSON__,
                 borderWidth: 2, borderColor: '#fff', hoverOffset: 6 }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { position: 'bottom', labels: { font: {size:11}, boxWidth:12, padding:10 } },
      tooltip: { callbacks: {
        label: ctx => ` ${ctx.label}: ${ctx.parsed} ticket${ctx.parsed!==1?'s':''}`
      }}
    }
  }
});

/* Confidence bar */
new Chart(document.getElementById('confidenceChart'), {
  type: 'bar',
  data: {
    labels: ['High','Medium','Low'],
    datasets: [{ data:[__HIGH_CONF__,__MED_CONF__,__LOW_CONF__],
                 backgroundColor:['#28a745','#ffc107','#dc3545'], borderRadius:4 }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend:{display:false} },
    scales: {
      x: { ticks:{font:{size:10}} },
      y: { beginAtZero:true, ticks:{stepSize:1, font:{size:10}} }
    }
  }
});

/* ════════ TABLE FILTERING ════════ */
function applyFilters() {
  const search = document.getElementById('searchBox').value.toLowerCase();
  const domain = document.getElementById('domainFilter').value;
  const conf   = document.getElementById('confFilter').value;
  const rows   = document.querySelectorAll('#tableBody .ticket-row');
  let visible  = 0;
  rows.forEach(r => {
    const ok = (!search || r.dataset.hsdid.includes(search) || r.dataset.title.toLowerCase().includes(search))
            && (!domain || r.dataset.domain === domain)
            && (!conf   || r.dataset.confidence === conf);
    r.style.display = ok ? '' : 'none';
    if (ok) visible++;
  });
  document.getElementById('rowCount').textContent = `Showing ${visible} of ${rows.length}`;
  document.getElementById('noResults').style.display = visible === 0 ? 'block' : 'none';
}

function clearFilters() {
  document.getElementById('searchBox').value = '';
  document.getElementById('domainFilter').value = '';
  document.getElementById('confFilter').value = '';
  applyFilters();
}

function filterByDomain(domain) {
  document.getElementById('domainFilter').value = domain;
  applyFilters();
  document.getElementById('ticketTable').scrollIntoView({behavior:'smooth', block:'start'});
}

/* ════════ TABLE SORTING ════════ */
let sortCol = -1, sortDir = 1;
function sortTable(col) {
  const headers = document.querySelectorAll('#ticketTable thead th');
  headers.forEach((h,i) => {
    h.classList.remove('sort-asc','sort-desc');
    h.querySelector('.sort-icon').textContent = '⇅';
  });
  if (sortCol === col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
  headers[col].classList.add(sortDir===1 ? 'sort-asc' : 'sort-desc');
  headers[col].querySelector('.sort-icon').textContent = sortDir===1 ? '↑' : '↓';

  const tbody = document.getElementById('tableBody');
  const rows  = Array.from(tbody.querySelectorAll('.ticket-row'));
  rows.sort((a,b) => {
    const ta = a.cells[col].textContent.trim().toLowerCase();
    const tb = b.cells[col].textContent.trim().toLowerCase();
    return ta < tb ? -sortDir : ta > tb ? sortDir : 0;
  });
  rows.forEach(r => tbody.appendChild(r));
}

/* ════════ TICKET DETAIL MODAL ════════ */
const modalInstance = new bootstrap.Modal(document.getElementById('detailModal'));

function showModal(hsdId) {
  const t = ticketMap[hsdId];
  if (!t) return;
  document.getElementById('m-hsd-id').textContent = t.hsd_id;
  document.getElementById('m-title').textContent  = t.title || t.list_title || '';

  const domEl  = document.getElementById('m-domain');
  domEl.textContent  = t.probable_domain;
  domEl.className    = 'badge fs-6 ' + (DOMAIN_BADGE[t.probable_domain] || 'bg-secondary');

  const conf_val = (t.confidence || t.confidence_level || 'unknown');
  const confEl = document.getElementById('m-confidence');
  confEl.textContent = conf_val.toUpperCase();
  confEl.className   = 'badge fs-6 ' + (CONF_BADGE[conf_val] || 'bg-secondary');

  // justification can live under 'justification' or be synthesised from key_evidence
  const justText = t.justification || (t.key_evidence ? t.key_evidence.join(' · ') : '');
  document.getElementById('m-justification').textContent = justText;
  const signals = t.important_signals || t.key_evidence || [];
  document.getElementById('m-signals').innerHTML =
    signals.map(s => `<span class="signal-pill">${s}</span>`).join('');

  const secSec = document.getElementById('m-secondary-section');
  if (t.secondary_domains && t.secondary_domains.length > 0) {
    secSec.style.display = '';
    document.getElementById('m-secondary').innerHTML =
      t.secondary_domains.map(d => `<span class="badge bg-secondary me-1">${d}</span>`).join('');
  } else {
    secSec.style.display = 'none';
  }
  modalInstance.show();
}

/* ── Init ── */
applyFilters();
</script>
</body>
</html>'''


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    input_path   = sys.argv[1] if len(sys.argv) > 1 else os.path.join(script_dir, 'triage_classification.json')
    output_path  = sys.argv[2] if len(sys.argv) > 2 else os.path.join(script_dir, 'triage_dashboard.html')
    generate(input_path, output_path)
