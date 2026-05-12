#!/usr/bin/env python3
"""
LRBP 2025 Indicator Pipeline — Stage 1: Discover OSP SDMX dataflow IDs.

WHAT IT DOES
  Hits the OSP SDMX REST API's dataflow catalog and tries to match each of
  the 41 LRBP base indicators to a specific OSP dataflow ID.

WHY IT EXISTS
  The fetch stage needs a dataflow ID per indicator. OSP doesn't publish
  a clean lookup, so we discover by searching the dataflow catalog for
  Lithuanian keywords from each indicator name.

OUTPUT
  - dataflow_catalog.json    : full OSP dataflow list (cached for reuse)
  - dataflow_candidates.csv  : per-indicator best-guess candidates
                               (review and edit before running stage 2)

USAGE
  python 01_discover_dataflows.py
  → review dataflow_candidates.csv → fill in DATAFLOW_ID column → save
  → run 02_fetch_values.py
"""

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    sys.exit("pip install requests pandas openpyxl")

OSP_REST = "https://osp-rs.stat.gov.lt/rest_xml"
HEADERS = {"Accept": "application/xml", "User-Agent": "LRBP-pipeline/1.0"}
TIMEOUT = 60

# 41 base indicators with Lithuanian search keywords for the OSP catalog.
# The "keywords" list is what the dataflow's name/description should contain
# for the indicator to be a candidate. Order = relevance hint.
INDICATORS = {
    # --- Socialinis ---
    'NL':  {'name': 'Nedarbo lygis',                       'sektorius': 'Socialinis',  'keywords': ['nedarbo lygis']},
    'IBD': {'name': 'Ilgalaikių bedarbių dalis',           'sektorius': 'Socialinis',  'keywords': ['ilgalaikiai bedarbiai', 'ilgalaikių bedarbių']},
    'LLS': {'name': 'Ligoninių lovų skaičius',             'sektorius': 'Socialinis',  'keywords': ['ligoninių lovų', 'lovų skaičius']},
    'MPM': {'name': 'Mokyklos plotas vienam mokiniui',     'sektorius': 'Socialinis',  'keywords': ['mokyklos plotas', 'patalpos mokyklose']},
    'MMA': {'name': 'Žmonių uždirbančių > MMA dalis',      'sektorius': 'Socialinis',  'keywords': ['minimaliąją mėnesinę algą', 'darbo užmokesčio pasiskirstymas', 'MMA']},

    # --- Susisiekimas ---
    'EIS': {'name': 'Įskaitinių eismo įvykių skaičius',    'sektorius': 'Susisiekimas','keywords': ['įskaitiniai eismo', 'eismo įvykiai']},
    'VTP': {'name': 'Viešojo transporto pasiekiamumas',    'sektorius': 'Susisiekimas','keywords': []},  # GIS-computed, no OSP source
    'KGP': {'name': 'Geležinkelių stočių pasiekiamumas',   'sektorius': 'Susisiekimas','keywords': []},  # GIS-computed, no OSP source
    'OUP': {'name': 'Oro uosto pasiekiamumas',             'sektorius': 'Susisiekimas','keywords': []},  # GIS-computed, no OSP source
    'KPD': {'name': 'Kelių su patobulinta danga %',        'sektorius': 'Susisiekimas','keywords': ['patobulinta danga', 'kelių danga']},

    # --- Inžinerinė ---
    'GVT': {'name': 'Prieiga prie geriamojo vandens',      'sektorius': 'Inžinerinė',  'keywords': ['geriamojo vandens tinklai', 'vandentiekio paslaugos']},
    'VBN': {'name': 'Prisijungimas prie nuotekų tinklų',   'sektorius': 'Inžinerinė',  'keywords': ['nuotekų tinklai', 'nuotekų tvarkymas']},
    'CTS': {'name': 'Centralizuotai tiekiama šiluma',      'sektorius': 'Inžinerinė',  'keywords': ['centralizuotai tiekiama šiluma', 'šilumos vartotojai']},

    # --- kraštovaizdis ---
    'NZP': {'name': 'Natūralių/pusiau natūralių ter.',     'sektorius': 'kraštovaizdis','keywords': ['kraštovaizdis', 'natūralios teritorijos']},

    # --- aplinkosauga ---
    'VTD': {'name': 'Geros būklės vandens telkiniai',      'sektorius': 'aplinkosauga','keywords': ['vandens telkinių būklė', 'paviršinis vanduo']},
    'OIT': {'name': 'Į orą išmetamų teršalų kiekis',       'sektorius': 'aplinkosauga','keywords': ['oro tarša', 'į aplinkos orą', 'teršalų emisija']},
    'EDD': {'name': 'Eroduojami dirvožemiai %',            'sektorius': 'aplinkosauga','keywords': ['erozija', 'eroduojami dirvožemiai']},

    # --- Ekonominis ---
    'NDU': {'name': 'Vidutinis neto darbo užmokestis',     'sektorius': 'Ekonominis',  'keywords': ['neto darbo užmokestis', 'vidutinis mėnesinis darbo užmokestis']},
    'TUI': {'name': 'Tiesioginės užsienio investicijos gyv.', 'sektorius': 'Ekonominis','keywords': ['tiesioginės užsienio investicijos', 'TUI']},
    'MIG': {'name': 'Materialinės investicijos gyv.',      'sektorius': 'Ekonominis',  'keywords': ['materialinės investicijos']},
    'PVG': {'name': 'Pridėtinė vertė gyventojui',          'sektorius': 'Ekonominis',  'keywords': ['pridėtinė vertė', 'BVP gyventojui', 'sukurta pridėtinė']},

    # --- Ekonominis. Urbanistinis ---
    'UGD': {'name': 'Užimtų gyventojų dalis',              'sektorius': 'Ekonominis. Urbanistinis','keywords': ['užimtumo lygis', 'užimti gyventojai']},
    'NM':  {'name': 'Neto migracija',                      'sektorius': 'Ekonominis. Urbanistinis','keywords': ['neto migracija', 'migracijos saldo']},
    'SLD': {'name': 'Statybos leidimai tūkst. gyv.',       'sektorius': 'Ekonominis. Urbanistinis','keywords': ['statybos leidimai', 'leidimai statyti']},
    'DAG': {'name': 'Darbingo amžiaus dalis',              'sektorius': 'Ekonominis. Urbanistinis','keywords': ['darbingo amžiaus', 'amžiaus grupės']},
    'PAG': {'name': 'Pensijinio amžiaus dalis',            'sektorius': 'Ekonominis. Urbanistinis','keywords': ['pensijinio amžiaus', 'pensinio amžiaus']},

    # --- Urbanistinis ---
    'USS': {'name': 'Ūkio subjektų skaičius / darb.amž.',  'sektorius': 'Urbanistinis','keywords': ['veikiantys ūkio subjektai', 'ūkio subjektai']},
    'GSM': {'name': 'Gyventojų skaičius miestuose',        'sektorius': 'Urbanistinis','keywords': ['miesto gyventojai', 'gyventojai miestuose']},
    'GSK': {'name': 'Gyventojų skaičius kaimuose',         'sektorius': 'Urbanistinis','keywords': ['kaimo gyventojai', 'gyventojai kaimuose']},
    'KGD': {'name': 'Kaimų be gyventojų dalis',            'sektorius': 'Urbanistinis','keywords': ['kaimų be gyventojų', 'gyvenamosios vietovės be gyventojų']},

    # --- Ištekliai ---
    'M':   {'name': 'Miškingumas',                         'sektorius': 'Ištekliai',   'keywords': ['miškingumas', 'miškų plotas']},
    'ZNP': {'name': 'Žemės ūkio naudmenų plotas',          'sektorius': 'Ištekliai',   'keywords': ['žemės ūkio naudmenos', 'naudmenų plotas']},
    'RIP': {'name': 'Rekreacinis išteklių potencialas',    'sektorius': 'Ištekliai',   'keywords': []},  # custom GIS metric
    'NII': {'name': 'Išžvalgyti kietųjų iškasenų ištekliai','sektorius': 'Ištekliai',  'keywords': ['naudingosios iškasenos', 'ištekliai']},
    'INI': {'name': 'Išžvalgyti naftos ištekliai',          'sektorius': 'Ištekliai',  'keywords': ['naftos ištekliai']},
    'PVI': {'name': 'Ištirti požeminio vandens ištekliai',  'sektorius': 'Ištekliai',  'keywords': ['požeminis vanduo', 'vandens ištekliai']},
    'KNI': {'name': 'Kietųjų iškasenų gavyba',              'sektorius': 'Ištekliai',  'keywords': ['iškasenų gavyba']},
    'NG':  {'name': 'Naftos gavyba',                        'sektorius': 'Ištekliai',  'keywords': ['naftos gavyba']},
    'PVA': {'name': 'Požeminio vandens gavyba',             'sektorius': 'Ištekliai',  'keywords': ['požeminio vandens gavyba']},

    # --- Ekosistemų apsauga ---
    'STP': {'name': 'Saugomos sausumos teritorijos',        'sektorius': 'Ekosistemų apsauga','keywords': ['saugomos teritorijos', 'natura 2000']},

    # --- Kultūros paveldas ---
    'KPO': {'name': 'Kultūros paveldo objektai 20 km²',     'sektorius': 'Kultūros paveldas','keywords': ['kultūros paveldas', 'paveldo objektai', 'kultūros vertybės']},
}


def fetch_all_dataflows():
    """Pull the full OSP SDMX dataflow catalog. ~3000+ entries; cache to file."""
    url = f"{OSP_REST}/dataflow/all/all/latest"
    print(f"[discover] GET {url}")
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def parse_dataflow_catalog(xml_text):
    """Extract (id, name_lt, name_en) tuples from SDMX dataflow XML."""
    root = ET.fromstring(xml_text)
    ns = {
        's': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure',
        'c': 'http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common',
    }
    flows = []
    for df in root.iter('{%s}Dataflow' % ns['s']):
        df_id = df.get('id', '')
        name_lt, name_en = '', ''
        for nm in df.findall('c:Name', ns):
            lang = nm.get('{http://www.w3.org/XML/1998/namespace}lang', 'en')
            text = (nm.text or '').strip()
            if lang == 'lt':
                name_lt = text
            else:
                name_en = text
        flows.append({'id': df_id, 'name_lt': name_lt, 'name_en': name_en})
    return flows


def score_match(keywords, name_lt, name_en):
    """Score how well a dataflow name matches the indicator keywords."""
    if not keywords:
        return 0
    text = (name_lt + ' ' + name_en).lower()
    hits = sum(1 for kw in keywords if kw.lower() in text)
    return hits


def main():
    out_dir = Path('.')
    catalog_path = out_dir / 'dataflow_catalog.json'

    if catalog_path.exists():
        print(f"[discover] Using cached catalog {catalog_path}")
        flows = json.loads(catalog_path.read_text(encoding='utf-8'))
    else:
        xml_text = fetch_all_dataflows()
        flows = parse_dataflow_catalog(xml_text)
        catalog_path.write_text(json.dumps(flows, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"[discover] Cached {len(flows)} dataflows to {catalog_path}")

    # Score candidates per indicator
    print(f"\n[discover] Matching {len(INDICATORS)} indicators against {len(flows)} dataflows")
    candidates_rows = []
    for code, meta in INDICATORS.items():
        scored = [(score_match(meta['keywords'], f['name_lt'], f['name_en']), f) for f in flows]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda x: -x[0])
        top = scored[:5]
        if not top:
            candidates_rows.append({
                'INDICATOR_CODE': code,
                'INDICATOR_NAME': meta['name'],
                'SEKTORIUS': meta['sektorius'],
                'DATAFLOW_ID': '',
                'CANDIDATE_1': '(no candidates — likely GIS-computed or no OSP source)',
                'CANDIDATE_2': '', 'CANDIDATE_3': '', 'CANDIDATE_4': '', 'CANDIDATE_5': '',
            })
        else:
            row = {
                'INDICATOR_CODE': code,
                'INDICATOR_NAME': meta['name'],
                'SEKTORIUS': meta['sektorius'],
                'DATAFLOW_ID': top[0][1]['id'],   # best guess pre-filled
            }
            for i, (score, f) in enumerate(top, 1):
                row[f'CANDIDATE_{i}'] = f"[{score}] {f['id']} — {f['name_lt'][:80]}"
            for i in range(len(top) + 1, 6):
                row[f'CANDIDATE_{i}'] = ''
            candidates_rows.append(row)

    import csv
    out = out_dir / 'dataflow_candidates.csv'
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(candidates_rows[0].keys()))
        w.writeheader()
        w.writerows(candidates_rows)
    print(f"\n[discover] Wrote candidates to {out}")
    print("[discover] REVIEW IT: top guess is in DATAFLOW_ID column.")
    print("[discover] If wrong, copy the right one from CANDIDATE_1..5 (use the ID part only — e.g. S3R629_M3010217).")
    print("[discover] Indicators with empty DATAFLOW_ID are GIS-computed; leave blank and the script will mark them N/A.")


if __name__ == '__main__':
    main()
