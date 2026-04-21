"""
fetch_hsd_ids.py

Fetches HSD IDs (and titles) from an HSDES saved query

Titles stored alongside IDs serve as GENI retrieval anchors during summary
extraction, dramatically reducing wrong-ticket hallucination.

Additionally generates platform-specific lists by classifying each HSD
ticket based on keywords found in its title (mutually exclusive, priority order):
  1. other_hsd_list.txt : titles containing VTD  (highest priority)
  2. mcp_hsd_list.txt   : titles containing MCP, XOS, or CorePMA
  3. imh2_hsd_list.txt  : titles containing IMH2/iMH2 or MIO
  4. imh1_hsd_list.txt  : titles containing IMH1/iMH1 or bare IMH/iMH
  5. other_hsd_list.txt : titles with no platform keyword (catch-all)

Each ticket appears in exactly one list.

Usage:
    python fetch_hsd_ids.py <query_id>   # fetch IDs + titles from a saved query
    Example: python fetch_hsd_ids.py 14025227120
"""
import json
import os
import re
import subprocess
import sys
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HSDES_BASE = "https://hsdes.intel.com/rest"
HSD_LIST_PATH = "hsd_list.txt"
IMH1_LIST_PATH  = "imh1_hsd_list.txt"
IMH2_LIST_PATH  = "imh2_hsd_list.txt"
MCP_LIST_PATH   = "mcp_hsd_list.txt"
OTHER_LIST_PATH = "other_hsd_list.txt"


def _classify_by_title(title):
    """
    Return the single platform category for the given HSD title.

    Priority (first match wins):
      1. 'other' – title contains VTD  (highest priority → other list)
      2. 'mcp'   – title contains MCP, XOS, or CorePMA
      3. 'imh2'  – title contains IMH2/iMH2 or MIO
      4. 'imh1'  – title contains IMH1/iMH1 OR bare IMH/iMH (not followed by a digit)
      5. None    – no platform keyword found
    """
    if re.search(r'\bvtd\b', title, re.IGNORECASE):
        return 'other'
    if re.search(r'\bmcp\b', title, re.IGNORECASE) or re.search(r'\bxos\b', title, re.IGNORECASE) or re.search(r'\bcorepma\b', title, re.IGNORECASE):
        return 'mcp'
    if re.search(r'imh2', title, re.IGNORECASE) or re.search(r'\bmio\b', title, re.IGNORECASE):
        return 'imh2'
    if re.search(r'imh1', title, re.IGNORECASE) or re.search(r'\bimh(?!\d)', title, re.IGNORECASE):
        return 'imh1'
    return None

def _curl_get(url):
    result = subprocess.run(
        ['curl', '-s', '--negotiate', '-u', ':', '-k',
         '-H', 'Accept: application/json', url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] curl failed: {result.stderr}")
        return None
    return result.stdout.strip() or None

def fetch_hsd_and_title_from_query(query_id, output_file=HSD_LIST_PATH):
    """
    Fetch HSD IDs and titles from a saved HSDES query.

    Writes tab-separated id<TAB>title lines to:
      - hsd_list.txt          (all entries)
      - imh1_hsd_list.txt     (IMH1/iMH1 or bare IMH/iMH tickets)
      - imh2_hsd_list.txt     (IMH2/iMH2 tickets)
      - mcp_hsd_list.txt      (MCP tickets)
      - other_hsd_list.txt    (no platform keyword matched)

    A ticket appears in exactly one file based on priority:
    MCP > IMH2 > IMH1 > other.  Only entries present in the latest query are
    kept (files are fully overwritten on each run).
    """
    url = (
        f"{HSDES_BASE}/query/execution/{query_id}"
        f"?fields=id,title&start_at=0&max_results=1000"
    )
    raw = _curl_get(url)
    if not raw:
        print("[ERROR] No data fetched from HSDES.")
        return

    try:
        data = json.loads(raw)
        articles = data.get('data', [])
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse failed: {e}")
        print(f"[DEBUG] Raw response: {raw[:500]}")
        return

    entries = {}
    for article in articles:
        hsd_id = str(article.get('id', '')).strip()
        title = article.get('title', '').strip()
        if hsd_id:
            entries[hsd_id] = title

    # Buckets keyed by category name → list of (id, title) tuples
    buckets = {'imh1': [], 'imh2': [], 'mcp': [], 'other': []}
    for hsd_id, title in entries.items():
        cat = _classify_by_title(title) or 'other'
        buckets[cat].append((hsd_id, title))

    def _write_list(path, rows):
        with open(path, 'w') as f:
            for hsd_id, title in rows:
                f.write(f"{hsd_id}\t{title}\n" if title else f"{hsd_id}\n")

    # Write master list
    _write_list(output_file, list(entries.items()))
    print(f"[INFO] {len(entries)} HSD IDs written to {output_file} (synced to query).")

    # Write platform-specific lists
    _write_list(IMH1_LIST_PATH, buckets['imh1'])
    print(f"[INFO] {len(buckets['imh1'])} entries written to {IMH1_LIST_PATH} (imh / imh1).")

    _write_list(IMH2_LIST_PATH, buckets['imh2'])
    print(f"[INFO] {len(buckets['imh2'])} entries written to {IMH2_LIST_PATH} (imh2).")

    _write_list(MCP_LIST_PATH, buckets['mcp'])
    print(f"[INFO] {len(buckets['mcp'])} entries written to {MCP_LIST_PATH} (mcp).")

    _write_list(OTHER_LIST_PATH, buckets['other'])
    print(f"[INFO] {len(buckets['other'])} entries written to {OTHER_LIST_PATH} (no platform keyword).")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fetch_hsd_and_title_from_query(sys.argv[1])
    else:
        print("Usage: python fetch_hsd_ids.py <query_id>")
