# hsdes-domain-tracker

**Automated triage and domain classification of Intel HSD-ES sighting tickets.**

This toolchain fetches sighting tickets from HSDES saved queries, extracts raw ticket data via the HSDES REST API, classifies each ticket into a root problem domain using an AI-driven skill prompt, and generates an interactive HTML dashboard for review.

---

## Workflow Overview

```
[HSDES Saved Query]
        │
        ▼  Step 1 – fetch_hsd_ids.py
[hsd_list.txt]  ──platform split──►  [mcp_hsd_list.txt]
                                     [imh1_hsd_list.txt]
                                     [imh2_hsd_list.txt]
                                     [other_hsd_list.txt]
        │
        ▼  Step 2 – hsdes_api_extractor.py
[raw_hsdes_data_<tag>.json]
        │
        ▼  Step 3 – GitHub Copilot + skills.md (AI triage skill)
[triage_classification_<tag>.json]
        │
        ▼  Step 4 – generate_dashboard.py
[triage_dashboard_<tag>.html]
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Intel Kerberos access | Must have a valid `@GAR.CORP.INTEL.COM` principal |
| Python 3.8+ | Available on PATH |
| `curl` with SPNEGO/Kerberos | Used by the API extractor for authenticated requests |
| GitHub Copilot (MCP/Agent mode) | Required for Step 3 – AI triage classification |

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/hsdes-domain-tracker.git
cd hsdes-domain-tracker
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate      # Linux / macOS
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

### Authentication (required before every session)

```bash
kinit <your-idsid>@GAR.CORP.INTEL.COM
klist   # verify ticket is present
```

---

### Step 1 — Fetch HSD IDs from a saved query

```bash
python fetch_hsd_ids.py <query_id>
```

**Example:**

```bash
python fetch_hsd_ids.py 14020705391
```

**Outputs** (written to a `queryId<query_id>/` directory):

| File | Contents |
|---|---|
| `hsd_list.txt` | All ticket IDs + titles (tab-separated) |
| `mcp_hsd_list.txt` | Tickets matching MCP / XOS / CorePMA |
| `cbb_hsd_list.txt` | Tickets matching CBB |
| `ioh_hsd_list.txt` | Tickets matching IOH |
| `imh1_hsd_list.txt` | Tickets matching IMH1 / iMH1 / bare IMH |
| `other_hsd_list.txt` | VTD tickets, enhancements, and unclassified |

Each ticket appears in exactly one list (mutually exclusive, priority-ordered).

---

### Step 2 — Extract raw HSDES data via REST API

```bash
python hsdes_api_extractor.py \
  --ids-file <path/to/mcp_hsd_list.txt> \
  --tag <tag> \
  --workers 3 \
  --delay 0.5
```

**Example:**

```bash
python hsdes_api_extractor.py \
  --ids-file /nfs/site/disks/pse_oks_002/amadaan/HSDES_DomainTrack/queryId14020705391/mcp_hsd_list.txt \
  --tag mcp \
  --workers 3 \
  --delay 0.5
```

**Output:** `raw_hsdes_data_<tag>.json` — contains all ticket fields including `description`, `comments`, and `sighting_forum_notes`.

---

### Step 3 — AI Triage Classification (GitHub Copilot)

Open GitHub Copilot in **agent / MCP mode** and run:

```
Read SKILL.md and apply it to <absolute_path>/raw_hsdes_data_<tag>.json
```

**Example prompt:**

```
Read SKILL.md and apply it to /nfs/site/disks/pse_oks_002/amadaan/HSDES_DomainTrack/raw_hsdes_data_mcp.json
```

Copilot reads `skills.md` for the triage instructions and classifies each ticket by its `description`, `comments`, and `sighting_forum_notes` fields — never by pre-labeled metadata fields.

**Output:** `triage_classification_<tag>.json` — per-ticket domain assignments, domain clusters, and summary statistics.

---

### Step 4 — Generate Interactive HTML Dashboard

```bash
python generate_dashboard.py <path/to/triage_classification_<tag>.json>
```

**Example:**

```bash
python generate_dashboard.py ./triage_classification_mcp.json
```

**Output:** `triage_dashboard_<tag>.html` — self-contained interactive dashboard with:
- Domain distribution charts
- Per-ticket searchable table (domain, confidence, key signals)
- Domain cluster view

---

## Repository Structure

```
hsdes-domain-tracker/
├── fetch_hsd_ids.py          # Step 1 – Fetch IDs from HSDES saved query
├── hsdes_api_extractor.py    # Step 2 – Extract raw ticket data via REST API
├── skills.md                 # Step 3 – AI triage classification skill prompt
├── generate_dashboard.py     # Step 4 – Generate HTML dashboard
├── requirements.txt          # Python dependencies
└── SampleResult_<query_id>/  # Example outputs (committed for reference)
    ├── hsd_list.txt
    ├── mcp_hsd_list.txt
    ├── cbb_hsd_list.txt
    ├── ioh_hsd_list.txt
    ├── imh1_hsd_list.txt
    ├── other_hsd_list.txt
    ├── raw_hsdes_data_output.json
    ├── triage_classification.json
    └── triage_dashboard.html
```

---

## Classification Domains

The skill in `skills.md` maps tickets to the following root domains:

| Domain | Key Signals |
|---|---|
| Reset/Power Sequencing | AWR/WR/CR/AGR/GR hangs, RESETPREP ACK timeouts, CF9 GO |
| Reset/RAS | MCA during reset, IERR/CATERR propagation, D2D ULA MCA |
| Power Management/S5 | S5 entry/exit, SLP_EN, PkgC, OS shutdown flow |
| Crashlog/RAS | CLA completion failures, OOB watchdog, OOBMSM SRAM |
| PCIe/HIOP/VTC | PCIETC_TIMEOUT, cambria queue, PCIe bifurcation |
| Emulation Infrastructure/Simics | Simics SIGABRT, VP/hybrid mapping, GRUB scripts |
| BIOS/Firmware | SMI handler loops, MMIO 0x0 access, MSR_BIOS_DONE |
| Software/OS | Kernel NULL ptr deref, wrong reset type from OS |
| Security/ACM | ACM auth failures, FIT profile, LT shutdown |
| OOB/OOBMSM/S3M | S3M UR responses, eSPI IOD decode, SPI TPM |
| Cache Coherency/HIOP | Protocol violation MCAs, HAMVF, WbMtoIPush |
| Interrupt Delivery | IRTE delivery, APIC ID decode, ExtINT |
| Memory/MC | ECC errors, e820 table, DRAM rule config |
| CXL/Memory | CXL type2/type3, fADR, CXL RP/EP config |
| IP Fuse/Configuration | Wrong fuse values, core fuse disable not honored |

---

## Output File Reference

| File | Generated by | Description |
|---|---|---|
| `hsd_list.txt` | `fetch_hsd_ids.py` | All HSD IDs + titles |
| `mcp_hsd_list.txt` | `fetch_hsd_ids.py` | MCP-platform ticket IDs |
| `raw_hsdes_data_<tag>.json` | `hsdes_api_extractor.py` | Raw HSDES ticket fields |
| `triage_classification_<tag>.json` | Copilot + `skills.md` | AI triage results |
| `triage_dashboard_<tag>.html` | `generate_dashboard.py` | Interactive HTML dashboard |

---

## Notes

- Kerberos tickets expire — re-run `kinit` at the start of each session.
- Runtime output files (JSON, HTML, txt) at repo root are excluded by `.gitignore`. Only `Sample_Result/` outputs are committed.
- The `--workers` and `--delay` flags in Step 2 control concurrency and rate-limiting against the HSDES API.
- Classification in Step 3 is based **exclusively** on `description`, `comments`, and `sighting_forum_notes` — metadata label fields (`domain`, `component`, `tag`) are intentionally ignored per the skill rules.
