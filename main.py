import numpy
import openpyxl
import pandas
import pip
import requests

pip install requests pandas numpy openpyxl

# Drop LRBP_Visi_Rodilliai__-_sutvarkyti_dots.csv in the folder, then:
python 01_discover_dataflows.py    # ~1 min: pulls OSP catalog, suggests dataflow IDs
# → review dataflow_candidates.csv, fix top guesses where they look wrong
python 02_fetch_values.py          # ~5-10 min: hits OSP for each indicator
python 03_assemble_final_csv.py    # instant: assembles final CSV