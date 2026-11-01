# Graft 360 - HoloGRAFT Report App

FastAPI + Pandas + Matplotlib + ReportLab, PostgreSQL, plain HTML/CSS/JS (no build step).
Database `TASK-PROJECT` (user `postgres`, password `1234`, localhost:5432) is created automatically.

## Run (Windows PowerShell)
```
cd graft360\backend
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open http://127.0.0.1:8000   (Linux/Mac: use python3 / pip3)
Other credentials: set DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME (see backend/.env.example).
Note: on first start with this version, tables from an older version are rebuilt automatically.

## Test
`cd backend && py -m pytest -q`
HTML test report (opens in your browser, saved as backend/test_report.html): `cd backend && py make_test_report.py`

## Pages
Upload (drag and drop CSV) - Results (search, date range, risk filter, CSV export, paging) -
History (every upload with status) - Patients (trend chart, PDF report, biopsies).
There is no built-in sample data: only files you upload are processed. "Download template" gives an empty CSV with the right headers.

## Formulas (backend/app/calculations.py)
cp_mL = C_rxn x (V_rxn / V_template) x (V_elution / V_plasma) x Z
eta = -2.85 + 2.10 x dd_cfDNA_pct + 0.025 x cp_mL ; prob = 1 / (1 + e^-eta)
CM_Score = prob / 0.3318 - 1.0 ; High Risk (Positive) if >= 0 else Low Risk (Negative)

## Validation
Zero/negative/missing/NaN/non-numeric values, Zygosity not 1 or 2, dd-cfDNA outside 0-100, bad date, missing Patient_ID
-> row saved as Invalid with a message (never crashes). Wrong/missing CSV columns, empty file, non-.csv, >5 MB -> upload rejected and logged as Failed in History.

## Duplicate handling
One result per patient + test date (database unique key).
Same row again -> skipped as duplicate. Changed values for same patient + date -> existing record updated.
An invalid row never overwrites a valid saved record.

## Database
patients -> test_results (unique patient_id + test_date) and biopsies (indexed by date); upload_batches -> test_results.

## Privacy
Interpretations are rule-based text. Nothing is sent to external services.
