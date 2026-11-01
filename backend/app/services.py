import csv
import hashlib
import io
import json
import re
from datetime import date

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import calculations as calc
from .models import Patient, TestResult, UploadBatch

MAX_BYTES = 5 * 1024 * 1024

PUBLIC_FIELDS = ["patient_id", "test_date", "c_rxn_copies_ul", "v_rxn_ul", "v_template_ul",
                 "v_elution_ul", "v_plasma_ml", "zygosity_z", "dd_cfdna_pct", "cp_ml", "eta",
                 "prob", "cm_score", "status", "is_valid", "error_message"]


# ---------- CSV reading (Pandas) ----------
def read_csv_rows(content: bytes) -> list[dict]:
    """Read CSV with Pandas (all as text). Raises ValueError on a structurally bad file."""
    if not content or not content.strip():
        raise ValueError("The uploaded file is empty.")
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str, skipinitialspace=True,
                         keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:
        raise ValueError(f"Could not read the CSV file: {exc}")

    canon = {c.lower(): c for c in calc.REQUIRED_COLUMNS}
    df.columns = [canon.get(re.sub(r"\s+", "", str(c)).lower(), str(c)) for c in df.columns]
    missing = [c for c in calc.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Wrong CSV columns. Missing: " + ", ".join(missing))
    if df.empty:
        raise ValueError("The CSV has a header but no data rows.")
    return df.to_dict(orient="records")


# ---------- row helpers ----------
def row_hash(r: dict) -> str:
    parts = [r["patient_id"], r["test_date"], r["c_rxn_copies_ul"], r["v_rxn_ul"], r["v_template_ul"],
             r["v_elution_ul"], r["v_plasma_ml"], r["zygosity_z"], r["dd_cfdna_pct"], r["error_message"]]
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def public_row(d: dict) -> dict:
    out = {k: d.get(k) for k in PUBLIC_FIELDS}
    if isinstance(out["test_date"], date):
        out["test_date"] = out["test_date"].isoformat()
    out["interpretation"] = calc.interpret(out["cm_score"], out["cp_ml"], out["dd_cfdna_pct"], out["status"])
    return out


def row_to_dict(t: TestResult) -> dict:
    out = public_row({k: getattr(t, k) for k in PUBLIC_FIELDS})
    out["id"], out["batch_id"] = t.id, t.batch_id
    return out


# ---------- upload processing with duplicate handling ----------
def process_csv(db: Session, content: bytes, filename: str) -> UploadBatch:
    batch = UploadBatch(filename=filename or "upload.csv", status="Processing")
    db.add(batch)
    db.commit()                                  # keep a history record even if the file fails
    try:
        if not (filename or "").lower().endswith(".csv"):
            raise ValueError("Only .csv files are accepted.")
        if len(content) > MAX_BYTES:
            raise ValueError("File is larger than 5 MB.")
        rows = read_csv_rows(content)
    except ValueError as exc:
        batch.status, batch.message = "Failed", str(exc)
        db.commit()
        raise

    try:
        snapshot, seen = [], set()
        c = {"new": 0, "updated": 0, "duplicate": 0, "valid": 0, "invalid": 0, "high": 0, "low": 0, "kept": 0}
        for i, raw in enumerate(rows, start=1):
            r = calc.process_row(raw)
            h = row_hash(r)
            pid, d = r["patient_id"], r["test_date"]
            c["valid" if r["is_valid"] else "invalid"] += 1
            if r["status"] == calc.HIGH:
                c["high"] += 1
            elif r["status"] == calc.LOW:
                c["low"] += 1

            if pid and pid not in seen:
                if db.get(Patient, pid) is None:
                    db.add(Patient(patient_id=pid))
                    db.flush()
                seen.add(pid)

            if pid and d:
                existing = db.scalars(select(TestResult).where(
                    TestResult.patient_id == pid, TestResult.test_date == d)).first()
            else:
                existing = db.scalars(select(TestResult).where(
                    TestResult.row_hash == h,
                    or_(TestResult.patient_id.is_(None), TestResult.test_date.is_(None)))).first()

            if existing is None:
                db.add(TestResult(batch_id=batch.id, row_hash=h, **r))
                action = "new"
            elif existing.row_hash == h:
                action = "duplicate"                       # identical record already stored
            elif r["is_valid"] or not existing.is_valid:
                for k, v in r.items():                      # corrected values replace the old record
                    setattr(existing, k, v)
                existing.row_hash, existing.batch_id = h, batch.id
                action = "updated"
            else:
                action = "kept"                             # invalid row never overwrites a valid record
            db.flush()
            c[action] += 1
            snapshot.append({**public_row(r), "row_number": i, "action": action})

        batch.total_rows, batch.valid_rows, batch.invalid_rows = len(rows), c["valid"], c["invalid"]
        batch.new_rows, batch.updated_rows, batch.duplicate_rows = c["new"], c["updated"], c["duplicate"]
        batch.high_rows, batch.low_rows = c["high"], c["low"]
        batch.snapshot = json.dumps(snapshot)
        if c["new"] + c["updated"] + c["kept"] == 0:
            batch.status = "No new records"
        elif c["invalid"]:
            batch.status = "Completed with errors"
        else:
            batch.status = "Completed"
        batch.message = (f"{c['new']} new, {c['updated']} updated, {c['duplicate']} duplicate skipped, "
                         f"{c['invalid']} invalid")
        db.commit()
    except Exception as exc:
        db.rollback()
        batch = db.get(UploadBatch, batch.id)
        batch.status, batch.message = "Failed", f"Unexpected processing error: {exc}"
        db.commit()
        raise ValueError(batch.message)
    return batch


def batch_summary(b: UploadBatch) -> dict:
    return {"id": b.id, "filename": b.filename, "uploaded_at": b.uploaded_at.isoformat() + "Z",
            "status": b.status, "message": b.message, "total": b.total_rows, "new": b.new_rows,
            "updated": b.updated_rows, "duplicate": b.duplicate_rows, "valid": b.valid_rows,
            "invalid": b.invalid_rows, "high": b.high_rows, "low": b.low_rows}


def batch_payload(b: UploadBatch) -> dict:
    return {"batch": batch_summary(b), "rows": json.loads(b.snapshot) if b.snapshot else []}


def patient_valid_tests(db: Session, patient_id: str) -> list[TestResult]:
    return list(db.scalars(select(TestResult).where(
        TestResult.patient_id == patient_id, TestResult.is_valid.is_(True))
        .order_by(TestResult.test_date, TestResult.id)))


# ---------- CSV export ----------
EXPORT_COLS = [("Patient_ID", "patient_id"), ("Test_Date", "test_date"), ("C_rxn_copies_uL", "c_rxn_copies_ul"),
               ("V_rxn_uL", "v_rxn_ul"), ("V_template_uL", "v_template_ul"), ("V_elution_uL", "v_elution_ul"),
               ("V_plasma_mL", "v_plasma_ml"), ("Zygosity_Z", "zygosity_z"), ("dd_cfDNA_pct", "dd_cfdna_pct"),
               ("cp_mL", "cp_ml"), ("eta", "eta"), ("prob", "prob"), ("CM_Score", "cm_score"),
               ("Status", "status"), ("Error", "error_message")]


def rows_to_csv(rows: list[dict], with_action: bool = False) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ([("Row", "row_number")] if with_action else []) + EXPORT_COLS + ([("Saved_As", "action")] if with_action else [])
    w.writerow([h for h, _ in cols])
    for r in rows:
        w.writerow([("" if r.get(k) is None else round(r[k], 4) if isinstance(r[k], float) else r[k])
                    for _, k in cols])
    return "\ufeff" + buf.getvalue()
