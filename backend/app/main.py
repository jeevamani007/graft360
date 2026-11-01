from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from . import calculations as calc
from . import config, services
from .database import Base, engine, get_db
from .models import Biopsy, Patient, TestResult, UploadBatch
from .pdf_report import build_patient_pdf
from .plots import scatter_png, trend_png


def ensure_schema():
    """Create tables. If tables from an older version exist, rebuild them (development convenience)."""
    insp = inspect(engine)
    if insp.has_table("test_results") and insp.has_table("upload_batches"):
        t_cols = {c["name"] for c in insp.get_columns("test_results")}
        b_cols = {c["name"] for c in insp.get_columns("upload_batches")}
        if "row_hash" not in t_cols or "snapshot" not in b_cols:
            Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    yield


app = FastAPI(title="Graft 360", version="2.0", lifespan=lifespan)


# ---------- schemas (Pydantic validation) ----------
class PatientIn(BaseModel):
    patient_id: str = Field(min_length=1, max_length=50)
    full_name: Optional[str] = Field(default=None, max_length=150)
    date_of_birth: Optional[date] = None
    sex: Optional[str] = Field(default=None, max_length=10)
    transplant_date: Optional[date] = None


class BiopsyIn(BaseModel):
    biopsy_date: date
    outcome: str = Field(min_length=1, max_length=60)
    notes: Optional[str] = None


def patient_dict(p: Patient, n_tests=None):
    d = {"patient_id": p.patient_id, "full_name": p.full_name, "sex": p.sex,
         "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
         "transplant_date": p.transplant_date.isoformat() if p.transplant_date else None}
    if n_tests is not None:
        d["n_tests"] = n_tests
    return d


def _batch_or_404(db: Session, batch_id: int) -> UploadBatch:
    b = db.get(UploadBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Upload not found.")
    return b


def _csv_response(text: str, name: str) -> Response:
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- upload + history ----------
@app.post("/api/upload")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        batch = services.process_csv(db, content, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return services.batch_payload(batch)


@app.get("/api/template.csv")
def template():
    return _csv_response(",".join(calc.REQUIRED_COLUMNS) + "\n", "graft360_template.csv")


@app.get("/api/batches")
def list_batches(db: Session = Depends(get_db)):
    q = select(UploadBatch).order_by(UploadBatch.id.desc()).limit(200)
    return [services.batch_summary(b) for b in db.scalars(q)]


@app.get("/api/batches/{batch_id}")
def batch_by_id(batch_id: int, db: Session = Depends(get_db)):
    return services.batch_payload(_batch_or_404(db, batch_id))


@app.get("/api/batches/{batch_id}/export.csv")
def batch_export(batch_id: int, db: Session = Depends(get_db)):
    data = services.batch_payload(_batch_or_404(db, batch_id))
    return _csv_response(services.rows_to_csv(data["rows"], with_action=True), f"graft360_upload_{batch_id}.csv")


@app.get("/api/plot/scatter.png")
def scatter(batch_id: int, db: Session = Depends(get_db)):
    data = services.batch_payload(_batch_or_404(db, batch_id))
    return Response(scatter_png(data["rows"]), media_type="image/png")


# ---------- stored results: search / filter / export ----------
def _result_query(patient_id, exact, date_from, date_to, status):
    q = select(TestResult)
    pid = (patient_id or "").strip()
    if pid:
        q = q.where(TestResult.patient_id == pid) if exact else q.where(TestResult.patient_id.ilike(f"%{pid}%"))
    if date_from:
        q = q.where(TestResult.test_date >= date_from)
    if date_to:
        q = q.where(TestResult.test_date <= date_to)
    if status == "high":
        q = q.where(TestResult.status == calc.HIGH)
    elif status == "low":
        q = q.where(TestResult.status == calc.LOW)
    elif status == "invalid":
        q = q.where(TestResult.is_valid.is_(False))
    return q.order_by(TestResult.test_date.desc().nulls_last(), TestResult.id.desc())


@app.get("/api/results")
def results(patient_id: Optional[str] = None, exact: bool = False,
            date_from: Optional[date] = None, date_to: Optional[date] = None,
            status: Optional[str] = Query(None, pattern="^(high|low|invalid)$"),
            limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db)):
    q = _result_query(patient_id, exact, date_from, date_to, status)
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return {"total": total, "rows": [services.row_to_dict(r) for r in rows]}


@app.get("/api/results/export.csv")
def results_export(patient_id: Optional[str] = None, exact: bool = False,
                   date_from: Optional[date] = None, date_to: Optional[date] = None,
                   status: Optional[str] = Query(None, pattern="^(high|low|invalid)$"),
                   db: Session = Depends(get_db)):
    rows = db.scalars(_result_query(patient_id, exact, date_from, date_to, status)).all()
    return _csv_response(services.rows_to_csv([services.row_to_dict(r) for r in rows]), "graft360_results.csv")


# ---------- patients ----------
@app.get("/api/patients")
def list_patients(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(TestResult.patient_id, func.count())
                             .where(TestResult.is_valid.is_(True)).group_by(TestResult.patient_id)).all())
    return [patient_dict(p, counts.get(p.patient_id, 0))
            for p in db.scalars(select(Patient).order_by(Patient.patient_id))]


@app.post("/api/patients")
def save_patient(body: PatientIn, db: Session = Depends(get_db)):
    pid = body.patient_id.strip()
    p = db.get(Patient, pid) or Patient(patient_id=pid)
    p.full_name, p.date_of_birth = body.full_name, body.date_of_birth
    p.sex, p.transplant_date = body.sex, body.transplant_date
    db.add(p)
    db.commit()
    return patient_dict(p)


def _window(db: Session, pid: str, months: int):
    p = db.get(Patient, pid)
    if p is None:
        raise HTTPException(404, "Patient not found")
    tests = services.patient_valid_tests(db, pid)
    if tests:
        cutoff = tests[-1].test_date - timedelta(days=round(months * 30.44))
        tests = [t for t in tests if t.test_date >= cutoff]
    return p, tests


@app.get("/api/patients/{pid}")
def patient_detail(pid: str, months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)):
    p, tests = _window(db, pid, months)
    biopsies = db.scalars(select(Biopsy).where(Biopsy.patient_id == pid).order_by(Biopsy.biopsy_date)).all()
    return {
        "patient": patient_dict(p),
        "tests": [services.row_to_dict(t) for t in tests],
        "biopsies": [{"id": b.id, "biopsy_date": b.biopsy_date.isoformat(), "outcome": b.outcome,
                      "notes": b.notes} for b in biopsies],
        "interpretation": (calc.interpret(tests[-1].cm_score, tests[-1].cp_ml, tests[-1].dd_cfdna_pct,
                                          tests[-1].status) if tests else "No valid results yet."),
    }


@app.post("/api/patients/{pid}/biopsies")
def add_biopsy(pid: str, body: BiopsyIn, db: Session = Depends(get_db)):
    if db.get(Patient, pid) is None:
        raise HTTPException(404, "Patient not found")
    db.add(Biopsy(patient_id=pid, **body.model_dump()))
    db.commit()
    return {"ok": True}


@app.delete("/api/biopsies/{biopsy_id}")
def delete_biopsy(biopsy_id: int, db: Session = Depends(get_db)):
    b = db.get(Biopsy, biopsy_id)
    if b is None:
        raise HTTPException(404, "Biopsy not found")
    db.delete(b)
    db.commit()
    return {"ok": True}


@app.get("/api/patients/{pid}/trend.png")
def trend(pid: str, months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)):
    _, tests = _window(db, pid, months)
    return Response(trend_png([(t.test_date, t.cm_score) for t in tests]), media_type="image/png")


@app.get("/api/patients/{pid}/report.pdf")
def report_pdf(pid: str, months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)):
    p, tests = _window(db, pid, months)
    biopsies = db.scalars(select(Biopsy).where(Biopsy.patient_id == pid).order_by(Biopsy.biopsy_date)).all()
    pdf = build_patient_pdf(p, tests, biopsies, months)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="graft360_{pid}.pdf"'})


# ---------- frontend (must be last) ----------
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
