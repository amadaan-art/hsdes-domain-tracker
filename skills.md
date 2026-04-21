# SKILL: HSD-ES Triage Domain Classification

## Description

Performs root-domain classification on a batch of Intel HSD-ES sighting tickets extracted into a JSON file. Acts as a senior Intel platform validation/triage engineer. For each ticket, infers the true problem domain from natural-language content only—never from metadata label fields (such as `domain`, `component`, `sub_component`, `tag`, or similarly labeled fields). Produces strict JSON output with per-ticket analysis, domain clusters, and a statistical summary.

---

## Inputs

| Parameter | Type | Description |
|---|---|---|
| `json_file_path` | string | Absolute path to the extracted HSD JSON file (e.g. `raw_hsdes_data.json`) |

The JSON file is expected to have the following top-level structure:
```json
{
  "data": [ { ...ticket_object... }, ... ],
  "failed": []
}
```

---

## Content Fields Used for Analysis

> **CRITICAL RULE**: Domain classification MUST be derived exclusively from the following three fields. Do NOT use `domain`, `component`, `sub_component`, `tag`, `release`, `platform`, or any other pre-labeled metadata field to assign the probable_domain.

| Field | Description |
|---|---|
| `description` | The primary problem statement written by the submitter. Contains symptom description, initial observations, and repro steps. |
| `comments` | Thread of engineering investigation notes. Contains debug traces, root cause findings, workarounds, fix status, and related ticket references. |
| `sighting_forum_notes` | Async forum discussion notes. Contains additional debug context, hypothesis exchanges, and architectural commentary. |

Secondary fields that may be read for **context only** (not for domain assignment):
- `title` — used to populate `list_title` in output, and as a quick orientation signal
- `id` — ticket identifier
- `sighting_conclusion` — record the value as-is in the summary; do not use to influence domain assignment
- `status` — record for summary statistics only

---

## Analysis Methodology

### Step 1 — Per-Ticket Deep Analysis

For each ticket in `data[]`:

1. Read `description`, `comments`, and `sighting_forum_notes` in full.
2. Extract **important signals**: error codes (MCA values, IERR, CATERR), register names, state machine labels, IP block names, software components, tool names, workaround actions.
3. Infer `probable_domain` from the signals using the Domain Taxonomy below.
4. Identify `secondary_domains` where the issue touches multiple layers.
5. Assign `confidence_level`:
   - `high` — clear root cause or dominant signal cluster points unambiguously to one domain
   - `medium` — signals suggest a domain but investigation is incomplete or split across two domains
   - `low` — minimal content, placeholder ticket, or no investigation notes
6. Write `justification` — 1–3 sentences drawn **exclusively** from evidence in `description`, `comments`, and `sighting_forum_notes`. Quote or closely paraphrase specific signal phrases; do not introduce information from any other field.

### Step 2 — Domain Clustering

Group all tickets by their assigned `probable_domain`. For each cluster:
- List all `ticket_ids`
- Write a `justification` explaining what the tickets in this cluster share
- Note any `ambiguous_tickets` (tickets that could belong to an adjacent cluster)

### Step 3 — Summary Statistics

Compute:
- Total tickets analyzed
- Domain distribution (ticket count per domain)
- List key findings: systemic patterns, highest-frequency root causes, and any hw.bug/hw.arch conclusions

---

## Domain Taxonomy

Use this taxonomy to assign `probable_domain`. The list is not exhaustive—if signals clearly indicate a domain not listed, use a descriptive name and note it.

| Domain | Characteristic Signals |
|---|---|
| `Reset/Power Sequencing` | Reset phase hangs (AWR/WR/CR/AGR/GR), RESETPREP ACK timeouts, HWRS Phase3/Phase4 waits, FEATURE_IP_RESET_COMPLETE not setting, CCF/MMC synchronization failures, CF9 GO response missing, cold/warm/global reset orchestration, SX_DEAD |
| `Reset/RAS` | Machine Check Errors (MCA) occurring **during** a reset flow (not caused by normal operation), IERR/CATERR not propagating during reset, D2D ULA MCA, RASIP MCA, Punit MCA (CPD timeout, PM Agent), YY_IERR_TX silently dropped |
| `Power Management/S5` | S5 entry/exit failures, SLP_EN PM1_CNT SCI_EN masking, S3M GO_S1_TMP premature, IO Trap race, CPD_S1 mid-IO-Trap, PkgC entry, OS-triggered shutdown flow |
| `Crashlog/RAS` | CLA (Crash Log Agent) completion failures, OOB watchdog vs VP race, LTM register population, OOBMSM SRAM table build, MCTP getframe responses, ucode crash frame advancement |
| `PCIe/HIOP/VTC` | PCIETC_TIMEOUT, PCIETC_ERR_NOT_FOUND, cambria queue, ERR_RVD_POISON, PCIe Root Port / Endpoint error, VTC VM stopping, pciextor freeze, PCIe bifurcation, IO resource allocation |
| `Emulation Infrastructure/Simics` | Simics crash (SIGABRT), VP/hybrid mapping timing, GRUB menu script bugs, ACED loop exit count, RTC device model bugs, testbench efficiency degradation (blocking bridge MOP stalls), emulation script configuration errors with no DUT RTL impact |
| `BIOS/Firmware` | SMI handler loops, BIOS MMIO access at 0x0 or reserved addresses, RTC SRAM file mismatch, MSR_BIOS_DONE to disabled cores, BIOS knob configuration, ACPI FADT settings |
| `Software/OS` | OS kernel crashes (NULL ptr deref, page fault, kernel BUG), wrong reset type from OS, AMD/vendor MSR access in power management code, CPUID feature bit misconfiguration in OS |
| `Security/ACM` | ACM authentication failures, fit_processing_failed_acm, FIT profile not loaded, LT shutdown, CPUID/stepping mismatch between model and keys, icecode load IERR |
| `OOB/OOBMSM/S3M` | S3M UR responses on unmapped IO/eSPI addresses, SPI TPM unconnected, fmod/BMC config missing, rdendpointcfg 0x97 unsupported, eSPI IOD decode misconfiguration |
| `Cache Coherency/HIOP` | Protocol violation MCAs (HAMVF, WbMtoIPush), data miscompare on DRd/MRd mixed traffic, wrong address hash fuse configuration, HIOP crossbar errors |
| `Interrupt Delivery` | IRTE delivery mode support gaps, APIC ID decode errors, ExtINT not supported, IDI vs IOMMU HAS discrepancy, interrupt broadcast targeting |
| `Memory/MC` | Memory controller ECC errors, UMM model ECC not supported, e820 table changes between reset cycles, DRAM rule configuration, DIMM training |
| `CXL/Memory` | CXL type2/type3 DRAM rule disabled, persistent memory not created, CXL RP/EP config, fADR support, model version-specific CXL capability gaps |
| `IP Fuse/Configuration` | Wrong fuse values causing IP bring-up failure, shared fuse misconfiguration, core fuse disable not honored, fuse mismatch between UCC flavors |

---

## Output Schema

Return **strict JSON only**. No markdown code fences. No prose before or after the JSON object.

```
{
  "ticket_analysis": [
    {
      "hsd_id": "<string>",
      "list_title": "<string — value of ticket title field>",
      "probable_domain": "<string — from taxonomy>",
      "secondary_domains": ["<string>", ...],
      "confidence_level": "high" | "medium" | "low",
      "justification": "<string — 1-3 sentences citing evidence from description, comments, or sighting_forum_notes only>",
      "important_signals": ["<string>", ...]
    }
  ],
  "domain_clusters": [
    {
      "domain": "<string>",
      "ticket_count": <integer>,
      "ticket_ids": ["<string>", ...],
      "justification": "<string>",
      "ambiguous_tickets": ["<string>", ...]
    }
  ],
  "summary": {
    "total_tickets_analyzed": <integer>,
    "platform": "<string>",
    "release_tag": "<string>",
    "total_domain_clusters": <integer>,
    "domain_distribution_by_ticket_count": { "<domain>": <integer>, ... },
    "key_findings": ["<string>", ...]
  }
}
```

---

## Execution Steps

1. Read `{{json_file_path}}` in chunks if large. Process all items in `data[]` regardless of file size—do not stop early.
2. For each ticket, read `description`, `comments`, and `sighting_forum_notes` completely before classifying.
3. Complete all 70 (or N) ticket classifications before writing output.
4. Build `domain_clusters` by grouping completed `ticket_analysis` entries.
5. Compute `summary` from the full classified set.
6. Write the result to a file named `triage_classification.json` in the same directory as the input file.
7. Output is **strict JSON only**—no markdown fences, no explanatory text before or after.

---

## Quality Rules

- Do NOT anchor classification to the `domain`, `component`, `sub_component`, or `tag` metadata fields.
- A ticket tagged `reset` in metadata must still be classified by content—if signals indicate `Emulation Infrastructure/Simics`, use that.
- `justification` must cite evidence drawn **only** from `description`, `comments`, and `sighting_forum_notes`. Do not reference metadata fields, ticket ID conventions, or external knowledge.
- `important_signals` must be extracted verbatim or near-verbatim from the ticket content—do not fabricate signal names.
- If `description`, `comments`, and `sighting_forum_notes` are all empty or null, assign `confidence_level: low` and note it in `justification`.
- Clone tickets (where `description` references a parent ticket by ID) should still be independently classified based on any additional signals present in their own `comments` or `sighting_forum_notes`.
- `key_findings` in the summary must highlight: dominant domains, systemic root causes affecting multiple tickets, and any hw.bug/hw.arch conclusions (as these have the highest impact on the physical design).
