#!/usr/bin/env python3
"""
OSP SDMX Diagnostic — figures out what format/namespace OSP actually returns
so the fetcher can be patched correctly.

USAGE
  python diagnose_osp.py [DATAFLOW_ID]

If no DATAFLOW_ID is passed, defaults to S3R629_M3010217 (the example from
OSP's own docs). Better: pass one of YOUR dataflow IDs that you saw failing,
e.g. the one assigned to NDU in dataflow_candidates.csv.

OUTPUT
  - Prints inspection report to console (paste this back)
  - Saves raw responses to disk:
      diag_data_generic.xml
      diag_data_structspec.xml
      diag_data.json
      diag_structure.xml
"""

import sys
import re
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

OSP = "https://osp-rs.stat.gov.lt"
TIMEOUT = 60


def inspect_xml(text, label):
    """Print root tag, namespaces, top-level structure, sample observation."""
    print(f"\n--- {label} ---")
    if not text.strip().startswith('<'):
        print("  Not XML (first 200 chars):")
        print("  " + text[:200].replace('\n', '\n  '))
        return

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        print(f"  First 500 chars:\n  {text[:500]}")
        return

    print(f"  Root tag: {root.tag}")
    print(f"  Root attrib: {dict(root.attrib)}")

    # Collect all unique element tags (with namespace)
    tags = set()
    for el in root.iter():
        tags.add(el.tag)
    print(f"  Total elements: {sum(1 for _ in root.iter())}")
    print(f"  Unique tags ({len(tags)}):")
    for t in sorted(tags)[:30]:
        print(f"    {t}")
    if len(tags) > 30:
        print(f"    ... and {len(tags) - 30} more")

    # Find anything that looks like a series or observation
    for hint in ['Series', 'Obs', 'Observation']:
        matches = [el for el in root.iter() if el.tag.endswith('}' + hint) or el.tag == hint]
        if matches:
            print(f"\n  Found {len(matches)} <{hint}> elements")
            sample = matches[0]
            print(f"    First sample tag: {sample.tag}")
            print(f"    First sample attribs: {dict(sample.attrib)}")
            children = list(sample)[:3]
            for c in children:
                print(f"      child: {c.tag}  attribs: {dict(c.attrib)}  text: {(c.text or '').strip()[:40]}")
            break


def inspect_json(text):
    print("\n--- JSON RESPONSE ---")
    import json
    try:
        d = json.loads(text)
    except Exception as e:
        print(f"  Not valid JSON: {e}")
        print(f"  First 500 chars:\n  {text[:500]}")
        return
    print(f"  Top-level keys: {list(d.keys())}")
    if 'dataSets' in d:
        ds = d['dataSets']
        print(f"  dataSets: list of {len(ds)}")
        if ds:
            print(f"    first dataSet keys: {list(ds[0].keys())}")
            if 'series' in ds[0]:
                s = ds[0]['series']
                if isinstance(s, dict):
                    print(f"    series count: {len(s)}")
                    first_key = next(iter(s), None)
                    if first_key is not None:
                        print(f"    first series key: {first_key!r}")
                        print(f"    first series value: {str(s[first_key])[:200]}")
    if 'structure' in d:
        st = d['structure']
        print(f"  structure keys: {list(st.keys())}")
        if 'dimensions' in st:
            dims = st['dimensions']
            print(f"    dimensions keys: {list(dims.keys())}")
            for level in ['series', 'observation']:
                if level in dims:
                    items = dims[level]
                    print(f"      {level} dimensions ({len(items)}):")
                    for it in items[:8]:
                        vals = it.get('values', [])
                        print(f"        id={it.get('id','?')}  name={(it.get('name','') or '')[:40]}  values={len(vals)}")


def try_fetch(url, accept):
    print(f"\n>>> GET {url}")
    print(f"    Accept: {accept}")
    try:
        r = requests.get(url, headers={'Accept': accept, 'User-Agent': 'LRBP-diag/1.0'}, timeout=TIMEOUT)
        print(f"    Status: {r.status_code}")
        print(f"    Content-Type: {r.headers.get('Content-Type','?')}")
        print(f"    Body size: {len(r.content)} bytes")
        return r.text if r.status_code == 200 else None
    except Exception as e:
        print(f"    ERROR: {e}")
        return None


def main():
    df_id = sys.argv[1] if len(sys.argv) > 1 else 'S3R629_M3010217'
    print(f"Diagnosing dataflow: {df_id}")

    # 1. Try JSON data
    text = try_fetch(
        f"{OSP}/rest_json/data/{df_id}/?startPeriod=2023&endPeriod=2025",
        "application/json"
    )
    if text:
        with open('diag_data.json', 'w', encoding='utf-8') as f:
            f.write(text)
        inspect_json(text)

    # 2. Try generic SDMX-ML
    text = try_fetch(
        f"{OSP}/rest_xml/data/{df_id}/?startPeriod=2023&endPeriod=2025",
        "application/vnd.sdmx.genericdata+xml;version=2.1"
    )
    if text:
        with open('diag_data_generic.xml', 'w', encoding='utf-8') as f:
            f.write(text)
        inspect_xml(text, "GENERIC SDMX-ML")

    # 3. Try structure-specific SDMX-ML
    text = try_fetch(
        f"{OSP}/rest_xml/data/{df_id}/?startPeriod=2023&endPeriod=2025",
        "application/vnd.sdmx.structurespecificdata+xml;version=2.1"
    )
    if text:
        with open('diag_data_structspec.xml', 'w', encoding='utf-8') as f:
            f.write(text)
        inspect_xml(text, "STRUCTURE-SPECIFIC SDMX-ML")

    # 4. Try the default rest_xml endpoint with plain Accept
    text = try_fetch(
        f"{OSP}/rest_xml/data/{df_id}/?startPeriod=2023&endPeriod=2025",
        "application/xml"
    )
    if text:
        inspect_xml(text, "DEFAULT (application/xml)")

    # 5. Get the dataflow STRUCTURE for codelist discovery
    text = try_fetch(
        f"{OSP}/rest_xml/dataflow/all/{df_id}/latest?references=all",
        "application/xml"
    )
    if text:
        with open('diag_structure.xml', 'w', encoding='utf-8') as f:
            f.write(text)
        inspect_xml(text, "STRUCTURE (with references)")

    print("\n\n========== SUMMARY ==========")
    print("Files saved: diag_data.json, diag_data_generic.xml,")
    print("             diag_data_structspec.xml, diag_structure.xml")
    print("\nPlease paste the FULL console output back. Optionally also paste")
    print("the first ~80 lines of whichever XML/JSON file actually contained data.")


if __name__ == '__main__':
    main()
