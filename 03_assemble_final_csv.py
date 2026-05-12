#!/usr/bin/env python3
"""
LRBP 2025 Indicator Pipeline — Stage 3: Assemble final CSV.

WHAT IT DOES
  1. Loads 2016 baseline + 2026 projection from the original LRBP CSV.
  2. Loads 2025 (or latest) raw values from stage 2 output.
  3. Computes derived indicators required by composite indices:
       • Gyventojų tankumas = GSM + GSK / plotas (proxy; needs sav. plotas table)
       • Taršos ir PV santykis = OIT / PVG
  4. Standardizes every 2025 indicator (linear rescale to 0-10).
  5. Maps to 1-3 index scale via terciles (matching the original LRBP method).
  6. Recomputes the 6 composite "Banginiai" indices (SO_EK, EK, SO_AP, AP,
     EK_AP, SO) as mean of member indicators' index values, plus VID as mean
     of the 6.
  7. Writes the final wide CSV with:
       SAV, APSKRITIS, plus per dimension:
         <dim>_2016, <dim>_2025, <dim>_2026_projected, <dim>_delta_2016_2025
       and a coverage column showing how many member indicators had 2025 data.

USAGE
  Run stages 1 and 2 first, then:
  python 03_assemble_final_csv.py

INPUTS (must be in working dir)
  - raw_values_long.csv                 (from stage 2)
  - LRBP_Visi_Rodilliai__-_sutvarkyti_dots.csv   (the original LRBP file)
OUTPUT
  - LRBP_indices_2016_2025_2026.csv
  - LRBP_base_indicators_2016_2025.csv
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("pip install pandas numpy")


# ---------- mappings from Banginiai sheet ----------
# Each composite dimension → list of base-indicator codes that feed into it.
# "DERIV:taršos_PV" and "DERIV:gyv_tankumas" mark derived indicators we must compute.

COMPOSITES = {
    'SO_EK': [   # Socialinė-Ekonominė (14 members)
        'CTS', 'GVT', 'VBN', 'NDU', 'MMA',
        'KPD', 'VTP', 'KGP', 'OUP',
        'DERIV:gyv_tankumas',
        'NM', 'NL', 'IBD',
        'DERIV:tarsos_PV',
    ],
    'EK': [      # Ekonominė (13 members)
        'TUI', 'MIG', 'PVG', 'USS', 'DAG',
        'NDU', 'MMA', 'NM', 'NL', 'IBD',
        'SLD',
        'DERIV:tarsos_PV',
        'CTS',
    ],
    'SO_AP': [   # Socialinė-Aplinkos (8 members)
        'VBN', 'RIP', 'OIT', 'CTS', 'GVT',
        'VTD', 'STP', 'M',
    ],
    'AP': [      # Aplinkos (12 members)
        'VTD', 'STP', 'M', 'NZP', 'ZNP', 'EDD',
        'DERIV:tarsos_PV',
        'CTS', 'OIT', 'SLD', 'NM',
    ],
    'EK_AP': [   # Eko-Ekonominė (4 members)
        'SLD',
        'DERIV:tarsos_PV',
        'CTS', 'NM',
    ],
    'SO': [      # Socialinė (16 members)
        'LLS', 'MPM', 'PAG', 'KGD', 'EIS', 'OIT',
        'NDU', 'MMA', 'NM', 'NL', 'IBD',
        'DERIV:tarsos_PV',
        'VTD', 'STP', 'M',
    ],
}

# Direction: True if HIGHER raw value is BETTER for the index (e.g. NDU).
# False if HIGHER is WORSE (e.g. NL — high unemployment is bad).
HIGHER_IS_BETTER = {
    # Socialinis
    'NL':  False, 'IBD': False, 'LLS': True,  'MPM': True,  'MMA': True,
    # Susisiekimas
    'EIS': False, 'VTP': True,  'KGP': True,  'OUP': False, 'KPD': True,  # OUP: less drive time = better → False
    # Inžinerinė
    'GVT': True,  'VBN': True,  'CTS': True,
    # kraštovaizdis
    'NZP': True,
    # aplinkosauga
    'VTD': True,  'OIT': False, 'EDD': False,
    # Ekonominis
    'NDU': True,  'TUI': True,  'MIG': True,  'PVG': True,
    # Ekonominis. Urbanistinis
    'UGD': True,  'NM':  True,  'SLD': True,  'DAG': True,  'PAG': False,
    # Urbanistinis
    'USS': True,  'GSM': True,  'GSK': True,  'KGD': False,
    # Ištekliai
    'M':   True,  'ZNP': True,  'RIP': True,  'NII': True,  'INI': True,
    'PVI': True,  'KNI': True,  'NG':  True,  'PVA': True,
    # Ekosistemų apsauga
    'STP': True,
    # Kultūros paveldas
    'KPO': True,
    # Derived
    'DERIV:gyv_tankumas': True,
    'DERIV:tarsos_PV': False,    # more pollution per €PV = worse
}


def standardize(series, higher_is_better):
    """Linear rescale to 0-10 with direction handling."""
    s = pd.to_numeric(series, errors='coerce')
    smin, smax = s.min(), s.max()
    if pd.isna(smin) or pd.isna(smax) or smax == smin:
        return pd.Series([np.nan] * len(s), index=s.index)
    rescaled = (s - smin) / (smax - smin) * 10.0
    if not higher_is_better:
        rescaled = 10.0 - rescaled
    return rescaled


def to_tercile_index(std_series):
    """Map standardized 0-10 values to 1/2/3 via terciles among non-NaN entries."""
    s = pd.to_numeric(std_series, errors='coerce')
    valid = s.dropna()
    if len(valid) < 3:
        return pd.Series([np.nan] * len(s), index=s.index)
    t1, t2 = valid.quantile(1/3), valid.quantile(2/3)
    out = pd.Series([np.nan] * len(s), index=s.index, dtype=float)
    out[s <= t1] = 1.0
    out[(s > t1) & (s <= t2)] = 2.0
    out[s > t2] = 3.0
    return out


def main():
    # --- Load inputs ---
    src_path = Path('LRBP_Visi_Rodilliai__-_sutvarkyti_dots.csv')
    raw_path = Path('raw_values_long.csv')
    if not src_path.exists() or not raw_path.exists():
        sys.exit(f"Need both {src_path} and {raw_path} in working dir.")

    src = pd.read_csv(src_path)
    src = src[src['SAV'] != 'Vidutinė Lietuvos savivaldybė'].reset_index(drop=True)
    raw = pd.read_csv(raw_path)

    # --- 2025 wide: SAV × indicator_value ---
    wide_2025 = raw.pivot_table(index='SAV', columns='INDICATOR_CODE', values='value', aggfunc='first')
    year_2025 = raw.pivot_table(index='SAV', columns='INDICATOR_CODE', values='year', aggfunc='first')

    # Derived: Taršos ir PV santykis
    if 'OIT' in wide_2025.columns and 'PVG' in wide_2025.columns:
        wide_2025['DERIV:tarsos_PV'] = wide_2025['OIT'] / wide_2025['PVG']
    # Derived: Gyventojų tankumas — needs savivaldybės plotas; the LRBP source
    # didn't include it as a base column, so we approximate it as GSM+GSK
    # *normalized* but this requires plotas. If you have a plotas table, drop
    # it as `savivaldybes_plotas.csv` (SAV,plotas_km2) and the script will use it.
    plotas_path = Path('savivaldybes_plotas.csv')
    if plotas_path.exists() and 'GSM' in wide_2025.columns and 'GSK' in wide_2025.columns:
        plotas = pd.read_csv(plotas_path).set_index('SAV')['plotas_km2']
        wide_2025['DERIV:gyv_tankumas'] = (wide_2025['GSM'].fillna(0) + wide_2025['GSK'].fillna(0)) / plotas
    else:
        wide_2025['DERIV:gyv_tankumas'] = np.nan  # marked as missing

    # --- Standardize + index each 2025 indicator ---
    std_2025 = pd.DataFrame(index=wide_2025.index)
    idx_2025 = pd.DataFrame(index=wide_2025.index)
    for code in wide_2025.columns:
        direction = HIGHER_IS_BETTER.get(code, True)
        std_2025[code] = standardize(wide_2025[code], direction)
        idx_2025[code] = to_tercile_index(std_2025[code])

    # --- 2016 indices: already in src as <CODE>16RI columns ---
    src_2016 = src.set_index('SAV')
    idx_2016 = pd.DataFrame(index=src_2016.index)
    for code in HIGHER_IS_BETTER:
        if code.startswith('DERIV:'):
            continue
        col = f'{code}16RI' if f'{code}16RI' in src_2016.columns else f'{code}16SI' if f'{code}16SI' in src_2016.columns else None
        if col:
            idx_2016[code] = pd.to_numeric(src_2016[col], errors='coerce')

    # --- Compute composite indices for 2025 ---
    out = pd.DataFrame({'SAV': src['SAV'], 'APSKRITIS': src['APSKRITIS']})
    out = out.set_index('SAV')
    for dim, members in COMPOSITES.items():
        # 2016 composite from existing data
        col16 = f'16{dim}'
        out[f'{dim}_2016'] = pd.to_numeric(src_2016[col16], errors='coerce') if col16 in src_2016.columns else np.nan

        # 2025 composite: mean of member indices that have values
        member_vals_2025 = []
        coverage_count = []
        for sav in out.index:
            vals = []
            for m in members:
                if m in idx_2025.columns and sav in idx_2025.index:
                    v = idx_2025.loc[sav, m]
                    if pd.notna(v):
                        vals.append(v)
            if vals:
                member_vals_2025.append(np.mean(vals))
            else:
                member_vals_2025.append(np.nan)
            coverage_count.append(f"{len(vals)}/{len(members)}")
        out[f'{dim}_2025'] = member_vals_2025
        out[f'{dim}_2025_coverage'] = coverage_count

        # 2026 projection from source
        col26 = f'26{dim}'
        out[f'{dim}_2026_projected'] = pd.to_numeric(src_2016[col26], errors='coerce') if col26 in src_2016.columns else np.nan

        # Delta
        out[f'{dim}_delta_2016_2025'] = out[f'{dim}_2025'] - out[f'{dim}_2016']

    # VID = mean of the 6 composite dimensions
    for year_suffix in ['_2016', '_2025', '_2026_projected']:
        cols = [f'{d}{year_suffix}' for d in COMPOSITES]
        out[f'VID{year_suffix}'] = out[cols].mean(axis=1)
    out['VID_delta_2016_2025'] = out['VID_2025'] - out['VID_2016']

    # Reorder columns
    dims_order = ['EK', 'SO_EK', 'SO', 'SO_AP', 'AP', 'EK_AP', 'VID']
    col_order = ['APSKRITIS']
    for d in dims_order:
        col_order += [f'{d}_2016', f'{d}_2025', f'{d}_2026_projected', f'{d}_delta_2016_2025']
        if f'{d}_2025_coverage' in out.columns:
            col_order.append(f'{d}_2025_coverage')
    out = out[[c for c in col_order if c in out.columns]].reset_index()

    # --- Write outputs ---
    out.to_csv('LRBP_indices_2016_2025_2026.csv', index=False)
    print("Wrote LRBP_indices_2016_2025_2026.csv")

    # Also a long-form base-indicator file with 2016 and 2025 side by side
    rows = []
    for sav in src['SAV']:
        for code in HIGHER_IS_BETTER:
            if code.startswith('DERIV:'):
                continue
            row = {
                'SAV': sav,
                'INDICATOR_CODE': code,
                'value_2016': src_2016.loc[sav, f'{code}16'] if f'{code}16' in src_2016.columns else np.nan,
                'value_2025': wide_2025.loc[sav, code] if (code in wide_2025.columns and sav in wide_2025.index) else np.nan,
                'year_2025': year_2025.loc[sav, code] if (code in year_2025.columns and sav in year_2025.index) else '',
                'index_2016': idx_2016.loc[sav, code] if (code in idx_2016.columns and sav in idx_2016.index) else np.nan,
                'index_2025': idx_2025.loc[sav, code] if (code in idx_2025.columns and sav in idx_2025.index) else np.nan,
            }
            rows.append(row)
    pd.DataFrame(rows).to_csv('LRBP_base_indicators_2016_2025.csv', index=False)
    print("Wrote LRBP_base_indicators_2016_2025.csv")

    # Summary
    n_with_2025 = wide_2025.notna().sum().sum()
    n_total = len(wide_2025) * len(wide_2025.columns)
    print(f"\n2025 coverage: {n_with_2025}/{n_total} cells ({100*n_with_2025/n_total:.0f}%)")
    print("Per-indicator 2025 coverage:")
    for code in sorted(wide_2025.columns):
        cov = wide_2025[code].notna().sum()
        print(f"  {code:<25}  {cov:>3}/60")


if __name__ == '__main__':
    main()
