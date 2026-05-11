"""
fetch_hsd_ids.py

Fetches HSD IDs (and titles) from an HSDES saved query

Titles stored alongside IDs serve as GENI retrieval anchors during summary
extraction, dramatically reducing wrong-ticket hallucination.

Additionally generates platform-specific lists by classifying each HSD
ticket based on keywords found in its title or type (mutually exclusive, priority order):
  1. other_hsd_list.txt : titles containing VTD OR type == 'enhancement' (highest priority)
  2. mcp_hsd_list.txt   : titles containing MCP, XOS, or CorePMA
  3. cbb_hsd_list.txt   : titles containing CBB
  4. ioh_hsd_list.txt   : titles containing IOH
  5. imh1_hsd_list.txt  : titles containing IMH1/iMH1 or bare IMH/iMH
  6. other_hsd_list.txt : titles with no platform keyword (catch-all)

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


def _classify_by_title(title, hsd_type=''):
    """
    Return the single platform category for the given HSD title and type.

    Priority (first match wins):
      1. 'other' – title contains VTD OR type is 'enhancement' (highest priority)
      2. 'mcp'   – title contains MCP, XOS, or CorePMA
      3. 'cbb'   – title contains CBB
      4. 'ioh'   – title contains IOH
      5. 'imh1'  – title contains IMH1/iMH1 OR bare IMH/iMH (not followed by a digit)
      6. None    – no platform keyword found
    """
    if re.search(r'\bvtd\b', title, re.IGNORECASE) or str(hsd_type).strip().lower() == 'enhancement':
        return 'other'
    if re.search(r'\bmcp\b', title, re.IGNORECASE) or re.search(r'\bxos\b', title, re.IGNORECASE) or re.search(r'\bcorepma\b', title, re.IGNORECASE):
        return 'mcp'
    if re.search(r'\bcbb\b', title, re.IGNORECASE):
        return 'cbb'
    if re.search(r'\bioh\b', title, re.IGNORECASE):
        return 'ioh'
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

def fetch_hsd_and_title_from_query(query_id):
    """
    Fetch HSD IDs and titles from a saved HSDES query.

    Creates a directory named after the query ID and writes tab-separated
    id<TAB>title lines to:
      - <query_id>/hsd_list.txt          (all entries)
      - <query_id>/mcp_hsd_list.txt      (MCP/XOS/CorePMA tickets)
      - <query_id>/cbb_hsd_list.txt      (CBB tickets)
      - <query_id>/ioh_hsd_list.txt      (IOH tickets)
      - <query_id>/imh1_hsd_list.txt     (IMH1/iMH1 or bare IMH/iMH tickets)
      - <query_id>/other_hsd_list.txt    (VTD, enhancement type, or no keyword matched)

    A ticket appears in exactly one file based on priority:
    VTD/enhancement > MCP > CBB > IOH > IMH1 > other.  Only entries present
    in the latest query are kept (files are fully overwritten on each run).
    """
    out_dir = str(query_id)
    os.makedirs(out_dir, exist_ok=True)
    print(f"[INFO] Output directory: {out_dir}/")
    url = (
        f"{HSDES_BASE}/query/execution/{query_id}"
        f"?fields=id,title,type&start_at=0&max_results=1000"
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
        hsd_type = article.get('type', '').strip()
        if hsd_id:
            entries[hsd_id] = (title, hsd_type)

    # Buckets keyed by category name → list of (id, title) tuples
    buckets = {'imh1': [], 'mcp': [], 'cbb': [], 'ioh': [], 'other': []}
    for hsd_id, (title, hsd_type) in entries.items():
        cat = _classify_by_title(title, hsd_type) or 'other'
        buckets[cat].append((hsd_id, title))

    def _write_list(path, rows):
        with open(path, 'w') as f:
            for hsd_id, title in rows:
                f.write(f"{hsd_id}\t{title}\n" if title else f"{hsd_id}\n")

    def _p(filename):
        return os.path.join(out_dir, filename)

    # Write master list (id + title only)
    master_rows = [(hsd_id, title) for hsd_id, (title, _) in entries.items()]
    _write_list(_p('hsd_list.txt'), master_rows)
    print(f"[INFO] {len(entries)} HSD IDs written to {_p('hsd_list.txt')} (synced to query).")

    # Write platform-specific lists
    _write_list(_p('mcp_hsd_list.txt'), buckets['mcp'])
    print(f"[INFO] {len(buckets['mcp'])} entries written to {_p('mcp_hsd_list.txt')} (mcp).")

    _write_list(_p('cbb_hsd_list.txt'), buckets['cbb'])
    print(f"[INFO] {len(buckets['cbb'])} entries written to {_p('cbb_hsd_list.txt')} (cbb).")

    _write_list(_p('ioh_hsd_list.txt'), buckets['ioh'])
    print(f"[INFO] {len(buckets['ioh'])} entries written to {_p('ioh_hsd_list.txt')} (ioh).")

    _write_list(_p('imh1_hsd_list.txt'), buckets['imh1'])
    print(f"[INFO] {len(buckets['imh1'])} entries written to {_p('imh1_hsd_list.txt')} (imh / imh1).")

    _write_list(_p('other_hsd_list.txt'), buckets['other'])
    print(f"[INFO] {len(buckets['other'])} entries written to {_p('other_hsd_list.txt')} (vtd / enhancement / no platform keyword).")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fetch_hsd_and_title_from_query(sys.argv[1])
    else:
        print("Usage: python fetch_hsd_ids.py <query_id>")
