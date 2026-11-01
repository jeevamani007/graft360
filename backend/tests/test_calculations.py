"""Unit tests against hand-calculated reference values (PDF sample data)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app import calculations as c  # noqa: E402


def row(**kw):
    base = {"Patient_ID": "PT-1", "Test_Date": "2026-10-01", "C_rxn_copies_uL": "0.25", "V_rxn_uL": "20",
            "V_template_uL": "8", "V_elution_uL": "60", "V_plasma_mL": "2", "Zygosity_Z": "1",
            "dd_cfDNA_pct": "0.28"}
    base.update(kw)
    return base


def test_pt101_hand_calculated():
    r = c.process_row(row())
    assert r["is_valid"]
    assert math.isclose(r["cp_ml"], 18.75)                      # 0.25*2.5*30*1
    assert math.isclose(r["eta"], -2.85 + 2.10 * 0.28 + 0.025 * 18.75)   # -1.79325
    assert math.isclose(r["prob"], 1 / (1 + math.exp(1.79325)))
    assert r["status"] == c.LOW and r["cm_score"] < 0


def test_pt104_zygosity_two_high_risk():
    r = c.process_row(row(Patient_ID="PT-104", C_rxn_copies_uL="0.72", V_plasma_mL="1.5",
                          Zygosity_Z="2", dd_cfDNA_pct="1.90"))
    assert math.isclose(r["cp_ml"], 0.72 * 2.5 * 40 * 2)         # 144
    assert r["status"] == c.HIGH and r["cm_score"] >= 0


def test_zero_plasma_volume_is_invalid_not_crash():
    r = c.process_row(row(V_plasma_mL="0.0"))
    assert not r["is_valid"] and r["status"] == c.INVALID
    assert r["cp_ml"] is None and "V_plasma_mL" in r["error_message"]


def test_blank_nan_and_text_values():
    for bad in ("", "NaN", "abc", "-1"):
        r = c.process_row(row(V_plasma_mL=bad))
        assert not r["is_valid"]


def test_bad_zygosity_and_date():
    assert not c.process_row(row(Zygosity_Z="3"))["is_valid"]
    assert not c.process_row(row(Test_Date="not-a-date"))["is_valid"]


def test_sigmoid_no_overflow():
    assert c.sigmoid(-5000) == 0.0 or c.sigmoid(-5000) < 1e-300
    assert c.sigmoid(5000) == 1.0
