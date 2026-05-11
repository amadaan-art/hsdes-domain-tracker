#!/usr/bin/env python3
"""
run_pipeline.py — End-to-end HSD query triage pipeline orchestrator

Serial workflow:
  Step 1  fetch_hsd_ids.py          → <query_id>/[platform]_hsd_list.txt
  Step 2  hsdes_api_extractor.py    → <query_id>/raw_hsdes_data_<tag>.json  (per platform)
  Step 3  GitHub Copilot (manual)   → <query_id>/triage_classification_<tag>.json
  Step 4  generate_dashboard.py     → <query_id>/triage_dashboard_<tag>.html

Step 3 cannot be automated — the pipeline pauses, prints the exact Copilot
prompt for each tag, polls until the output file appears, renames it with the
tag suffix, then continues.

Usage:
  python run_pipeline.py <query_id>
  python run_pipeline.py <query_id> --workers 3 --delay 0.5
  python run_pipeline.py <query_id> --skip-step1            # skip fetch (dir already exists)
  python run_pipeline.py <query_id> --skip-step2            # skip extraction (raw JSON exists)
  python run_pipeline.py <query_id> --skip-step3            # skip Copilot (triage JSON exists)
  python run_pipeline.py <query_id> --skip-step1 --skip-step2 --skip-step3   # step 4 only
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Platform lists produced by fetch_hsd_ids.py (in priority order) ──────────
PLATFORM_FILES = [
    ("mcp",   "mcp_hsd_list.txt"),
    ("cbb",   "cbb_hsd_list.txt"),
    ("ioh",   "ioh_hsd_list.txt"),
    ("imh1",  "imh1_hsd_list.txt"),
    ("other", "other_hsd_list.txt"),
]

SCRIPT_DIR = Path(__file__).parent.resolve()


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess runner
# ─────────────────────────────────────────────────────────────────────────────

def run_step(cmd: list, log: logging.Logger, label: str) -> None:
    """
    Run a subprocess, stream each output line to the logger, and raise
    RuntimeError on non-zero exit.  All output goes to the log file; lines
    containing recognised keywords are also echoed to the console.
    """
    log.info("  CMD: %s", " ".join(str(c) for c in cmd))
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    echo_keywords = ("[INFO]", "[WARNING]", "[ERROR]", "Done.", "written →",
                     "found=", "failed=", "Dashboard written")
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        log.debug("    %s", line)
        if any(kw in line for kw in echo_keywords):
            log.info("    %s", line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{label} exited with code {proc.returncode}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def check_kerberos(log: logging.Logger) -> None:
    """Warn if no valid Kerberos ticket is present (non-fatal)."""
    result = subprocess.run(["klist", "-s"], capture_output=True)
    if result.returncode != 0:
        log.warning("No valid Kerberos ticket detected.")
        log.warning("  Run: kinit <alias>@GAR.CORP.INTEL.COM")
    else:
        log.info("Kerberos ticket is valid.")


def count_ids(path: Path) -> int:
    """Return number of non-empty, non-comment lines in a platform list file."""
    if not path.exists():
        return 0
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Manual Copilot checkpoint
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_copilot(
    tag: str,
    raw_json: Path,
    out_dir: Path,
    poll_interval: int,
    poll_timeout_min: int,
    log: logging.Logger,
) -> Path:
    """
    Print the Copilot prompt for one tag, then poll all candidate output
    locations until a valid triage_classification JSON file appears.

    Copilot may write the output in any of these locations / names:
      1. <out_dir>/triage_classification.json          (skills.md fixed name)
      2. <out_dir>/triage_classification_<tag>.json    (with tag suffix)
      3. <SCRIPT_DIR>/triage_classification.json       (workspace root)
      4. <SCRIPT_DIR>/triage_classification_<tag>.json (workspace root + tag)

    All candidates are collected via glob every poll cycle and validated.
    The first valid JSON found is moved to <out_dir>/triage_classification_<tag>.json.
    """
    final_path = out_dir / f"triage_classification_{tag}.json"

    # Already done in a previous run
    if final_path.exists():
        log.info("    [SKIP] %s already exists — skipping Copilot step for '%s'.",
                 final_path.name, tag)
        return final_path

    # Clean up any stale untagged file from a prior tag's run
    for stale in [out_dir / "triage_classification.json",
                  SCRIPT_DIR / "triage_classification.json"]:
        if stale.exists():
            log.warning("    Removing stale %s before waiting for tag '%s'.",
                        stale, tag)
            stale.unlink()

    # All directories Copilot might write into
    search_dirs = list(dict.fromkeys([out_dir, SCRIPT_DIR]))  # dedup, order preserved

    separator = "=" * 72
    print()
    print(separator)
    print(f"  STEP 3 — MANUAL ACTION REQUIRED  (tag: {tag})")
    print(separator)
    print()
    print("  Open GitHub Copilot in Agent / MCP mode and run this prompt:")
    print()
    print(f"      Read skills.md and apply it to {raw_json.resolve()}")
    print()
    print("  Copilot should write triage_classification.json next to the input file.")
    print(f"  Pipeline will move/rename it to: {final_path}")
    print()
    print(f"  Scanning locations every {poll_interval}s  |  timeout: {poll_timeout_min} min")
    print("  Watched locations:")
    for d in search_dirs:
        print(f"    {d}/triage_classification*.json")
    print(separator)
    print()

    def _valid_json(path: Path) -> bool:
        """Return True if path is non-empty valid JSON."""
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            with open(path, encoding="utf-8") as fh:
                json.load(fh)
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def _candidate_paths() -> list:
        """
        Return only the specific candidate paths for THIS tag.
        Never touch files belonging to other tags.
        Candidates (checked in priority order):
          1. <out_dir>/triage_classification_<tag>.json   (final destination)
          2. <out_dir>/triage_classification.json          (untagged, fixed name)
          3. <SCRIPT_DIR>/triage_classification_<tag>.json (workspace root + tag)
          4. <SCRIPT_DIR>/triage_classification.json       (workspace root, untagged)
        """
        candidates = []
        for d in search_dirs:
            tagged   = d / f"triage_classification_{tag}.json"
            untagged = d / "triage_classification.json"
            if tagged != final_path and tagged.exists():
                candidates.append(tagged)
            if untagged.exists():
                candidates.append(untagged)
        return candidates

    deadline = time.time() + poll_timeout_min * 60
    while time.time() < deadline:
        # First: check if final destination already exists (written directly by Copilot)
        if _valid_json(final_path):
            log.info("    Output at destination → %s", final_path.name)
            return final_path

        candidates = _candidate_paths()
        if candidates:
            log.debug("    Candidates found: %s", [str(p) for p in candidates])

        for cand in candidates:
            if not _valid_json(cand):
                log.info("    %s exists but not yet valid JSON — waiting ...", cand.name)
                continue
            try:
                shutil.move(str(cand), str(final_path))
                log.info("    Output detected (%s) → moved to %s", cand.name, final_path.name)
            except OSError as exc:
                log.error("    Failed to move %s → %s: %s", cand, final_path, exc)
                raise RuntimeError(str(exc)) from exc
            return final_path

        mins_left = max(0, int((deadline - time.time()) / 60))
        log.info("    Waiting for Copilot output [%s] ... (%d min remaining)",
                 tag, mins_left)
        time.sleep(poll_interval)

    # Report exactly what was and wasn't found to aid diagnosis
    candidates = _candidate_paths()
    raise RuntimeError(
        f"Timed out after {poll_timeout_min} min waiting for Copilot output "
        f"for tag '{tag}'.\n"
        f"  Expected: {final_path}\n"
        f"  Files found matching triage_classification*.json: "
        f"{[str(p) for p in candidates] or 'none'}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HSD triage pipeline — runs Steps 1-4 serially for a given query ID.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query_id",
                        help="HSDES saved query ID (e.g. 14025227120)")
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel API workers for hsdes_api_extractor (default: 3)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Per-article API delay in seconds (default: 0.5)")
    parser.add_argument("--poll-interval", type=int, default=15,
                        help="Seconds between Copilot output polls (default: 15)")
    parser.add_argument("--poll-timeout-minutes", type=int, default=60,
                        help="Max minutes to wait for Copilot per tag (default: 60)")
    parser.add_argument("--skip-step1", action="store_true",
                        help="Skip Step 1 — assumes <query_id>/ already populated")
    parser.add_argument("--skip-step2", action="store_true",
                        help="Skip Step 2 — assumes raw_hsdes_data_*.json already present")
    parser.add_argument("--skip-step3", action="store_true",
                        help="Skip Step 3 — assumes triage_classification_<tag>.json present")
    args = parser.parse_args()

    query_id = str(args.query_id)

    # Always run relative to the project directory so fetch_hsd_ids.py creates
    # the <query_id>/ dir in the right place (it uses a relative path internally).
    os.chdir(SCRIPT_DIR)

    out_dir = SCRIPT_DIR / query_id
    out_dir.mkdir(exist_ok=True)

    log = setup_logging(out_dir / "pipeline.log")

    log.info("=" * 60)
    log.info("HSD Triage Pipeline  |  query_id=%s", query_id)
    log.info("Project dir : %s", SCRIPT_DIR)
    log.info("Output dir  : %s/", out_dir)
    log.info("=" * 60)

    check_kerberos(log)

    # ── STEP 1 — Fetch HSD IDs ────────────────────────────────────────────────
    if args.skip_step1:
        log.info("[Step 1] SKIPPED (--skip-step1)")
    else:
        log.info("[Step 1] Fetching HSD IDs for query %s ...", query_id)
        try:
            run_step(
                [sys.executable, SCRIPT_DIR / "fetch_hsd_ids.py", query_id],
                log, "Step 1",
            )
        except RuntimeError as exc:
            log.error("[Step 1] FAILED: %s", exc)
            sys.exit(1)

        master = out_dir / "hsd_list.txt"
        total  = count_ids(master)
        if total == 0:
            log.error("[Step 1] FAILED: %s is empty or missing.", master)
            sys.exit(1)
        log.info("[Step 1] DONE — %d total IDs written to %s/", total, query_id)

    # ── STEP 2 — Extract raw HSDES data (per non-empty platform list) ─────────
    if args.skip_step2:
        log.info("[Step 2] SKIPPED (--skip-step2)")
        # Discover tags from any raw JSON files already present
        existing = sorted(out_dir.glob("raw_hsdes_data_*.json"))
        tags_and_raws = [
            (p.stem.replace("raw_hsdes_data_", ""), p) for p in existing
        ]
        if not tags_and_raws:
            log.error(
                "[Step 2] --skip-step2 used but no raw_hsdes_data_*.json found in %s/",
                query_id,
            )
            sys.exit(1)
        log.info("[Step 2] Using %d existing raw file(s): %s",
                 len(tags_and_raws), [t for t, _ in tags_and_raws])
    else:
        log.info("[Step 2] Extracting raw HSDES data for all non-empty platform lists ...")
        tags_and_raws = []

        for tag, filename in PLATFORM_FILES:
            ids_file = out_dir / filename
            n = count_ids(ids_file)
            if n == 0:
                log.info("  [%-5s] SKIPPED — %s is empty or missing.", tag, filename)
                continue

            raw_output = out_dir / f"raw_hsdes_data_{tag}.json"
            log.info("  [%-5s] %d IDs → running extractor ...", tag, n)
            try:
                run_step(
                    [
                        sys.executable, SCRIPT_DIR / "hsdes_api_extractor.py",
                        "--ids-file", str(ids_file),
                        "--tag",      tag,
                        "--output",   str(raw_output),
                        "--workers",  str(args.workers),
                        "--delay",    str(args.delay),
                    ],
                    log, f"Step 2 [{tag}]",
                )
            except RuntimeError as exc:
                log.error("  [%-5s] FAILED: %s — stopping pipeline.", tag, exc)
                sys.exit(1)

            if not raw_output.exists():
                log.error("  [%-5s] FAILED: %s not produced.", tag, raw_output.name)
                sys.exit(1)

            log.info("  [%-5s] DONE → %s", tag, raw_output.name)
            tags_and_raws.append((tag, raw_output))

        if not tags_and_raws:
            log.error("[Step 2] All platform lists are empty — nothing to extract.")
            sys.exit(1)

        log.info("[Step 2] DONE — %d platform(s): %s",
                 len(tags_and_raws), [t for t, _ in tags_and_raws])

    # ── STEP 3 — GitHub Copilot classification (manual, per tag) ─────────────
    triage_jsons = []

    if args.skip_step3:
        log.info("[Step 3] SKIPPED (--skip-step3)")
        for tag, _ in tags_and_raws:
            p = out_dir / f"triage_classification_{tag}.json"
            if not p.exists():
                log.error("  [%-5s] --skip-step3 used but %s not found.", tag, p.name)
                sys.exit(1)
            triage_jsons.append((tag, p))
            log.info("  [%-5s] Using %s", tag, p.name)
    else:
        log.info("[Step 3] %d tag(s) require Copilot classification.",
                 len(tags_and_raws))
        for tag, raw_json in tags_and_raws:
            # Skip tags whose triage file already exists (prior run)
            final_path = out_dir / f"triage_classification_{tag}.json"
            if not final_path.exists():
                # Ask the user whether to process this tag
                print()
                ids_file = out_dir / f"{tag}_hsd_list.txt"
                n_tickets = count_ids(ids_file)
                print(f"  Tag '{tag}' — {n_tickets} ticket(s)  [{raw_json.name}]")
                while True:
                    answer = input(f"  Process tag '{tag}' with Copilot? [y]es / [s]kip / [q]uit : ").strip().lower()
                    if answer in ("y", "yes", ""):
                        break
                    if answer in ("s", "skip", "n", "no"):
                        log.info("[Step 3][%-5s] SKIPPED by user.", tag)
                        break
                    if answer in ("q", "quit"):
                        log.info("[Step 3] Aborted by user.")
                        sys.exit(0)
                    print("  Please enter y, s, or q.")
                if answer in ("s", "skip", "n", "no"):
                    continue

            try:
                result = wait_for_copilot(
                    tag, raw_json, out_dir,
                    args.poll_interval, args.poll_timeout_minutes, log,
                )
                triage_jsons.append((tag, result))
                log.info("[Step 3][%-5s] DONE → %s", tag, result.name)
            except RuntimeError as exc:
                log.error("[Step 3][%-5s] FAILED: %s — stopping pipeline.", tag, exc)
                sys.exit(1)

    # ── STEP 4 — Generate dashboards (per tag) ────────────────────────────────
    log.info("[Step 4] Generating dashboards for %d tag(s) ...", len(triage_jsons))
    dashboards = []

    for tag, triage_json in triage_jsons:
        dashboard = out_dir / f"triage_dashboard_{tag}.html"
        log.info("  [%-5s] Generating dashboard ...", tag)
        try:
            run_step(
                [
                    sys.executable, SCRIPT_DIR / "generate_dashboard.py",
                    str(triage_json),
                    str(dashboard),
                ],
                log, f"Step 4 [{tag}]",
            )
        except RuntimeError as exc:
            log.error("  [%-5s] FAILED: %s — stopping pipeline.", tag, exc)
            sys.exit(1)

        if not dashboard.exists():
            log.error("  [%-5s] FAILED: %s not produced.", tag, dashboard.name)
            sys.exit(1)

        log.info("  [%-5s] DONE → %s", tag, dashboard.name)
        dashboards.append((tag, dashboard))

    # ── Final summary ─────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE — query_id=%s  (%d tag(s))",
             query_id, len(dashboards))
    log.info("=" * 60)
    log.info("")
    log.info("  %-6s  %-42s  %-42s", "Tag", "Triage JSON", "Dashboard HTML")
    log.info("  %-6s  %-42s  %-42s", "─" * 6, "─" * 42, "─" * 42)
    for tag, dashboard in dashboards:
        tj = f"triage_classification_{tag}.json"
        dh = f"triage_dashboard_{tag}.html"
        log.info("  %-6s  %-42s  %-42s", tag, tj, dh)
    log.info("")
    log.info("  Log: %s/pipeline.log", query_id)
    log.info("")


if __name__ == "__main__":
    main()
