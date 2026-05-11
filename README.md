# hsdes-domain-tracker

**Automated triage and domain classification of Intel HSD-ES sighting tickets.**

This toolchain fetches sighting tickets from HSDES saved queries, extracts raw ticket data via the HSDES REST API, classifies each ticket into a root problem domain using an AI-driven skill prompt, and generates an interactive HTML dashboard for review.

All outputs for a given query are stored under a directory named after the query ID (`<query_id>/`).

---

## Workflow Overview

```
[HSDES Saved Query]
        │
        ▼  Step 1 – fetch_hsd_ids.py
<query_id>/hsd_list.txt  ──platform split──►  <query_id>/mcp_hsd_list.txt
                                               <query_id>/cbb_hsd_list.txt
                                               <query_id>/ioh_hsd_list.txt
                                               <query_id>/imh1_hsd_list.txt
                                               <query_id>/other_hsd_list.txt
        │
        ▼  Step 2 – hsdes_api_extractor.py  (runs per non-empty platform list)
<query_id>/raw_hsdes_data_<tag>.json
        │
        ▼  Step 3 – GitHub Copilot + skills.md  (manual, per tag — skippable)
<query_id>/triage_classification_<tag>.json
        │
        ▼  Step 4 – generate_dashboard.py
<query_id>/triage_dashboard_<tag>.html
```

The full pipeline is orchestrated by `run_pipeline.py`.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Intel Kerberos access | Must have a valid `@GAR.CORP.INTEL.COM` principal |
| Python 3.8+ | Available on PATH |
| GitHub Copilot (Agent mode) | Required for Step 3 – AI triage classification |

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
kinit <your-Alias-id>@GAR.CORP.INTEL.COM
klist   # verify ticket is present
```

---

## Recommended: Run the full pipeline

```bash
python run_pipeline.py <query_id>
```

**Example:**

```bash
python run_pipeline.py 14020705391
```

This runs all four steps in order. Step 3 pauses at each platform tag and prompts:

```
  Tag 'ioh' — 12 ticket(s)  [raw_hsdes_data_ioh.json]
  Process tag 'ioh' with Copilot? [y]es / [s]kip / [q]uit :
```

- **y / Enter** — proceeds with the Copilot classification prompt
- **s** — skips this tag and continues to the next
- **q** — exits the pipeline cleanly

**Skip flags** (for resuming a partial run):

```bash
python run_pipeline.py <query_id> --skip-step1            # platform lists already fetched
python run_pipeline.py <query_id> --skip-step1 --skip-step2        # raw JSON already extracted
python run_pipeline.py <query_id> --skip-step1 --skip-step2 --skip-step3  # step 4 only
```

**Additional options:**

| Flag | Default | Description |
|---|---|---|
| `--workers N` | 3 | Parallel API workers for extraction |
| `--delay N` | 0.5 | Seconds between API calls per article |
| `--poll-interval N` | 15 | Seconds between Copilot output polls |
| `--poll-timeout-minutes N` | 60 | Max wait per tag in Step 3 |

All output is written to `<query_id>/` and logged to `<query_id>/pipeline.log`.

---

## Individual Steps

### Step 1 — Fetch HSD IDs from a saved query

```bash
python fetch_hsd_ids.py <query_id>
```

Fetches `id`, `title`, and `type` for every ticket in the saved query, classifies each by platform keyword, and writes the results under `<query_id>/`.

**Classification priority (first match wins):**

| Priority | File | Criteria |
|---|---|---|
| 1 (highest) | `other_hsd_list.txt` | Title contains `VTD` or `DMR`, or `type == enhancement` |
| 2 | `mcp_hsd_list.txt` | Title contains `MCP`, `XOS`, or `CorePMA` |
| 3 | `cbb_hsd_list.txt` | Title contains `CBB` |
| 4 | `ioh_hsd_list.txt` | Title contains `IOH` |
| 5 | `imh1_hsd_list.txt` | Title contains `IMH1`, `iMH1`, or bare `IMH` |
| 6 (catch-all) | `other_hsd_list.txt` | No platform keyword matched |

Each ticket appears in exactly one file.

---

### Step 2 — Extract raw HSDES data via REST API

```bash
python hsdes_api_extractor.py \
  --ids-file <query_id>/mcp_hsd_list.txt \
  --tag mcp \
  --output <query_id>/raw_hsdes_data_mcp.json \
  --workers 3 \
  --delay 0.5
```

**Output:** `<query_id>/raw_hsdes_data_<tag>.json` — all ticket fields including `description`, `comments`, and `sighting_forum_notes`.

When using `run_pipeline.py`, this runs automatically for every non-empty platform list.

---

### Step 3 — AI Triage Classification (GitHub Copilot)

Open GitHub Copilot in **Agent mode** and run the prompt printed by the pipeline:

```
Read skills.md and apply it to <query_id>/raw_hsdes_data_<tag>.json
```

Copilot classifies each ticket using only `description`, `comments`, and `sighting_forum_notes` — never pre-labeled metadata fields.

**Output:** `<query_id>/triage_classification_<tag>.json`

The pipeline polls for this file automatically. It accepts the file regardless of whether Copilot writes it as `triage_classification.json` or `triage_classification_<tag>.json`, in the query dir or the workspace root — whichever appears first.

Tags with an existing `triage_classification_<tag>.json` are skipped automatically on re-runs.

---

### Step 4 — Generate Interactive HTML Dashboard

```bash
python generate_dashboard.py \
  <query_id>/triage_classification_<tag>.json \
  <query_id>/triage_dashboard_<tag>.html
```

**Output:** `<query_id>/triage_dashboard_<tag>.html` — self-contained interactive dashboard with:
- Domain distribution charts
- Per-ticket searchable table (domain, confidence, key signals)
- Domain cluster view

---

## Repository Structure

```
hsdes-domain-tracker/
├── run_pipeline.py           # Full pipeline orchestrator (recommended entry point)
├── fetch_hsd_ids.py          # Step 1 – Fetch IDs + platform split from HSDES query
├── hsdes_api_extractor.py    # Step 2 – Extract raw ticket data via REST API
├── skills.md                 # Step 3 – AI triage classification skill prompt
├── generate_dashboard.py     # Step 4 – Generate HTML dashboard
└── Sample_Result/            # Example outputs (committed for reference)
    └── <query_id>/
        ├── hsd_list.txt
        ├── mcp_hsd_list.txt
        ├── cbb_hsd_list.txt
        ├── ioh_hsd_list.txt
        ├── imh1_hsd_list.txt
        ├── other_hsd_list.txt
        ├── raw_hsdes_data_<tag>.json
        ├── triage_classification_<tag>.json
        └── triage_dashboard_<tag>.html
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
| `<query_id>/hsd_list.txt` | `fetch_hsd_ids.py` | All HSD IDs + titles |
| `<query_id>/*_hsd_list.txt` | `fetch_hsd_ids.py` | Per-platform ticket ID lists |
| `<query_id>/raw_hsdes_data_<tag>.json` | `hsdes_api_extractor.py` | Raw HSDES ticket fields |
| `<query_id>/triage_classification_<tag>.json` | Copilot + `skills.md` | AI triage results |
| `<query_id>/triage_dashboard_<tag>.html` | `generate_dashboard.py` | Interactive HTML dashboard |
| `<query_id>/pipeline.log` | `run_pipeline.py` | Full run log with timestamps |

---

## Notes

- Kerberos tickets expire — re-run `kinit` at the start of each session.
- All runtime outputs are stored under `<query_id>/` — never at the project root.
- The `--workers` and `--delay` flags control concurrency and rate-limiting against the HSDES API.
- Classification in Step 3 is based **exclusively** on `description`, `comments`, and `sighting_forum_notes` — metadata label fields (`domain`, `component`, `tag`) are intentionally ignored per the skill rules.
- Enhancement-type tickets and DMR/VTD titles are routed to `other_hsd_list.txt` and excluded from platform-specific triage by default. Use `--skip-step3` or answer `s` at the Step 3 prompt to skip their classification entirely.
