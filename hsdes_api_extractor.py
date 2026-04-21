#!/usr/bin/env python3
"""
HSDES API Extractor  –  produces raw_hsdes_data_<tag>.json
============================================================
Reads HSD IDs from an input file (e.g. mcp_hsd_list.txt), fetches each
article via the HSDES REST API (GET /rest/article/{id}), maps the returned
field names to the clean format used in sample_raw_data.json, then fetches
comments via the WS API and writes a consolidated JSON file.

Why GET instead of EQL
──────────────────────
The EQL endpoint (POST /rest/query/execution/eql) returns 503 Service
Unavailable.  GET /rest/article/{id} is available and returns all fields.
Tenant-specific fields come back with a namespace prefix
(e.g. "sighting_central.sighting.component_affected") which we strip to
match the clean format in sample_raw_data.json.

Field name mapping
──────────────────
Base HSDES fields are already clean in the GET response:
  id, title, description, status, priority, owner, tenant, component,
  family, submitted_date, domain, domain_affected, release, subject

Tenant-specific fields arrive as "<tenant>.<subject>.<short_name>"; we
strip the prefix and keep only the fields present in sample_raw_data.json:
  component_affected, sighting_forum_notes, sighting_conclusion,
  customer  →  renamed to sighting_central_sighting_customer

Output schema  (matches sample_raw_data.json)
─────────────────────────────────────────────
{
  "metadata": { ... },
  "articles": [
    {
      "hsd_id":      "<id>",
      "list_title":  "<title from input file>",
      "subject":     "hsdes_sighting",
      "not_found":   false,
      "raw": {
          "id":                                "...",
          "title":                             "...",
          "description":                       "...",
          "status":                            "...",
          "priority":                          "...",
          "owner":                             "...",
          "tenant":                            "sighting_central",
          "component":                         "...",
          "family":                            "...",
          "submitted_date":                    "...",
          "domain":                            "...",
          "domain_affected":                   "...",
          "release":                           "...",
          "sighting_forum_notes":              "...",
          "sighting_conclusion":               "...",
          "component_affected":                "...",
          "sighting_central_sighting_customer":"...",
          "subject":                           "sighting",
          "comments":                          "<concatenated comment text>"
      },
      "retrieved_at": "<ISO-8601>"
    }
  ],
  "failed": [ { "hsd_id": ..., "error": ... }, ... ]
}

Authentication
──────────────
Kerberos SSO.  Run `kinit` before running this script.

Requirements
────────────
    pip install requests requests-kerberos urllib3

Usage
─────
    python3 hsdes_api_extractor.py
    python3 hsdes_api_extractor.py --ids-file mcp_hsd_list.txt --tag mcp --workers 2
    python3 hsdes_api_extractor.py --ids 14025435518 22021701817
    python3 hsdes_api_extractor.py --tenant sighting_central --subject sighting
"""

import re
import sys
import json
import time
import html
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests_kerberos import HTTPKerberosAuth, OPTIONAL
import urllib3

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
HSDES_BASE_URL  = "https://hsdes-api.intel.com/rest"
DEFAULT_CERT    = "/etc/ssl/certs/"
PROXIES         = {"http": "", "https": ""}
REQUEST_DELAY   = 1.0
MAX_RETRIES     = 4
RETRY_BACKOFF   = 2.0
COMMENT_PAGE_SZ = 200
DEFAULT_WORKERS = 2
_RATE_LIMIT_MSG = "RateLimit"

DEFAULT_TENANT  = "sighting_central"
DEFAULT_SUBJECT = "sighting"

# Direct mapping: key_in_GET_response → key_in_output_raw
# Determined by inspecting the actual GET /rest/article/{id} response.
FIELD_MAP = {
    # Base fields (already clean in the API response)
    "id":               "id",
    "title":            "title",
    "description":      "description",
    "status":           "status",
    "priority":         "priority",
    "owner":            "owner",
    "tenant":           "tenant",
    "component":        "component",
    "family":           "family",
    "submitted_date":   "submitted_date",
    "domain":           "domain",
    "domain_affected":  "domain_affected",
    "release":          "release",
    "subject":          "subject",
    "component_affected": "component_affected",
    # Tenant-specific fields (actual keys observed from the API)
    "sighting.forum_notes":                  "sighting_forum_notes",
    "sighting.conclusion":                   "sighting_conclusion",
    "sighting_central.sighting.customer":    "sighting_central_sighting_customer",
}

# All expected output keys (used to fill missing keys with None)
ALL_OUTPUT_KEYS = set(FIELD_MAP.values()) | {"comments"}

# Fields whose values contain HTML markup from the API — stripped to plain text.
HTML_TEXT_FIELDS = {"description", "sighting_forum_notes", "comments"}

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("hsdes_api_extractor.log"),
    ],
)
log = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_session(cert: str) -> requests.Session:
    s = requests.Session()
    s.auth    = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    s.verify  = cert
    s.proxies = PROXIES
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    return s


def _is_rate_limit(resp) -> bool:
    if resp is None:
        return False
    return resp.status_code == 429 or (
        resp.status_code == 401 and _RATE_LIMIT_MSG in (resp.text or "")
    )


def _get(session: requests.Session, url: str, params: dict = None) -> dict:
    """GET with exponential-backoff retry; handles rate-limit 401."""
    backoff = RETRY_BACKOFF
    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            if _is_rate_limit(resp):
                log.warning("GET %s  attempt %d/%d  rate-limited, waiting %.1fs",
                            url, attempt, MAX_RETRIES, backoff * 3)
                if attempt < MAX_RETRIES:
                    time.sleep(backoff * 3)
                    backoff *= 2
                continue
            raise
        except Exception as exc:
            log.warning("GET %s  attempt %d/%d  %s", url, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} attempts")


def _post(session: requests.Session, url: str, payload,
          params: dict = None) -> dict:
    """POST with exponential-backoff retry; handles rate-limit 401."""
    backoff = RETRY_BACKOFF
    resp = None
    body = json.dumps(payload) if not isinstance(payload, str) else payload
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, data=body, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            if _is_rate_limit(resp):
                log.warning("POST %s  attempt %d/%d  rate-limited, waiting %.1fs",
                            url, attempt, MAX_RETRIES, backoff * 3)
                if attempt < MAX_RETRIES:
                    time.sleep(backoff * 3)
                    backoff *= 2
                continue
            raise
        except Exception as exc:
            log.warning("POST %s  attempt %d/%d  %s", url, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"POST {url} failed after {MAX_RETRIES} attempts")


# ─────────────────────────────────────────────────────────────────────────────
# Article fetch + field mapping
# ─────────────────────────────────────────────────────────────────────────────

def fetch_article_get(session: requests.Session, hsd_id: str) -> dict:
    """
    GET /rest/article/{id}
    Returns the flat article dict (resp["data"][0]).
    Raises RuntimeError if the article is not found.
    """
    url  = f"{HSDES_BASE_URL}/article/{hsd_id}"
    resp = _get(session, url)
    data = resp.get("data") or []
    if not data:
        raise RuntimeError(f"Article not found: id={hsd_id}")
    return data[0] if isinstance(data, list) else data


def _clean_text(value) -> str:
    """Strip HTML tags, unescape entities, and collapse whitespace.
    <img> src URLs are preserved as [image: <url>] so binary attachment
    references (e.g. https://hsdes.intel.com/rest/binary/<id>) are not lost.
    """
    if not value:
        return ""
    # Preserve image attachment URLs before stripping all other tags
    text = re.sub(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*/?>',
                  r' [image: \1] ', str(value), flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_article_fields(flat: dict) -> dict:
    """
    Map the raw GET /rest/article/{id} response flat dict to the clean
    format matching sample_raw_data.json, using the direct FIELD_MAP lookup.

    - HTML is stripped and entities unescaped for HTML_TEXT_FIELDS.
    - Missing output keys are filled with None.
    - id is always a string.
    - The 'comments' key is NOT set here; it is handled separately by
      parse_embedded_comments().
    """
    result = {}
    for api_key, out_key in FIELD_MAP.items():
        if api_key in flat:
            value = flat[api_key]
            result[out_key] = _clean_text(value) if out_key in HTML_TEXT_FIELDS else value

    for k in ALL_OUTPUT_KEYS:
        result.setdefault(k, None)

    result["id"] = str(result.get("id") or "")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Comments  (embedded in GET /rest/article/{id} as the 'comments' field)
# ─────────────────────────────────────────────────────────────────────────────
# The API returns comments as a single string with '++++ ' record separators:
#   "++++<id> <user>\n<body>++++<id2> <user2>\n<body2>..."
# We parse this into the compact format used by sample_raw_data.json:
#   "<id> <user> <text>  <id2> <user2> <text2> ..."


# ─────────────────────────────────────────────────────────────────────────────
# Comment formatting
# ─────────────────────────────────────────────────────────────────────────────

def parse_embedded_comments(raw_comments: str) -> str:
    """
    Parse the 'comments' string returned by GET /rest/article/{id}.

    The API encodes comments as:
        "++++<id> <user>\n<body>++++<id2> <user2>\n<body2>..."

    Returns a single compact string:
        "<id> <user> <text>  <id2> <user2> <text2> ..."
    with HTML stripped and entities unescaped from each body.
    """
    if not raw_comments:
        return ""
    # Split on the ++++ record separator (may or may not have leading ++++)
    parts   = re.split(r"\+{4,}", raw_comments)
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # First line: "<id> <user>", rest: body text
        lines    = part.split("\n", 1)
        header   = lines[0].strip()
        body     = _clean_text(lines[1]) if len(lines) > 1 else ""
        combined = f"{header} {body}".strip()
        if combined:
            results.append(combined)
    return "  ".join(results)


# ─────────────────────────────────────────────────────────────────────────────
# Per-article orchestration
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_article(hsd_id: str, list_title: str,
                    session: requests.Session, delay: float,
                    tenant: str = DEFAULT_TENANT,
                    subject: str = DEFAULT_SUBJECT) -> dict:
    """
    Fetch one HSDES article via GET /rest/article/{id}, map field names to
    the clean format, parse the embedded comments string, and return an entry
    matching sample_raw_data.json schema.

    Comments are already included in the GET response as the 'comments' field
    (a '++++ ' delimited string), so no separate API call is needed.
    """
    # ── Article fields + embedded comments via GET ────────────────────────────
    try:
        flat_article = fetch_article_get(session, hsd_id)
    except Exception as exc:
        log.error("[%s] Article GET failed: %s", hsd_id, exc)
        return {
            "hsd_id":     hsd_id,
            "list_title": list_title,
            "not_found":  True,
            "error":      str(exc),
        }

    art_tenant  = flat_article.get("tenant") or tenant
    art_subject = flat_article.get("subject") or subject

    # Extract and clean the article fields
    raw = clean_article_fields(flat_article)

    # Parse the embedded comments string (already in the GET response)
    raw_comments = flat_article.get("comments") or ""
    raw["comments"] = parse_embedded_comments(raw_comments)

    log.info("[%s] Article OK: tenant=%s subject=%s comments_len=%d",
             hsd_id, art_tenant, art_subject, len(raw["comments"]))
    time.sleep(delay)

    return {
        "hsd_id":       hsd_id,
        "list_title":   list_title,
        "subject":      f"hsdes_{art_subject}",
        "not_found":    False,
        "raw":          raw,
        "retrieved_at": _now_iso(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Input file parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_ids_file(path: str) -> list:
    """
    Returns list of (id_str, title_str) from a tab-separated or
    whitespace-separated ID file.  Lines starting with # are ignored.
    """
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts    = line.split("\t", 1) if "\t" in line else line.split(None, 1)
            id_token = parts[0].strip()
            if not re.match(r"^\d+$", id_token):
                continue
            title = parts[1].strip() if len(parts) > 1 else ""
            entries.append((id_token, title))
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch HSDES articles via GET and produce raw_hsdes_data_<tag>.json"
    )
    parser.add_argument("--ids-file", default="mcp_hsd_list.txt",
                        help="Input file with HSD IDs")
    parser.add_argument("--ids", nargs="*",
                        help="Specific HSD IDs (overrides --ids-file)")
    parser.add_argument("--output", default=None,
                        help="Output JSON path (default: raw_hsdes_data_<tag>.json)")
    parser.add_argument("--tag", default="output",
                        help="Tag for auto-generated output filename")
    parser.add_argument("--cert", default=DEFAULT_CERT,
                        help=f"SSL cert bundle (default: {DEFAULT_CERT})")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help=f"Seconds between API calls per article (default: {REQUEST_DELAY})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--tenant", default=DEFAULT_TENANT,
                        help=f"HSDES tenant (default: {DEFAULT_TENANT})")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT,
                        help=f"HSDES subject (default: {DEFAULT_SUBJECT})")
    args = parser.parse_args()

    # ── Resolve IDs ──────────────────────────────────────────────────────────
    if args.ids:
        entries      = [(str(i), "") for i in args.ids if re.match(r"^\d+$", str(i))]
        source_files = ["--ids CLI argument"]
    else:
        entries      = parse_ids_file(args.ids_file)
        source_files = [args.ids_file]

    if not entries:
        log.error("No valid HSD IDs found.")
        sys.exit(1)

    out_path = args.output or f"raw_hsdes_data_{args.tag}.json"
    log.info("Loaded %d HSD IDs → %s", len(entries), out_path)
    log.info("tenant=%s  subject=%s  workers=%d  delay=%.1fs",
             args.tenant, args.subject, args.workers, args.delay)

    # ── Process ──────────────────────────────────────────────────────────────
    articles, failed = [], []

    def worker(entry):
        hsd_id, title = entry
        sess = _make_session(args.cert)
        try:
            return process_article(hsd_id, title, sess, args.delay,
                                   tenant=args.tenant, subject=args.subject)
        except Exception as exc:
            log.error("[%s] Unhandled error: %s", hsd_id, exc)
            return {"hsd_id": hsd_id, "list_title": title,
                    "not_found": True, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, e): e for e in entries}
        done = 0
        for future in as_completed(futures):
            done += 1
            hsd_id, _ = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"hsd_id": hsd_id, "not_found": True, "error": str(exc)}
            if result.get("not_found"):
                failed.append(result)
            else:
                articles.append(result)
            log.info("Progress %d/%d  [%s]  found=%s",
                     done, len(entries), hsd_id, not result.get("not_found"))

    # Re-sort to match input order
    id_order = {e[0]: i for i, e in enumerate(entries)}
    articles.sort(key=lambda a: id_order.get(a["hsd_id"], 9999))

    # ── Write output ─────────────────────────────────────────────────────────
    output = {
        "metadata": {
            "source_files":    source_files,
            "total_hsds":      len(entries),
            "found":           len(articles),
            "not_found":       len(failed),
            "api_errors":      len(failed),
            "extraction_mode": "hsdes_rest_api_get",
            "generated_at":    _now_iso(),
            "api_endpoint":    HSDES_BASE_URL,
        },
        "articles": articles,
        "failed":   failed,
    }
    Path(out_path).write_text(json.dumps(output, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    log.info("Done. found=%d  failed=%d → %s", len(articles), len(failed), out_path)


if __name__ == "__main__":
    main()
