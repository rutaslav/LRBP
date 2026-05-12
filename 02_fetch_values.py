#!/usr/bin/env python3
"""
LRBP 2025 Indicator Pipeline — Stage 2: Fetch latest values from OSP.

WHAT IT DOES
  For each indicator with a populated DATAFLOW_ID in dataflow_candidates.csv,
  hits the OSP SDMX REST API and extracts the latest available value per
  savivaldybė, walking back year by year (2025 → 2024 → 2023) until data exists.

OUTPUT
  - raw_values_long.csv : long-format (SAV, INDICATOR_CODE, value, year, source)
  - fetch_log.txt       : per-indicator fetch report (success / fallback / fail)

USAGE
  Run stage 1 first, edit dataflow_candidates.csv if needed, then:
  python 02_fetch_values.py

NOTES
  • OSP SDMX data is structured by dimensions. Two key ones are:
      - TIME_PERIOD (year, e.g. "2025")
      - Geographic level — usually a code like "SAV" with savivaldybė codes
    The exact dimension names vary per dataflow. The script auto-detects.
  • If a dataflow returns no savivaldybė-level data, the indicator is flagged.
  • If the latest value is from before 2020, it's marked as STRUCTURAL/STALE.
"""

import csv
import json
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("pip install requests pandas")

OSP_REST = "https://osp-rs.stat.gov.lt/rest_xml"
HEADERS = {"Accept": "application/xml", "User-Agent": "LRBP-pipeline/1.0"}
TIMEOUT = 60
YEARS_TO_TRY = ['2025', '2024', '2023', '2022', '2021', '2020']

# Canonical 60 savivaldybės — names as used in the LRBP source data.
# OSP returns codes; the dimension definition will list code→name mappings.
CANONICAL_SAVS = [
    'Akmenės r. sav.', 'Alytaus m. sav.', 'Alytaus r. sav.', 'Anykščių r. sav.',
    'Birštono sav.', 'Biržų r. sav.', 'Druskininkų sav.', 'Elektrėnų sav.',
    'Ignalinos r. sav.', 'Jonavos r. sav.', 'Joniškio r. sav.', 'Jurbarko r. sav.',
    'Kaišiadorių r. sav.', 'Kalvarijos sav.', 'Kauno m. sav.', 'Kauno r. sav.',
    'Kazlų Rūdos sav.', 'Kelmės r. sav.', 'Klaipėdos m. sav.', 'Klaipėdos r. sav.',
    'Kretingos r. sav.', 'Kupiškio r. sav.', 'Kėdainių r. sav.', 'Lazdijų r. sav.',
    'Marijampolės sav.', 'Mažeikių r. sav.', 'Molėtų r. sav.', 'Neringos sav.',
    'Pagėgių sav.', 'Pakruojo r. sav.', 'Palangos m. sav.', 'Panevėžio m. sav.',
    'Panevėžio r. sav.', 'Pasvalio r. sav.', 'Plungės r. sav.', 'Prienų r. sav.',
    'Radviliškio r. sav.', 'Raseinių r. sav.', 'Rietavo sav.', 'Rokiškio r. sav.',
    'Skuodo r. sav.', 'Tauragės r. sav.', 'Telšių r. sav.', 'Trakų r. sav.',
    'Ukmergės r. sav.', 'Utenos r. sav.', 'Varėnos r. sav.', 'Vilkaviškio r. sav.',
    'Vilniaus m. sav.', 'Vilniaus r. sav.', 'Visagino sav.', 'Zarasų r. sav.',
    'Šakių r. sav.', 'Šalčininkų r. sav.', 'Šiaulių m. sav.', 'Šiaulių r. sav.',
    'Šilalės r. sav.', 'Šilutės r. sav.', 'Širvintų r. sav.', 'Švenčionių r. sav.',
]


# ---------- helpers ----------

NS = {
    'g': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic',
    's': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure',
    'c': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common',
    'm': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message',
}


def normalize_sav_name(raw):
    """OSP names sometimes lack 'sav.' suffix or have extra whitespace. Normalize."""
    if not raw:
        return ''
    n = raw.strip()
    # Strip Lithuanian gender suffixes / punctuation variants commonly seen
    n = n.replace('  ', ' ')
    # Standard endings
    for needle, replacement in [
        (' rajono savivaldybė', ' r. sav.'),
        (' rajono sav.', ' r. sav.'),
        (' miesto savivaldybė', ' m. sav.'),
        (' miesto sav.', ' m. sav.'),
        (' savivaldybė', ' sav.'),
    ]:
        if n.endswith(needle):
            n = n[:-len(needle)] + replacement
    return n


def fetch_dataflow_data(dataflow_id, start_year, end_year):
    """Fetch all observations for one dataflow within a year range."""
    url = f"{OSP_REST}/data/{dataflow_id}/?startPeriod={start_year}&endPeriod={end_year}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            return None, f"404 not found"
        r.raise_for_status()
        return r.text, None
    except requests.exceptions.RequestException as e:
        return None, str(e)


def parse_sdmx_observations(xml_text):
    """Extract observations from SDMX-ML generic data response.

    Returns list of dicts: { dims: {DIM: code}, time: 'YYYY', value: float }
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return [], f"XML parse error: {e}"

    obs_list = []
    # Generic data format: <Series><SeriesKey>(dims)</SeriesKey><Obs>(time, value)</Obs></Series>
    for series in root.iter('{%s}Series' % NS['g']):
        dims = {}
        key = series.find('g:SeriesKey', NS)
        if key is not None:
            for v in key.findall('g:Value', NS):
                dims[v.get('id')] = v.get('value')
        for ob in series.findall('g:Obs', NS):
            t = ob.find('g:ObsDimension', NS)
            v = ob.find('g:ObsValue', NS)
            if t is None or v is None:
                continue
            time_val = t.get('value', '')
            try:
                val = float(v.get('value'))
            except (TypeError, ValueError):
                continue
            obs_list.append({'dims': dict(dims), 'time': time_val, 'value': val})
    return obs_list, None


def find_geo_dim(observations):
    """Heuristic: find which dimension represents geography (savivaldybė).

    Looks for a dim with values that look like savivaldybė names or LAU codes.
    Returns the dimension ID, or None.
    """
    if not observations:
        return None
    # Collect all dim values per dim_id
    dim_values = defaultdict(set)
    for o in observations:
        for d, v in o['dims'].items():
            dim_values[d].add(v)
    # Geography dim has many distinct values (~60); other dims usually have 1-10
    candidates = [(len(vs), d) for d, vs in dim_values.items()]
    candidates.sort(reverse=True)
    if candidates and candidates[0][0] >= 30:
        return candidates[0][1]
    return None


def filter_to_savivaldybes(observations, geo_dim, code_to_name):
    """Keep only observations where geo dim resolves to a recognized savivaldybė."""
    out = []
    for o in observations:
        code = o['dims'].get(geo_dim)
        if not code:
            continue
        name = code_to_name.get(code, '')
        if name and any(name == c for c in CANONICAL_SAVS):
            out.append({**o, 'sav': name})
    return out


def fetch_codelist(codelist_id):
    """Fetch a codelist (e.g. for geographic codes) and return code→name dict."""
    url = f"{OSP_REST}/codelist/all/{codelist_id}/latest"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        return {}
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return {}
    mapping = {}
    for code in root.iter('{%s}Code' % NS['s']):
        cid = code.get('id', '')
        name_lt = ''
        for nm in code.findall('c:Name', NS):
            lang = nm.get('{http://www.w3.org/XML/1998/namespace}lang', 'en')
            if lang == 'lt':
                name_lt = (nm.text or '').strip()
        if cid and name_lt:
            mapping[cid] = normalize_sav_name(name_lt)
    return mapping


def fetch_dataflow_structure(dataflow_id):
    """Discover which dimensions belong to a dataflow and their codelist IDs."""
    url = f"{OSP_REST}/dataflow/all/{dataflow_id}/latest?references=all"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException:
        return None


def discover_geo_codelist(structure_xml):
    """From a dataflow structure response, find the codelist containing savivaldybės."""
    try:
        root = ET.fromstring(structure_xml)
    except ET.ParseError:
        return None
    # Look for codelists with ~60 savivaldybė entries
    for cl in root.iter('{%s}Codelist' % NS['s']):
        cid = cl.get('id', '')
        codes = list(cl.findall('s:Code', NS))
        if 50 <= len(codes) <= 90:
            for code in codes[:5]:
                for nm in code.findall('c:Name', NS):
                    txt = (nm.text or '').lower()
                    if 'sav' in txt or 'savivaldyb' in txt:
                        return cid
            # Heuristic by id
            if 'sav' in cid.lower() or 'lau' in cid.lower() or 'admin' in cid.lower():
                return cid
    return None


# ---------- main fetcher ----------

def main():
    in_csv = Path('dataflow_candidates.csv')
    if not in_csv.exists():
        sys.exit("Missing dataflow_candidates.csv — run 01_discover_dataflows.py first.")

    out_rows = []
    log_lines = []

    with open(in_csv, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        indicators = list(reader)

    for i, row in enumerate(indicators, 1):
        code = row['INDICATOR_CODE']
        name = row['INDICATOR_NAME']
        df_id = (row.get('DATAFLOW_ID') or '').strip()
        if not df_id:
            log_lines.append(f"[{i:>2}/{len(indicators)}] {code:<5} SKIP — no DATAFLOW_ID (likely GIS-computed)")
            continue

        print(f"[{i:>2}/{len(indicators)}] {code:<5} {name[:50]:<50} → {df_id}")
        log_lines.append(f"[{i:>2}/{len(indicators)}] {code:<5} {name}")

        # Discover the geo codelist
        struct = fetch_dataflow_structure(df_id)
        code_to_name = {}
        if struct:
            cl_id = discover_geo_codelist(struct)
            if cl_id:
                code_to_name = fetch_codelist(cl_id)
                log_lines.append(f"      geo codelist {cl_id}: {len(code_to_name)} codes")
            else:
                log_lines.append(f"      WARN: could not auto-detect geo codelist")

        # Pull the latest few years and pick the most recent with savivaldybė coverage
        xml_text, err = fetch_dataflow_data(df_id, '2020', '2025')
        if err or not xml_text:
            log_lines.append(f"      FETCH ERROR: {err}")
            continue
        obs, perr = parse_sdmx_observations(xml_text)
        if perr or not obs:
            log_lines.append(f"      PARSE ERROR or empty: {perr or 'no observations'}")
            continue

        # Find geo dim if codelist auto-detection failed
        geo_dim = find_geo_dim(obs)
        if not geo_dim:
            log_lines.append(f"      ERROR: no geographic dimension with ≥30 distinct values")
            continue

        # If we don't have code_to_name, try to use the dim values directly (some
        # dataflows put the savivaldybė name straight into the dimension)
        if not code_to_name:
            code_to_name = {v: normalize_sav_name(v)
                            for o in obs for v in [o['dims'].get(geo_dim)] if v}

        # Group obs by (sav, year)
        sav_obs = filter_to_savivaldybes(obs, geo_dim, code_to_name)
        if not sav_obs:
            log_lines.append(f"      WARN: no obs match canonical savivaldybės — check name normalization")
            continue

        # Pick most recent year with full coverage; fall back per-savivaldybė if needed
        by_year = defaultdict(dict)
        for o in sav_obs:
            year = o['time'][:4]
            by_year[year][o['sav']] = o['value']

        log_lines.append(f"      years with data: {sorted(by_year.keys(), reverse=True)}")

        # For each savivaldybė, take the latest year with a value
        for sav in CANONICAL_SAVS:
            best_year = None
            best_val = None
            for y in sorted(by_year.keys(), reverse=True):
                if sav in by_year[y]:
                    best_year = y
                    best_val = by_year[y][sav]
                    break
            if best_val is not None:
                out_rows.append({
                    'SAV': sav,
                    'INDICATOR_CODE': code,
                    'INDICATOR_NAME': name,
                    'value': best_val,
                    'year': best_year,
                    'dataflow_id': df_id,
                })

        time.sleep(0.5)  # be polite to OSP

    # Save
    out_path = Path('raw_values_long.csv')
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['SAV','INDICATOR_CODE','INDICATOR_NAME','value','year','dataflow_id'])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n[fetch] Wrote {len(out_rows)} rows to {out_path}")

    log_path = Path('fetch_log.txt')
    log_path.write_text('\n'.join(log_lines), encoding='utf-8')
    print(f"[fetch] Log → {log_path}")


if __name__ == '__main__':
    main()
