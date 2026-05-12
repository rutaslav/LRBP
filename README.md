# LRBP 2025 Indicator Pipeline

Three-stage pipeline to pull current (2024/2025) Lithuanian municipal indicator
data from OSP (Valstybės duomenų agentūra) and assemble a comparison CSV
showing **2016 baseline → 2025 actual → 2026 projection** for the 7 composite
LRBP indices and 41 underlying base indicators.

---

## Quick start

```bash
# 1. Install deps
pip install requests pandas numpy openpyxl

# 2. Drop the original LRBP file into this folder
cp /path/to/LRBP_Visi_Rodilliai__-_sutvarkyti_dots.csv .

# 3. Run the three stages in order
python 01_discover_dataflows.py
# → review dataflow_candidates.csv, fix the DATAFLOW_ID column where wrong, save
python 02_fetch_values.py
# → check fetch_log.txt for any errors
python 03_assemble_final_csv.py
# → LRBP_indices_2016_2025_2026.csv  ← main deliverable
# → LRBP_base_indicators_2016_2025.csv  ← per-indicator detail
```

---

## What each stage does

### Stage 1 — `01_discover_dataflows.py`
Pulls the full OSP SDMX dataflow catalog (~3000 entries), then for each of the
41 LRBP base indicators, searches the catalog by Lithuanian keywords and
ranks the top 5 candidates. Pre-fills the best guess into `DATAFLOW_ID`.

**Manual step:** Open `dataflow_candidates.csv` and verify. The top candidate
is often right, but for ambiguous names (e.g. "darbo užmokestis" matches many
tables), pick the right one from `CANDIDATE_1..5` and replace `DATAFLOW_ID`.

**Indicators with no OSP source** (left blank intentionally):
- `VTP`, `KGP`, `OUP` — GIS-computed accessibility metrics, not in OSP
- `RIP` — custom recreational potential index, not in OSP
- `NII`, `INI`, `PVI`, `KNI`, `NG`, `PVA` — ištekliai indicators; may need
  Lietuvos geologijos tarnyba (LGT) data rather than OSP. Leave blank if you
  can't find OSP matches.

### Stage 2 — `02_fetch_values.py`
For each indicator with a `DATAFLOW_ID`:
1. Fetches the dataflow structure to identify the geographic dimension
2. Pulls all observations 2020-2025
3. For each of the 60 savivaldybės, picks the latest year with a value
4. Writes long-format `raw_values_long.csv`

Logs everything to `fetch_log.txt` — review this if coverage looks off.

### Stage 3 — `03_assemble_final_csv.py`
1. Loads 2016 + 2026 from the original LRBP source
2. Loads 2025 values from stage 2
3. Computes 2 derived indicators required by composites:
   - **Taršos ir PV santykis** = OIT / PVG (automatic)
   - **Gyventojų tankumas** = (GSM + GSK) / plotas — **needs** a separate
     `savivaldybes_plotas.csv` file with columns `SAV,plotas_km2`. Without it,
     gyv. tankumas is left blank and the composite indices that include it
     will be computed from one fewer member.
4. Standardizes each 2025 indicator (linear rescale to 0-10, with direction
   handling — see `HIGHER_IS_BETTER` dict in the script if you need to flip)
5. Maps to 1-3 tercile index (matching the original LRBP method)
6. Recomputes the 6 composite "Banginiai" indices (SO_EK, EK, SO_AP, AP, EK_AP, SO)
   as mean of member indicators' 1-3 indices
7. VID = mean of the 6 composites

---

## Output schema

`LRBP_indices_2016_2025_2026.csv` (61 rows × ~36 cols):

| Column | Meaning |
|---|---|
| `SAV` | Savivaldybė name |
| `APSKRITIS` | Apskritis |
| `<DIM>_2016` | Composite index 2016 baseline (from original LRBP) |
| `<DIM>_2025` | Composite index 2025 (recomputed from current OSP data) |
| `<DIM>_2026_projected` | Original 2026 projection from LRBP working files |
| `<DIM>_delta_2016_2025` | Change from 2016 to 2025 |
| `<DIM>_2025_coverage` | "N/M" — how many of M member indicators had data |

DIM ∈ {EK, SO_EK, SO, SO_AP, AP, EK_AP, VID}.

`LRBP_base_indicators_2016_2025.csv` (60 × 41 = 2460 rows):

| Column | Meaning |
|---|---|
| `SAV` | Savivaldybė |
| `INDICATOR_CODE` | Indicator code (NL, NDU, …) |
| `value_2016`, `value_2025` | Raw values |
| `year_2025` | Actual year used (might be 2024 if 2025 not yet published) |
| `index_2016`, `index_2025` | 1-3 tercile index values |

---

## Methodology notes & caveats

1. **The 2025 composites are reconstructions, not official LRBP figures.** The
   original LRBP team computed these in 2018-2020 using their methodology;
   this pipeline replicates the steps as best they can be inferred from the
   working files but isn't audited against the original code.

2. **Tercile boundaries are recomputed for 2025.** This means a savivaldybė
   could move from index 2→3 in 2025 because its rank improved, OR because
   the overall distribution shifted. If you want to use the 2016 boundaries,
   modify `to_tercile_index()` to take fixed thresholds.

3. **The standardization formula** is inferred as linear rescale to 0-10. The
   original LRBP file shows slight deviations from this — there may have been
   outlier capping or a slightly different rescale. Differences are second
   order (~0.1-0.5 on the 0-10 scale).

4. **Year fallback strategy** is "latest available per savivaldybė". This
   means different savivaldybės may have data from different years for the
   same indicator — usually within 1-2 years of each other. The
   `year_2025` column documents which year was actually used.

5. **Direction matters.** The `HIGHER_IS_BETTER` dict in stage 3 encodes
   whether high values are good or bad for each indicator. Double-check the
   ones that are subtle:
   - `OUP` (oro uosto pasiekiamumas) — currently set to `False` since this is
     a *drive-time* metric (less = better). If OSP returns "% of territory
     within X km of airport" instead, flip it to `True`.
   - `PAG` (pensijinio amžiaus dalis) — set to `False` (lower share of
     retirees = younger, more economically active population). Reasonable
     default but debatable depending on context.

6. **Missing data is propagated explicitly.** If an indicator has no 2025
   source (because it's GIS-computed or the dataflow lookup failed), the
   composite indices that depend on it are computed from the remaining
   members. The `_coverage` column shows this so you can spot which
   composites are under-supported.

---

## If something goes wrong

- **`Host not in allowlist` or 403 errors** — your network/firewall is
  blocking OSP. The script needs unrestricted access to `osp-rs.stat.gov.lt`.
- **`fetch_log.txt` shows "no geographic dimension"** — the dataflow may use
  a different dimension layout. Check by visiting:
  `https://osp-rs.stat.gov.lt/rest_xml/dataflow/all/{DATAFLOW_ID}/latest?references=all`
  in a browser and inspecting the dimensions.
- **Coverage is low (< 40/60)** — the savivaldybė name normalization may not
  match. Edit `normalize_sav_name()` in stage 2.
- **Composite 2025 values look way off** — check `_coverage` column first.
  If coverage is < 5/many, the composite is unreliable.
