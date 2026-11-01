"""Pure calculation + validation logic (no database, no web) so it is unit-testable.

Formulas (from the PDF):
  cp_mL    = C_rxn * (V_rxn / V_template) * (V_elution / V_plasma) * Z
  eta      = -2.85 + 2.10 * dd_cfDNA_pct + 0.025 * cp_mL
  prob     = 1 / (1 + e^-eta)
  CM_Score = prob / 0.3318 - 1.0        -> High Risk if >= 0 else Low Risk
"""
import math
from datetime import datetime

from . import config

HIGH = "High Risk (Positive)"
LOW = "Low Risk (Negative)"
INVALID = "Invalid"

NUMERIC_FIELDS = ["C_rxn_copies_uL", "V_rxn_uL", "V_template_uL", "V_elution_uL",
                  "V_plasma_mL", "Zygosity_Z", "dd_cfDNA_pct"]
REQUIRED_COLUMNS = ["Patient_ID", "Test_Date"] + NUMERIC_FIELDS
VOLUME_FIELDS = ["V_rxn_uL", "V_template_uL", "V_elution_uL", "V_plasma_mL"]
MISSING_TOKENS = {"", "nan", "null", "none", "na", "n/a", "-"}


def sigmoid(eta: float) -> float:
    """Overflow-safe logistic function."""
    if eta >= 0:
        return 1.0 / (1.0 + math.exp(-eta))
    z = math.exp(eta)
    return z / (1.0 + z)


def compute_cp_ml(c_rxn, v_rxn, v_template, v_elution, v_plasma, zygosity) -> float:
    if v_template <= 0 or v_plasma <= 0:
        raise ValueError("Volumes used as divisors must be greater than 0")
    return c_rxn * (v_rxn / v_template) * (v_elution / v_plasma) * zygosity


def compute_scores(cp_ml: float, dd_pct: float) -> dict:
    eta = config.INTERCEPT + config.COEF_DD * dd_pct + config.COEF_CP * cp_ml
    prob = sigmoid(eta)
    cm = prob / config.T_OPT - 1.0
    return {"eta": eta, "prob": prob, "cm_score": cm, "status": HIGH if cm >= 0 else LOW}


def _parse_number(value):
    """Returns (number|None, problem|None)."""
    if value is None:
        return None, "missing"
    s = str(value).strip()
    if s.lower() in MISSING_TOKENS:
        return None, "missing"
    try:
        x = float(s)
    except ValueError:
        return None, f"not a number ('{s}')"
    if math.isnan(x) or math.isinf(x):
        return None, "not a finite number"
    return x, None


def parse_date(value):
    s = str(value).strip() if value is not None else ""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def process_row(raw: dict) -> dict:
    """Validate one CSV row and compute results. Never raises on bad data."""
    errors = []
    out = {
        "patient_id": (str(raw.get("Patient_ID") or "").strip() or None),
        "test_date": parse_date(raw.get("Test_Date")),
        "cp_ml": None, "eta": None, "prob": None, "cm_score": None,
    }
    if out["patient_id"] is None:
        errors.append("Patient_ID is missing")
    if out["test_date"] is None:
        errors.append("Test_Date is missing or not a valid date")

    vals = {}
    for f in NUMERIC_FIELDS:
        v, problem = _parse_number(raw.get(f))
        vals[f] = v
        if problem:
            errors.append(f"{f} is {problem}")

    for f in VOLUME_FIELDS:
        if vals[f] is not None and vals[f] <= 0:
            errors.append(f"{f} must be greater than 0 (got {vals[f]:g})")
    if vals["C_rxn_copies_uL"] is not None and vals["C_rxn_copies_uL"] < 0:
        errors.append("C_rxn_copies_uL cannot be negative")
    if vals["Zygosity_Z"] is not None and vals["Zygosity_Z"] not in (1, 2):
        errors.append(f"Zygosity_Z must be 1 or 2 (got {vals['Zygosity_Z']:g})")
    dd = vals["dd_cfDNA_pct"]
    if dd is not None and not (0 <= dd <= 100):
        errors.append(f"dd_cfDNA_pct must be between 0 and 100 (got {dd:g})")

    out.update({
        "c_rxn_copies_ul": vals["C_rxn_copies_uL"], "v_rxn_ul": vals["V_rxn_uL"],
        "v_template_ul": vals["V_template_uL"], "v_elution_ul": vals["V_elution_uL"],
        "v_plasma_ml": vals["V_plasma_mL"], "zygosity_z": vals["Zygosity_Z"],
        "dd_cfdna_pct": vals["dd_cfDNA_pct"],
    })

    if errors:
        out.update(is_valid=False, status=INVALID, error_message="; ".join(errors))
        return out

    cp = compute_cp_ml(vals["C_rxn_copies_uL"], vals["V_rxn_uL"], vals["V_template_uL"],
                       vals["V_elution_uL"], vals["V_plasma_mL"], vals["Zygosity_Z"])
    out.update(cp_ml=cp, **compute_scores(cp, dd))
    out.update(is_valid=True, error_message=None)
    return out


def interpret(cm_score, cp_ml, dd_pct, status) -> str:
    """Deterministic, rule-based clinical text. Runs 100% offline - no external APIs."""
    if cm_score is None:
        return "No interpretation available: this result is invalid. Correct the input data and re-upload."
    if status == HIGH:
        text = (f"CM-Score {cm_score:.2f} is at or above the 0.0 threshold (positive). "
                "Predicted rejection probability exceeds the reference cut-off, indicating elevated "
                "risk of allograft rejection. Correlate with clinical findings and consider biopsy confirmation.")
    else:
        text = (f"CM-Score {cm_score:.2f} is below the 0.0 threshold (negative). "
                "Predicted rejection probability is under the reference cut-off, consistent with "
                "low risk of allograft rejection. Continue routine serial monitoring.")
    notes = []
    dd_high = dd_pct is not None and dd_pct >= config.DD_REF_PCT
    cp_high = cp_ml is not None and cp_ml >= config.CP_REF
    if dd_high and cp_high:
        notes.append("Both dd-cfDNA (>= 0.50%) and plasma copies (>= 44 cp/mL) are above their reference lines.")
    elif dd_high:
        notes.append("dd-cfDNA is at or above 0.50% while plasma copies are below 44 cp/mL.")
    elif cp_high:
        notes.append("Plasma copies are at or above 44 cp/mL while dd-cfDNA is below 0.50%.")
    return text + (" " + " ".join(notes) if notes else "")
