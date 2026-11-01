import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

HEADER = "Patient_ID,Test_Date,C_rxn_copies_uL,V_rxn_uL,V_template_uL,V_elution_uL,V_plasma_mL,Zygosity_Z,dd_cfDNA_pct\n"
CSV = HEADER + """PT-101,2026-10-01,0.25,20.0,8.0,60.0,2.0,1,0.28
PT-102,2026-10-05,0.85,20.0,8.0,60.0,2.0,1,0.65
PT-103,2026-10-12,0.22,20.0,8.0,60.0,2.0,1,0.75
PT-104,2026-10-18,0.72,20.0,8.0,60.0,1.5,2,1.90
PT-105,2026-10-25,0.50,20.0,8.0,60.0,0.0,1,0.45
"""


def up(cl, text, name="data.csv"):
    return cl.post("/api/upload", files={"file": (name, text.encode(), "text/csv")})


def test_full_flow():
    with TestClient(app) as cl:
        d = up(cl, CSV).json()
        b = d["batch"]
        assert (b["total"], b["new"], b["invalid"], b["status"]) == (5, 5, 1, "Completed with errors")
        assert {r["patient_id"]: r["status"] for r in d["rows"]}["PT-105"] == "Invalid"

        # same file again -> no duplicates stored
        b2 = up(cl, CSV).json()["batch"]
        assert (b2["new"], b2["duplicate"], b2["status"]) == (0, 5, "No new records")
        assert cl.get("/api/results").json()["total"] == 5

        # corrected value -> updated, not duplicated
        b3 = up(cl, CSV.replace("PT-101,2026-10-01,0.25", "PT-101,2026-10-01,0.30")).json()["batch"]
        assert (b3["updated"], b3["duplicate"]) == (1, 4)
        assert cl.get("/api/results").json()["total"] == 5

        # filters
        assert {r["patient_id"] for r in cl.get("/api/results?status=high").json()["rows"]} == {"PT-102", "PT-104"}
        assert cl.get("/api/results?patient_id=105").json()["total"] == 1
        assert cl.get("/api/results?date_from=2026-10-10&date_to=2026-10-20").json()["total"] == 2
        assert cl.get("/api/results/export.csv").text.startswith("\ufeffPatient_ID")

        # history + exports + plot + patient pages
        assert len(cl.get("/api/batches").json()) == 3
        assert cl.get(f"/api/batches/{b['id']}/export.csv").status_code == 200
        assert cl.get(f"/api/plot/scatter.png?batch_id={b['id']}").headers["content-type"] == "image/png"
        cl.post("/api/patients", json={"patient_id": "PT-104", "full_name": "Test Patient", "sex": "Male"})
        cl.post("/api/patients/PT-104/biopsies", json={"biopsy_date": "2026-10-20", "outcome": "ABMR"})
        det = cl.get("/api/patients/PT-104").json()
        assert det["tests"] and det["biopsies"]
        assert cl.get("/api/patients/PT-104/report.pdf").content[:4] == b"%PDF"
        assert cl.get("/api/template.csv").text.startswith("Patient_ID,Test_Date")


def test_bad_files_are_logged():
    with TestClient(app) as cl:
        assert up(cl, "a,b\n1,2\n").status_code == 400                     # wrong columns
        assert up(cl, "").status_code == 400                               # empty
        assert up(cl, CSV, "data.txt").status_code == 400                  # wrong extension
        hist = cl.get("/api/batches").json()
        assert hist[0]["status"] == "Failed" and hist[0]["message"]


def test_negative_and_missing_values():
    with TestClient(app) as cl:
        text = HEADER + ("PT-201,2026-09-01,0.5,20,8,60,-2,1,0.4\n"
                         "PT-202,2026-09-02,,20,8,60,2,1,0.4\n"
                         "PT-203,2026-09-03,0.5,20,0,60,2,1,0.4\n")
        b = up(cl, text).json()["batch"]
        assert b["invalid"] == 3 and b["valid"] == 0
