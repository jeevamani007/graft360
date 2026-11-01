"""Clinician PDF: Matplotlib chart -> in-memory PNG buffer -> embedded with ReportLab."""
import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import calculations as calc
from .plots import trend_png


def build_patient_pdf(patient, tests, biopsies, months: int) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Graft 360 report {patient.patient_id}")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], alignment=0, fontSize=18, textColor="#14213D")
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor="#14213D", spaceBefore=10)
    body = ss["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)

    el = [Paragraph("Graft 360 - Patient CM-Score Report", h1),
          Paragraph(f"Generated {date.today().isoformat()} - monitoring window: last {months} months", small),
          Spacer(1, 6)]

    demo = [["Patient ID", patient.patient_id, "Name", patient.full_name or "-"],
            ["Date of birth", str(patient.date_of_birth or "-"), "Sex", patient.sex or "-"],
            ["Transplant date", str(patient.transplant_date or "-"), "Tests in window", str(len(tests))]]
    t = Table(demo, colWidths=[30 * mm, 50 * mm, 30 * mm, 50 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    el += [t, Paragraph("CM-Score trend", h2)]

    png = trend_png([(x.test_date, x.cm_score) for x in tests])
    el.append(Image(io.BytesIO(png), width=170 * mm, height=170 * mm * 3.8 / 7.5))

    el.append(Paragraph("Results", h2))
    rows = [["Date", "dd-cfDNA %", "cp_mL", "Prob", "CM-Score", "Status"]]
    for x in tests:
        rows.append([x.test_date.isoformat(), f"{x.dd_cfdna_pct:.2f}", f"{x.cp_ml:.2f}",
                     f"{x.prob:.2f}", f"{x.cm_score:.2f}", x.status])
    rt = Table(rows, repeatRows=1)
    style = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14213D")),
             ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]
    for i, x in enumerate(tests, start=1):
        style.append(("BACKGROUND", (0, i), (-1, i),
                      colors.HexColor("#FDECEA") if x.status == calc.HIGH else colors.HexColor("#E8F5EE")))
    rt.setStyle(TableStyle(style))
    el.append(rt)

    if tests:
        last = tests[-1]
        el += [Paragraph("Interpretation of latest result (rule-based, generated offline)", h2),
               Paragraph(calc.interpret(last.cm_score, last.cp_ml, last.dd_cfdna_pct, last.status), body)]

    if biopsies:
        el.append(Paragraph("Biopsy outcomes", h2))
        br = [["Date", "Outcome", "Notes"]] + [[b.biopsy_date.isoformat(), b.outcome, b.notes or ""] for b in biopsies]
        bt = Table(br, colWidths=[28 * mm, 45 * mm, 100 * mm], repeatRows=1)
        bt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB"))]))
        el.append(bt)

    el += [Spacer(1, 10), Paragraph("Decision-support output; interpret together with clinical findings.", small)]
    doc.build(el)
    return buf.getvalue()
