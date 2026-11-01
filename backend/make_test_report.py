"""Document-based test report for Graft 360.

Runs test cases written from the two assessment PDFs against the project code and writes
test_report.html (opens in your browser). Always uses a temporary SQLite database:
your real PostgreSQL data is never touched. Does not need pytest to collect anything.

Usage, from the backend folder:
    py make_test_report.py
"""
import html
import inspect as pyinspect
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "test_report.html"
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "report.db")
sys.path.insert(0, str(HERE))

IMPORT_ERR = None
try:
    import sqlalchemy
    from pydantic import BaseModel
    from fastapi.testclient import TestClient
    from app import calculations as calc, config, database, plots, services
    from app import main as appmain
except Exception:  # shown in the report instead of crashing
    IMPORT_ERR = traceback.format_exc()

# --------------------------------------------------------------------------- data from the PDFs
HEADER = "Patient_ID,Test_Date,C_rxn_copies_uL,V_rxn_uL,V_template_uL,V_elution_uL,V_plasma_mL,Zygosity_Z,dd_cfDNA_pct"
BODY = """PT-101,2026-10-01,0.25,20.0,8.0,60.0,2.0,1,0.28
PT-102,2026-10-05,0.85,20.0,8.0,60.0,2.0,1,0.65
PT-103,2026-10-12,0.22,20.0,8.0,60.0,2.0,1,0.75
PT-104,2026-10-18,0.72,20.0,8.0,60.0,1.5,2,1.90
PT-105,2026-10-25,0.50,20.0,8.0,60.0,0.0,1,0.45
"""
SAMPLE = HEADER + "\n" + BODY
SAMPLE_SPACED = HEADER.replace(",", ", ") + "\n" + BODY       # header style used in the second PDF

# worked out by hand from the PDF formulas (PT-105 has plasma volume 0, so no result)
HAND = {
    "PT-101": dict(cp=18.75, eta=-1.79325, prob=0.1427, cm=-0.5700, status="Low Risk (Negative)"),
    "PT-102": dict(cp=63.75, eta=0.10875, prob=0.5272, cm=0.5888, status="High Risk (Positive)"),
    "PT-103": dict(cp=16.5, eta=-0.8625, prob=0.2968, cm=-0.1054, status="Low Risk (Negative)"),
    "PT-104": dict(cp=144.0, eta=4.74, prob=0.9913, cm=1.9878, status="High Risk (Positive)"),
}
IN_ALL = ["C_rxn_copies_uL", "V_rxn_uL", "V_template_uL", "V_elution_uL", "V_plasma_mL", "Zygosity_Z", "dd_cfDNA_pct"]

GROUPS = [
    ("A", "Step 1: the input dataset", "Assignment PDF, Step 1"),
    ("B", "Step 2: the three formulas and the risk status", "Assignment PDF, Step 2"),
    ("C", "Step 3: bad rows must not crash the app", "Assignment PDF, Step 3 (FastAPI backend and input validation)"),
    ("D", "Step 3: web report table and scatter plot", "Assignment PDF, Step 3 (web report view and plot)"),
    ("E", "Interview questions Q3 to Q8", "Interview packet PDF, Parts B and C"),
]

# --------------------------------------------------------------------------- tiny test framework
CHECKS = []


class Skip(Exception):
    pass


class Mismatch(AssertionError):
    def __init__(self, exp, act):
        super().__init__("Expected and actual differ")
        self.exp, self.act = exp, act


def check(cid, group, source, title, requirement, how, fin=(), fout=()):
    def deco(fn):
        CHECKS.append(dict(id=cid, group=group, source=source, title=title, req=requirement, how=how,
                           fin=list(fin), fout=list(fout), fn=fn))
        return fn
    return deco


def fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, dict):
        return "  |  ".join(f"{k}: {fmt(x)}" for k, x in v.items())
    if isinstance(v, tuple):
        return "(" + ", ".join(fmt(x) for x in v) + ")"
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v)
    return str(v)


def same(e, a, tol):
    if isinstance(e, dict):
        return isinstance(a, dict) and e.keys() == a.keys() and all(same(e[k], a[k], tol) for k in e)
    if isinstance(e, (list, tuple)):
        return isinstance(a, (list, tuple)) and len(e) == len(a) and all(same(x, y, tol) for x, y in zip(e, a))
    if isinstance(e, float) or isinstance(a, float):
        try:
            return abs(float(e) - float(a)) <= (tol if tol is not None else 1e-9)
        except (TypeError, ValueError):
            return False
    return e == a


def expect(exp, act, tol=None):
    if not same(exp, act, tol):
        raise Mismatch(exp, act)
    return fmt(exp), fmt(act)


def ok(cond, exp_text, act_text):
    if not cond:
        raise Mismatch(exp_text, act_text)
    return exp_text, act_text


# --------------------------------------------------------------------------- helpers
_client = None
_up = None


def client():
    global _client
    if _client is None:
        _client = TestClient(appmain.app)
        _client.__enter__()          # runs startup: creates tables in the temporary database
    return _client


def upload(text, name="data.csv"):
    return client().post("/api/upload", files={"file": (name, text.encode(), "text/csv")})


def sample_upload():
    global _up
    if _up is None:
        r = upload(SAMPLE, "sample_holograft_data.csv")
        _up = (r.status_code, r.json())
    return _up


def sample_rows():
    return {r["Patient_ID"]: calc.process_row(r) for r in services.read_csv_rows(SAMPLE.encode())}


def raw(**kw):
    base = dict(Patient_ID="PT-X", Test_Date="2026-10-01", C_rxn_copies_uL="0.25", V_rxn_uL="20", V_template_uL="8",
                V_elution_uL="60", V_plasma_mL="2", Zygosity_Z="1", dd_cfDNA_pct="0.28")
    base.update(kw)
    return base


def read_figure(func, args, reader):
    """Run a plot function, keep its Matplotlib figure open long enough to inspect it."""
    holder, orig = [], plots.plt.close
    plots.plt.close = lambda fig=None: holder.append(fig)
    try:
        func(*args)
    finally:
        plots.plt.close = orig
    fig = holder[0]
    try:
        return reader(fig)
    finally:
        orig(fig)


def lines_of(ax):
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v                      # dates on the x axis are kept as they are
    return [([num(x) for x in ln.get_xdata()], [num(y) for y in ln.get_ydata()], ln.get_linestyle()) for ln in ax.lines]


# =========================================================================== A: dataset
@check("A1", "A", "Assignment PDF, Step 1", "The sample CSV is read with Pandas",
       "Save the dataset as sample_holograft_data.csv: 5 patients, 9 columns.",
       "Feeds the exact 5-row dataset from the PDF to the CSV reader.", fin=["sample_holograft_data.csv"])
def a1():
    rows = services.read_csv_rows(SAMPLE.encode())
    return expect({"rows": 5, "columns": 9}, {"rows": len(rows), "columns": len(rows[0])})


@check("A2", "A", "Assignment PDF 2, Step 1", "A header with spaces after the commas still works",
       "The second PDF writes the header as 'Patient_ID, Test_Date, ...' with spaces.",
       "Reads the same data with that header style and checks every required column is found.")
def a2():
    rows = services.read_csv_rows(SAMPLE_SPACED.encode())
    return expect({"rows": 5, "all columns found": True},
                  {"rows": len(rows), "all columns found": set(calc.REQUIRED_COLUMNS) <= set(rows[0])})


# =========================================================================== B: formulas
@check("B1", "B", "Assignment PDF, Step 2, formula 1", "cp_mL (standardized plasma concentration)",
       "cp_mL = C_rxn x (V_rxn / V_template) x (V_elution / V_plasma) x Zygosity_Z",
       "Compares the app's cp_mL for each valid patient with the value worked out by hand.",
       fin=IN_ALL, fout=["cp_mL"])
def b1():
    sr = sample_rows()
    return expect({k: v["cp"] for k, v in HAND.items()}, {k: sr[k]["cp_ml"] for k in HAND}, tol=1e-6)


@check("B2", "B", "Assignment PDF, Step 2, formula 2", "eta (log-odds)",
       "eta = -2.85 + (2.10 x dd_cfDNA_pct) + (0.025 x cp_mL)",
       "Compares eta with the hand-calculated value for each valid patient.",
       fin=["dd_cfDNA_pct", "cp_mL"], fout=["eta"])
def b2():
    sr = sample_rows()
    return expect({k: v["eta"] for k, v in HAND.items()}, {k: sr[k]["eta"] for k in HAND}, tol=1e-6)


@check("B3", "B", "Assignment PDF, Step 2, formula 2", "prob (rejection probability)",
       "prob = 1 / (1 + e^-eta)", "Compares prob with the hand-calculated value (4 decimals).",
       fin=["eta"], fout=["prob"])
def b3():
    sr = sample_rows()
    return expect({k: v["prob"] for k, v in HAND.items()}, {k: sr[k]["prob"] for k in HAND}, tol=1e-4)


@check("B4", "B", "Assignment PDF, Step 2, formula 3", "CM_Score (zero-centered)",
       "CM_Score = (prob / 0.3318) - 1.0", "Compares CM_Score with the hand-calculated value.",
       fin=["prob"], fout=["CM_Score"])
def b4():
    sr = sample_rows()
    return expect({k: v["cm"] for k, v in HAND.items()}, {k: sr[k]["cm_score"] for k in HAND}, tol=5e-4)


@check("B5", "B", "Assignment PDF, Step 2", "The constants are exactly as given",
       "Topt = 0.3318, intercept -2.85, dd-cfDNA coefficient 2.10, cp_mL coefficient 0.025.",
       "Reads the constants used by the app.")
def b5():
    return expect({"Topt": 0.3318, "intercept": -2.85, "dd_coef": 2.10, "cp_coef": 0.025},
                  {"Topt": config.T_OPT, "intercept": config.INTERCEPT, "dd_coef": config.COEF_DD, "cp_coef": config.COEF_CP})


@check("B6", "B", "Assignment PDF, Step 2", "Risk status for each sample patient",
       "High Risk (Positive) if CM_Score >= 0.0, otherwise Low Risk (Negative).",
       "Checks the status the app gives to each patient in the sample file.", fin=["CM_Score"], fout=["Status"])
def b6():
    sr = sample_rows()
    return expect({k: v["status"] for k, v in HAND.items()}, {k: sr[k]["status"] for k in HAND})


@check("B7", "B", "Assignment PDF, Step 2", "The 0.0 boundary of the status rule",
       "The switch from Low to High happens at CM_Score = 0.0 (prob = 0.3318).",
       "With dd-cfDNA = 0, cp_mL = 86.00 gives a probability just above 0.3318 and 85.99 just below it.",
       fin=["cp_mL"], fout=["Status"])
def b7():
    return expect({"cp_mL 86.00": "High Risk (Positive)", "cp_mL 85.99": "Low Risk (Negative)"},
                  {"cp_mL 86.00": calc.compute_scores(86.00, 0.0)["status"],
                   "cp_mL 85.99": calc.compute_scores(85.99, 0.0)["status"]})


# =========================================================================== C: validation
@check("C1", "C", "Assignment PDF, Step 3 + Interview Q3", "PT-105 (plasma volume 0) is marked invalid, no crash",
       "Validate invalid or zero volumes so the application does not crash on bad rows.",
       "Runs the PT-105 row from the dataset. No infinite or corrupted number may be produced.",
       fin=["V_plasma_mL = 0"], fout=["Status", "cp_mL", "prob", "CM_Score"])
def c1():
    r = sample_rows()["PT-105"]
    return expect({"status": "Invalid", "cp_mL": None, "prob": None, "CM_Score": None},
                  {"status": r["status"], "cp_mL": r["cp_ml"], "prob": r["prob"], "CM_Score": r["cm_score"]})


@check("C2", "C", "Assignment PDF, Step 3 + Interview Q3", "Negative volume is rejected",
       "Catch plasma volume <= 0, not only exactly 0.", "Runs a row with V_plasma_mL = -2.",
       fin=["V_plasma_mL = -2"], fout=["Status"])
def c2():
    r = calc.process_row(raw(V_plasma_mL="-2"))
    return expect({"status": "Invalid", "message names V_plasma_mL": True},
                  {"status": r["status"], "message names V_plasma_mL": "V_plasma_mL" in (r["error_message"] or "")})


@check("C3", "C", "Interview Q3", "Blank and NaN values are rejected",
       "Check for missing/NaN values.", "Runs rows where V_plasma_mL is empty, 'NaN' and 'null'.",
       fin=["V_plasma_mL blank / NaN / null"], fout=["Status"])
def c3():
    got = {v or "(blank)": calc.process_row(raw(V_plasma_mL=v))["status"] for v in ["", "NaN", "null"]}
    return expect({"(blank)": "Invalid", "NaN": "Invalid", "null": "Invalid"}, got)


@check("C4", "C", "Assignment PDF, Step 3", "Text in a number column is rejected",
       "Bad rows must not crash the app.", "Runs a row with the word 'abc' as dd_cfDNA_pct and as C_rxn_copies_uL.",
       fin=["dd_cfDNA_pct", "C_rxn_copies_uL"], fout=["Status"])
def c4():
    got = [calc.process_row(raw(dd_cfDNA_pct="abc"))["status"], calc.process_row(raw(C_rxn_copies_uL="abc"))["status"]]
    return expect(["Invalid", "Invalid"], got)


@check("C5", "C", "Assignment PDF, Step 3", "Zero template volume is rejected (second divisor)",
       "Validate invalid or zero volumes.", "Runs a row with V_template_uL = 0, which is also used in a division.",
       fin=["V_template_uL = 0"], fout=["Status"])
def c5():
    return expect("Invalid", calc.process_row(raw(V_template_uL="0"))["status"])


@check("C6", "C", "Assignment PDF, Step 3", "A file with wrong columns is refused with a clear message",
       "The application must not crash on a bad file.", "Uploads a CSV that has only columns 'a' and 'b'.",
       fin=["CSV columns"], fout=["HTTP 400", "message"])
def c6():
    r = upload("a,b\n1,2\n")
    return expect({"http": 400, "message says missing columns": True},
                  {"http": r.status_code, "message says missing columns": "Missing" in r.json().get("detail", "")})


@check("C7", "C", "Assignment PDF, Step 3", "The whole sample file is processed: 4 valid, 1 invalid",
       "Compute cp_mL, prob, CM_Score and Status for valid rows; flag invalid rows.",
       "Uploads the 5-row dataset through the real upload endpoint.", fin=["sample_holograft_data.csv"],
       fout=["valid rows", "invalid rows"])
def c7():
    code, d = sample_upload()
    b = d["batch"]
    return expect({"http": 200, "rows": 5, "valid": 4, "invalid": 1},
                  {"http": code, "rows": b["total"], "valid": b["valid"], "invalid": b["invalid"]})


@check("C8", "C", "Interview Q3", "No infinite or corrupted value is saved",
       "Return a clear warning rather than saving infinite/corrupted values.",
       "Reads back every saved sample result and checks each number is finite; the invalid row has no cp_mL.")
def c8():
    sample_upload()
    rows = [r for r in client().get("/api/results?limit=500").json()["rows"]
            if r["patient_id"] in ("PT-101", "PT-102", "PT-103", "PT-104", "PT-105")]
    nums = [r[k] for r in rows for k in ("cp_ml", "eta", "prob", "cm_score") if r[k] is not None]
    bad = [r for r in rows if r["patient_id"] == "PT-105" and r["cp_ml"] is not None]
    return ok(len(rows) == 5 and all(math.isfinite(n) for n in nums) and not bad,
              "5 saved rows, every number finite, PT-105 has no cp_mL",
              f"{len(rows)} saved rows, {len(nums)} numbers all finite, PT-105 cp_mL empty")


# =========================================================================== D: web report
@check("D1", "D", "Assignment PDF, Step 3", "Table numbers are rounded to 2 decimal places",
       "Display an HTML table (rounded to 2 decimal places).", "Looks for the 2-decimal rounding in the page script (frontend/app.js).")
def d1():
    js = (config.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    return ok("toFixed(2)" in js, "numbers formatted with toFixed(2)", "toFixed(2) found" if "toFixed(2)" in js else "not found")


@check("D2", "D", "Assignment PDF, Step 3", "High Risk, Low Risk and invalid rows get different colours",
       "Highlight High Risk rows, Low Risk rows and invalid rows.",
       "Checks the page script gives each row a class, and the stylesheet colours all three.")
def d2():
    js = (config.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    css = (config.FRONTEND_DIR / "style.css").read_text(encoding="utf-8")
    found = {c: (f"tr.{c} td" in css) for c in ("high", "low", "invalid")}
    uses = all(w in js for w in ('"invalid"', '"high"', '"low"'))
    return ok(all(found.values()) and uses, "rules for tr.high, tr.low, tr.invalid, and the script sets them",
              f"css rules: {', '.join(k for k, v in found.items() if v)}; script sets classes: {uses}")


@check("D3", "D", "Assignment PDF, Step 3", "Scatter plot: dd_cfDNA_pct on X, cp_mL on Y, invalid row left out",
       "Matplotlib scatter plot of dd_cfDNA_pct (X) against cp_mL (Y).",
       "Draws the plot for the sample upload and reads the plotted points back from the figure.",
       fin=["dd_cfDNA_pct", "cp_mL"])
def d3():
    rows = sample_upload()[1]["rows"]

    def reader(fig):
        ax = fig.axes[0]
        pts = sorted((round(float(x), 4), round(float(y), 4)) for c in ax.collections for x, y in c.get_offsets())
        return pts, ax.get_xlabel(), ax.get_ylabel()
    pts, xl, yl = read_figure(plots.scatter_png, (rows,), reader)
    exp = sorted((round(HAND[k]["cp"] * 0 + dd, 4), HAND[k]["cp"]) for k, dd in
                 {"PT-101": 0.28, "PT-102": 0.65, "PT-103": 0.75, "PT-104": 1.9}.items())
    e, a = expect(exp, pts, tol=1e-6)
    ok("dd" in xl.lower() and "cp_ml" in yl.lower(), "X label has dd-cfDNA, Y label has cp_mL", f"X: '{xl}', Y: '{yl}'")
    return e, a + "   (4 points, PT-105 not plotted)"


@check("D4", "D", "Assignment PDF, Step 3", "Dashed vertical reference line at X = 0.50 %",
       "Dashed reference line at X = 0.50%.", "Reads the lines drawn on the scatter plot.")
def d4():
    rows = sample_upload()[1]["rows"]
    found = read_figure(plots.scatter_png, (rows,), lambda f: lines_of(f.axes[0]))
    hit = [ln for ln in found if all(abs(x - 0.5) < 1e-9 for x in ln[0]) and ln[2] == "--"]
    return ok(bool(hit), "a dashed vertical line at x = 0.50", "dashed line at x = 0.50 found" if hit else "none found")


@check("D5", "D", "Assignment PDF, Step 3", "Dashed horizontal reference line at Y = 44 cp/mL",
       "Dashed reference line at Y = 44 cp/mL.", "Reads the lines drawn on the scatter plot.")
def d5():
    rows = sample_upload()[1]["rows"]
    found = read_figure(plots.scatter_png, (rows,), lambda f: lines_of(f.axes[0]))
    hit = [ln for ln in found if all(abs(y - 44) < 1e-9 for y in ln[1]) and ln[2] == "--"]
    return ok(bool(hit), "a dashed horizontal line at y = 44", "dashed line at y = 44 found" if hit else "none found")


# =========================================================================== E: interview questions
@check("E1", "E", "Interview Q3", "Plasma volume 0 or blank: upload returns a warning, not an error",
       "Return a clear validation warning rather than crashing.",
       "Uploads one-row files with V_plasma_mL = 0, blank and NaN, and reads the app's answer.",
       fin=["V_plasma_mL"], fout=["HTTP status", "warning text"])
def e1():
    got = {}
    for i, v in enumerate(["0", "", "NaN"]):
        r = upload(f"{HEADER}\nPT-30{i},2026-10-01,0.5,20,8,60,{v},1,0.4\n")
        row = r.json()["rows"][0]
        got[v or "(blank)"] = (r.status_code, row["status"], "V_plasma_mL" in (row["error_message"] or ""))
    exp = {k: (200, "Invalid", True) for k in ("0", "(blank)", "NaN")}
    return expect(exp, got)


@check("E2", "E", "Interview Q3", "Schema validation with Pydantic",
       "Use schema validation (Pydantic models in FastAPI).",
       "Checks the patient form model is a Pydantic model, and that an empty patient ID is refused with HTTP 422.")
def e2():
    r = client().post("/api/patients", json={"patient_id": ""})
    return expect({"Pydantic model": True, "empty ID gives HTTP": 422},
                  {"Pydantic model": issubclass(appmain.PatientIn, BaseModel), "empty ID gives HTTP": r.status_code})


@check("E3", "E", "Interview Q3", "One bad row does not stop the good rows",
       "Do not crash on bad rows.", "Uploads a file where the first row is bad and the second row is good.")
def e3():
    r = upload(f"{HEADER}\nPT-310,2026-10-01,0.5,20,8,60,0,1,0.4\nPT-311,2026-10-02,0.5,20,8,60,2,1,0.4\n").json()
    return expect(["Invalid", "Low Risk (Negative)"] if r["rows"][1]["status"].startswith("Low") else ["Invalid", "High Risk (Positive)"],
                  [r["rows"][0]["status"], r["rows"][1]["status"]])


@check("E4", "E", "Interview Q4", "Normalized tables: patients, test_results, biopsies",
       "A Patients table with patient_id as primary key, plus Test_Results and Biopsies tables.",
       "Inspects the real table structure.", fout=["patients", "test_results", "biopsies"])
def e4():
    insp = sqlalchemy.inspect(database.engine)
    names = set(insp.get_table_names())
    pk = insp.get_pk_constraint("patients")["constrained_columns"]
    return expect({"tables exist": True, "patients primary key": ["patient_id"]},
                  {"tables exist": {"patients", "test_results", "biopsies"} <= names, "patients primary key": pk})


@check("E5", "E", "Interview Q4", "Foreign keys link results and biopsies to the patient",
       "Test_Results and Biopsies are linked by Foreign Key (patient_id).", "Reads the foreign keys of both tables.")
def e5():
    insp = sqlalchemy.inspect(database.engine)

    def fk(t):
        return any(f["constrained_columns"] == ["patient_id"] and f["referred_table"] == "patients" for f in insp.get_foreign_keys(t))
    return expect({"test_results -> patients": True, "biopsies -> patients": True},
                  {"test_results -> patients": fk("test_results"), "biopsies -> patients": fk("biopsies")})


@check("E6", "E", "Interview Q4", "Both tables are indexed by date",
       "Test_Results and Biopsies indexed by collection date.", "Reads the indexes of both tables.")
def e6():
    insp = sqlalchemy.inspect(database.engine)

    def idx(t, col):
        return any(col in i["column_names"] for i in insp.get_indexes(t))
    return expect({"test_results by test_date": True, "biopsies by biopsy_date": True},
                  {"test_results by test_date": idx("test_results", "test_date"), "biopsies by biopsy_date": idx("biopsies", "biopsy_date")})


@check("E7", "E", "Interview Q4", "One patient can have 5 to 10 tests in a year",
       "A single recipient is tested 5 to 10 times over a year.",
       "Uploads 8 monthly tests for one patient, uploads the same file again, and counts the saved records.")
def e7():
    body = "".join(f"PT-400,2026-{m:02d}-10,0.5,20,8,60,2,1,0.4\n" for m in range(1, 9))
    upload(HEADER + "\n" + body)
    upload(HEADER + "\n" + body)
    total = client().get("/api/results?patient_id=PT-400&exact=true").json()["total"]
    return expect({"saved records": 8}, {"saved records": total})


@check("E8", "E", "Interview Q5", "The chart has a dashed threshold line at 0.0",
       "Horizontal threshold line at 0.0 separating negative and positive risk zones.",
       "Draws the CM-Score trend with Matplotlib and reads its lines back.", fin=["CM_Score by date"], fout=["trend chart"])
def e8():
    from datetime import date
    pts = [(date(2026, 8, 1), -0.2), (date(2026, 9, 1), 0.3), (date(2026, 10, 1), 0.8)]
    found = read_figure(plots.trend_png, (pts,), lambda f: lines_of(f.axes[0]))
    hit = [ln for ln in found if all(abs(y) < 1e-12 for y in ln[1]) and ln[2] == "--"]
    return ok(bool(hit), "a dashed horizontal line at y = 0.0", "dashed line at y = 0.0 found" if hit else "none found")


@check("E9", "E", "Interview Q5", "Downloadable PDF with the chart embedded",
       "Render the Matplotlib figure to an in-memory buffer and embed it in a PDF (ReportLab).",
       "Downloads the patient PDF for PT-104 and checks that it is a real PDF containing an image.", fout=["PDF file"])
def e9():
    sample_upload()
    r = client().get("/api/patients/PT-104/report.pdf")
    c = r.content
    return expect({"http": 200, "type": "application/pdf", "starts with %PDF": True, "contains image": True},
                  {"http": r.status_code, "type": r.headers.get("content-type"), "starts with %PDF": c[:4] == b"%PDF",
                   "contains image": b"/Image" in c})


@check("E10", "E", "Interview Q5", "The patient view covers the last 6 months",
       "A patient's CM-Score trend over 6 months.",
       "Uploads tests in Jan, Aug and Oct 2026 for one patient. The default view must drop January; 12 months must keep all.")
def e10():
    upload(f"{HEADER}\nPT-500,2026-01-05,0.5,20,8,60,2,1,0.4\nPT-500,2026-08-01,0.5,20,8,60,2,1,0.4\nPT-500,2026-10-05,0.5,20,8,60,2,1,0.4\n")
    six = [t["test_date"] for t in client().get("/api/patients/PT-500").json()["tests"]]
    twelve = len(client().get("/api/patients/PT-500?months=12").json()["tests"])
    return expect({"6 months": ["2026-08-01", "2026-10-05"], "12 months": 3}, {"6 months": six, "12 months": twelve})


@check("E11", "E", "Interview Q6", "Clinical interpretation is rule-based and repeatable",
       "Deterministic, rule-based interpretation text that runs 100% offline.",
       "Asks for the interpretation of a High and a Low result twice each; the text must be the same every time.")
def e11():
    hi = [calc.interpret(1.2, 120.0, 1.5, calc.HIGH) for _ in range(2)]
    lo = [calc.interpret(-0.5, 10.0, 0.2, calc.LOW) for _ in range(2)]
    return ok(hi[0] == hi[1] and lo[0] == lo[1] and hi[0] != lo[0] and "elevated risk" in hi[0] and "low risk" in lo[0],
              "same text each time; High mentions elevated risk, Low mentions low risk",
              "text repeatable; High and Low differ as expected")


@check("E12", "E", "Interview Q6", "No external AI or web service is used",
       "Patient data must not be sent to external cloud APIs.",
       "Scans every file in backend/app for imports of openai, anthropic, requests, httpx, aiohttp or urllib, and for web addresses of AI services.")
def e12():
    pat = re.compile(r"^\s*(import|from)\s+(openai|anthropic|requests|httpx|aiohttp|urllib)\b", re.M)
    bad = []
    for f in sorted((HERE / "app").glob("*.py")):
        t = f.read_text(encoding="utf-8")
        if pat.search(t) or "api.openai.com" in t or "api.anthropic.com" in t:
            bad.append(f.name)
    return expect({"files with external calls": []}, {"files with external calls": bad})


@check("E13", "E", "Interview Q7", "Docker volumes and daily pg_dump backup",
       "Persistent Docker volumes and a daily pg_dump backup to a second drive.",
       "Not built yet: you asked to leave Docker out for now.")
def e13():
    raise Skip("Not built yet. Docker and the pg_dump backup were left out on request.")


@check("E14", "E", "Interview Q8", "Hand-calculated reference tests exist and pass",
       "Write unit tests against hand-calculated reference examples before deploying.",
       "Runs the project's own pytest files (tests/test_calculations.py and tests/test_api.py).")
def e14():
    p = HERE / "tests" / "test_calculations.py"
    if "isclose" not in p.read_text(encoding="utf-8") or "18.75" not in p.read_text(encoding="utf-8"):
        raise Mismatch("reference values (e.g. 18.75) in test_calculations.py", "not found")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "tests/test_api.py", "tests/test_calculations.py"],
                           cwd=HERE, capture_output=True, text=True, timeout=240,
                           env={k: v for k, v in os.environ.items() if k != "DATABASE_URL"})
    except subprocess.TimeoutExpired:
        raise Mismatch("project tests finish", "timed out after 240 s")
    out = (r.stdout + r.stderr)
    if "No module named pytest" in out:
        raise Skip("pytest is not installed. Run: py -m pip install pytest")
    m = re.search(r"(\d+) passed", out)
    failed = re.search(r"(\d+) failed", out)
    return ok(r.returncode == 0 and bool(m), "all project tests pass",
              (f"{m.group(1)} passed" if m else "no result") + (f", {failed.group(1)} failed" if failed else ""))


# --------------------------------------------------------------------------- run
def run_all():
    results = []
    if IMPORT_ERR:
        return [dict(id="X0", group="E", source="Project", title="The project code can be loaded",
                     req="The app must start.", how="Imports the app modules.", fin=[], fout=[], status="failed",
                     exp="all modules import", act="import failed", detail=IMPORT_ERR, ms=0)]
    for c in CHECKS:
        r = {k: c[k] for k in ("id", "group", "source", "title", "req", "how", "fin", "fout")}
        t0 = time.time()
        try:
            r["exp"], r["act"] = c["fn"]()
            r["status"], r["detail"] = "passed", ""
        except Skip as s:
            r.update(status="skipped", exp="", act="", detail=str(s))
        except Mismatch as m:
            r.update(status="failed", exp=fmt(m.exp), act=fmt(m.act), detail="")
        except Exception:
            r.update(status="failed", exp="", act="", detail=traceback.format_exc())
        r["ms"] = (time.time() - t0) * 1000
        results.append(r)
    if _client is not None:
        _client.__exit__(None, None, None)
    return results


# --------------------------------------------------------------------------- HTML
def e(s):
    return html.escape(str(s))


def card(r):
    label = {"passed": "Passed", "failed": "Failed", "skipped": "Not built"}[r["status"]]
    fin = "".join(f'<span class="chip in">{e(x)}</span>' for x in r["fin"])
    fout = "".join(f'<span class="chip out">{e(x)}</span>' for x in r["fout"])
    cmp_ = ""
    if r["exp"] or r["act"]:
        cmp_ = (f'<div class="cmp"><div><span>Expected</span><p>{e(r["exp"])}</p></div>'
                f'<div class="{"bad" if r["status"] == "failed" else "good"}"><span>Actual</span><p>{e(r["act"])}</p></div></div>')
    note = f'<p class="note {r["status"]}">{e(r["detail"])}</p>' if r["detail"] and r["status"] == "skipped" else ""
    err = f'<details open class="err"><summary>Error details</summary><pre>{e(r["detail"])}</pre></details>' if r["detail"] and r["status"] == "failed" else ""
    return f"""<article class="test {r['status']}" data-status="{r['status']}" data-text="{e((r['id'] + ' ' + r['title'] + ' ' + r['req'] + ' ' + ' '.join(r['fin'] + r['fout'])).lower())}">
 <div class="top"><span class="badge {r['status']}">{label}</span><span class="tid">{e(r['id'])}</span><h3>{e(r['title'])}</h3><span class="time">{r['ms']:.0f} ms</span></div>
 <p class="src">{e(r['source'])}</p>
 <dl><dt>Requirement</dt><dd>{e(r['req'])}</dd><dt>How it is tested</dt><dd>{e(r['how'])}</dd>
 {f'<dt>Fields in</dt><dd>{fin}</dd>' if fin else ''}{f'<dt>Fields out</dt><dd>{fout}</dd>' if fout else ''}</dl>
 {cmp_}{note}{err}
</article>"""


def build(results, seconds):
    total = len(results)
    passed = sum(r["status"] == "passed" for r in results)
    failed = sum(r["status"] == "failed" for r in results)
    skipped = total - passed - failed
    ok_ = failed == 0 and total > 0
    banner = f"All {passed} built requirements passed" if ok_ else f"{failed} of {total} checks failed"
    if ok_ and skipped:
        banner += f" ({skipped} not built yet)"
    secs = []
    for gid, gtitle, gsub in GROUPS:
        rs = [r for r in results if r["group"] == gid]
        if rs:
            gp = sum(r["status"] == "passed" for r in rs)
            secs.append(f'<section><div class="ghead"><div><h2>{e(gtitle)}</h2><p>{e(gsub)}</p></div>'
                        f'<span class="gcount">{gp} of {len(rs)} passed</span></div>{"".join(card(r) for r in rs)}</section>')
    return (TEMPLATE.replace("__BANNER__", e(banner)).replace("__OKCLASS__", "ok" if ok_ else "bad")
            .replace("__TOTAL__", str(total)).replace("__PASSED__", str(passed)).replace("__FAILED__", str(failed))
            .replace("__SKIPPED__", str(skipped)).replace("__SECONDS__", f"{seconds:.1f}")
            .replace("__PCT__", f"{(passed / total * 100) if total else 0:.0f}")
            .replace("__WHEN__", datetime.now().strftime("%d %b %Y, %H:%M")).replace("__SECTIONS__", "".join(secs)))


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Graft 360 - Test report</title>
<style>
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--muted:#6e6e73;--line:rgba(0,0,0,.1);--soft:rgba(0,0,0,.05);--blue:#0071e3;
--good:#1f7a3a;--good-bg:#e9f6ed;--bad:#c4160a;--bad-bg:#fdeeec;--warn:#a85200;--warn-bg:#fff3e0;--shadow:0 6px 30px rgba(0,0,0,.07)}
@media(prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#f5f5f7;--muted:#a1a1a6;--line:rgba(255,255,255,.14);--soft:rgba(255,255,255,.08);
--blue:#2997ff;--good:#6fdc92;--good-bg:#14301f;--bad:#ff8a80;--bad-bg:#3a1b19;--warn:#ffc266;--warn-bg:#352708;--shadow:none}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1000px;margin:auto;padding:48px 22px 80px}
h1{font-size:clamp(32px,5vw,48px);letter-spacing:-.03em;margin:0;line-height:1.08}
.sub{color:var(--muted);margin:6px 0 26px}
.banner{border-radius:20px;padding:20px 24px;font-size:22px;font-weight:700;letter-spacing:-.02em}
.banner.ok{background:var(--good-bg);color:var(--good)}.banner.bad{background:var(--bad-bg);color:var(--bad)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}
.stat{background:var(--card);border-radius:18px;padding:14px 18px;box-shadow:var(--shadow)}
.stat span{color:var(--muted);font-size:13px}.stat b{display:block;font-size:30px;letter-spacing:-.03em;line-height:1.2}
.stat.g b{color:var(--good)}.stat.r b{color:var(--bad)}.stat.w b{color:var(--warn)}
.bar{height:10px;border-radius:99px;background:var(--soft);overflow:hidden;margin:6px 0 26px}.bar i{display:block;height:100%;background:var(--good);width:__PCT__%}
.tools{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.seg{display:inline-flex;background:var(--soft);padding:3px;border-radius:12px}
.seg button{border:0;background:none;font:inherit;font-size:14px;padding:6px 14px;border-radius:9px;color:var(--muted);cursor:pointer}
.seg button.on{background:var(--card);color:var(--ink);font-weight:600;box-shadow:0 1px 4px rgba(0,0,0,.15)}
input[type=search]{font:inherit;font-size:14px;padding:8px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);min-width:240px;flex:1;max-width:340px}
:focus-visible{outline:3px solid var(--blue);outline-offset:2px;border-radius:8px}
.ghead{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;margin:36px 0 12px;flex-wrap:wrap}
.ghead h2{margin:0;font-size:25px;letter-spacing:-.025em}.ghead p{margin:0;color:var(--muted);font-size:14px}.gcount{font-size:14px;color:var(--muted)}
.test{background:var(--card);border-radius:18px;box-shadow:var(--shadow);padding:18px 20px;margin-bottom:12px;border-left:6px solid var(--good)}
.test.failed{border-left-color:var(--bad)}.test.skipped{border-left-color:var(--warn)}
.top{display:flex;align-items:center;gap:10px}.top h3{margin:0;font-size:18px;letter-spacing:-.015em;flex:1}
.tid{font:600 12.5px ui-monospace,Menlo,Consolas,monospace;color:var(--muted);background:var(--soft);padding:2px 8px;border-radius:8px}
.time{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.badge{font-size:12.5px;font-weight:700;padding:3px 12px;border-radius:99px;background:var(--good-bg);color:var(--good);white-space:nowrap}
.badge.failed{background:var(--bad-bg);color:var(--bad)}.badge.skipped{background:var(--warn-bg);color:var(--warn)}
.src{margin:6px 0 10px;color:var(--blue);font-size:13.5px}
dl{display:grid;grid-template-columns:120px 1fr;gap:6px 12px;margin:0;font-size:14.5px}dt{color:var(--muted)}dd{margin:0}
.chip{display:inline-block;padding:1px 10px;border-radius:99px;font-size:12.5px;margin:0 6px 4px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.chip.in{background:var(--soft)}.chip.out{background:color-mix(in srgb,var(--blue) 14%,transparent);color:var(--blue)}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.cmp>div{background:var(--soft);border-radius:12px;padding:10px 14px}.cmp>div.good{background:var(--good-bg)}.cmp>div.bad{background:var(--bad-bg)}
.cmp span{font-size:12px;color:var(--muted);text-transform:none}.cmp p{margin:2px 0 0;font:13.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-word}
.note{margin:12px 0 0;padding:10px 14px;border-radius:12px;background:var(--warn-bg);color:var(--warn);font-size:14px}
details{margin-top:12px}summary{cursor:pointer;color:var(--bad);font-weight:600;font-size:14px}
pre{background:var(--bad-bg);color:var(--bad);padding:12px;border-radius:12px;overflow:auto;font-size:12.5px;white-space:pre-wrap}
.none{text-align:center;color:var(--muted);padding:30px}footer{margin-top:40px;color:var(--muted);font-size:13px;text-align:center}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;background:var(--soft);padding:1px 7px;border-radius:6px}
@media(max-width:620px){dl{grid-template-columns:1fr}dt{margin-top:4px}.cmp{grid-template-columns:1fr}.top{flex-wrap:wrap}}
@media print{body{background:#fff}.tools{display:none}.test{box-shadow:none;border:1px solid #ddd;break-inside:avoid}}
</style></head><body><main>
<h1>Graft 360 test report</h1>
<p class="sub">Generated __WHEN__. Test cases are written from the two PDFs. They run on a temporary database, your real data is not touched.</p>
<div class="banner __OKCLASS__" role="status">__BANNER__</div>
<div class="stats">
 <div class="stat"><span>Checks</span><b>__TOTAL__</b></div>
 <div class="stat g"><span>Passed</span><b>__PASSED__</b></div>
 <div class="stat r"><span>Failed</span><b>__FAILED__</b></div>
 <div class="stat w"><span>Not built</span><b>__SKIPPED__</b></div>
 <div class="stat"><span>Run time</span><b>__SECONDS__ s</b></div>
</div>
<div class="bar" aria-hidden="true"><i></i></div>
<div class="tools">
 <div class="seg" id="seg" role="group" aria-label="Filter"><button class="on" data-v="">All</button><button data-v="passed">Passed</button><button data-v="failed">Failed</button><button data-v="skipped">Not built</button></div>
 <input type="search" id="q" placeholder="Search a field, e.g. V_plasma_mL, or an ID, e.g. B2" aria-label="Search checks">
</div>
__SECTIONS__
<p class="none" id="none" hidden>No checks match.</p>
<footer>Create this page again any time: <code>py make_test_report.py</code> in the backend folder.</footer>
</main>
<script>
const tests=[...document.querySelectorAll('.test')];let st='';
function apply(){const q=document.getElementById('q').value.trim().toLowerCase();let n=0;
 tests.forEach(t=>{const ok=(!st||t.dataset.status===st)&&(!q||t.dataset.text.includes(q));t.hidden=!ok;if(ok)n++;});
 document.getElementById('none').hidden=n>0;}
document.querySelectorAll('#seg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#seg button').forEach(x=>x.classList.toggle('on',x===b));st=b.dataset.v;apply();});
document.getElementById('q').addEventListener('input',apply);
</script></body></html>"""


def main():
    start = time.time()
    results = run_all()
    for r in results:
        mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[r["status"]]
        print(f"[{mark}] {r['id']:<4} {r['title']}")
    OUT.write_text(build(results, time.time() - start), encoding="utf-8")
    p = sum(r["status"] == "passed" for r in results)
    f = sum(r["status"] == "failed" for r in results)
    print(f"\n{p} passed, {f} failed, {len(results) - p - f} not built")
    print(f"Report saved: {OUT}")
    if "--no-open" not in sys.argv:
        webbrowser.open(OUT.as_uri())
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())