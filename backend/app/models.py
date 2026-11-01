"""Normalized schema:
patients 1--* test_results, patients 1--* biopsies, upload_batches 1--* test_results.
A patient can have only ONE result per test date (unique key) -> re-uploading the same CSV
never creates duplicate records."""
from datetime import datetime, date

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(150))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(10))
    transplant_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tests = relationship("TestResult", back_populates="patient", cascade="all, delete-orphan")
    biopsies = relationship("Biopsy", back_populates="patient", cascade="all, delete-orphan")


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(30), default="Processing")
    message: Mapped[str | None] = mapped_column(Text)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    new_rows: Mapped[int] = mapped_column(Integer, default=0)
    updated_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    high_rows: Mapped[int] = mapped_column(Integer, default=0)
    low_rows: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[str | None] = mapped_column(Text)   # JSON of the processed file rows

    results = relationship("TestResult", back_populates="batch")


class TestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (
        UniqueConstraint("patient_id", "test_date", name="uq_test_patient_date"),
        Index("ix_test_results_patient_date", "patient_id", "test_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batches.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.patient_id", ondelete="CASCADE"))
    test_date: Mapped[date | None] = mapped_column(Date)
    row_hash: Mapped[str | None] = mapped_column(String(40), index=True)

    c_rxn_copies_ul: Mapped[float | None] = mapped_column(Float)
    v_rxn_ul: Mapped[float | None] = mapped_column(Float)
    v_template_ul: Mapped[float | None] = mapped_column(Float)
    v_elution_ul: Mapped[float | None] = mapped_column(Float)
    v_plasma_ml: Mapped[float | None] = mapped_column(Float)
    zygosity_z: Mapped[float | None] = mapped_column(Float)
    dd_cfdna_pct: Mapped[float | None] = mapped_column(Float)

    cp_ml: Mapped[float | None] = mapped_column(Float)
    eta: Mapped[float | None] = mapped_column(Float)
    prob: Mapped[float | None] = mapped_column(Float)
    cm_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))          # High Risk / Low Risk / Invalid
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="tests")
    batch = relationship("UploadBatch", back_populates="results")


class Biopsy(Base):
    __tablename__ = "biopsies"
    __table_args__ = (Index("ix_biopsies_patient_date", "patient_id", "biopsy_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id", ondelete="CASCADE"))
    biopsy_date: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(String(60))          # e.g. No rejection / ABMR / TCMR / Borderline
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="biopsies")
