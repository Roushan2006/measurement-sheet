"""Measurement Sheet - FastAPI backend.

Run locally:  uvicorn main:app --reload
Storage:      SQLite by default; set DATABASE_URL for Postgres (Neon / Supabase).
Keeps only the 10 most recently saved/updated sheets.
"""
import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import (Column, Integer, MetaData, String, Table as SATable, Text,
                        create_engine, delete, desc, insert, select, update)

BASE = Path(__file__).parent
KEEP = 10

# ------------------------------------------------------------------ database
db_url = os.getenv("DATABASE_URL") or f"sqlite:///{BASE / 'measurements.db'}"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
engine = create_engine(
    db_url, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
)
meta = MetaData()
sheets = SATable(
    "sheets", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String(200)),
    Column("created_at", String(40)),
    Column("updated_at", String(40)),
    Column("payload", Text),
    Column("summary", Text),
)
meta.create_all(engine)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prune(conn):
    """Keep only the KEEP most recently updated sheets."""
    keep = [r.id for r in conn.execute(
        select(sheets.c.id).order_by(desc(sheets.c.updated_at), desc(sheets.c.id)).limit(KEEP))]
    conn.execute(delete(sheets).where(sheets.c.id.not_in(keep)))


# ------------------------------------------------------------------ models
class RowIn(BaseModel):
    item_no: str = Field("", max_length=30)
    item_name: str = Field("", max_length=100)
    length: str = Field("", max_length=12)
    width: str = Field("", max_length=12)
    height: str = Field("", max_length=12)
    qty: int = Field(1, ge=1, le=100000)
    remark: str = Field("", max_length=100)


class SheetIn(BaseModel):
    title: str = Field("", max_length=200)
    contractor: str = Field("", max_length=100)
    po_no: str = Field("", max_length=60)
    sheet_item_no: str = Field("", max_length=60)
    civil_int: str = Field("", max_length=100)
    rab_no: str = Field("", max_length=60)
    jms_no: str = Field("", max_length=60)
    date: str = Field("", max_length=20)
    measurements: list[RowIn] = Field(default_factory=list, max_length=300)
    less: list[RowIn] = Field(default_factory=list, max_length=300)


# ------------------------------------------------------------------ maths
def parse(text: str, field: str):
    """'3.6' -> 3 ft 6 in. Returns (feet_as_float, label); (None, '') if blank."""
    s = (text or "").strip()
    if not s:
        return None, ""
    try:
        if "." in s:
            f, i = s.split(".", 1)
            feet, inch = int(f or 0), int(i or 0)
        else:
            feet, inch = int(s), 0
    except ValueError:
        raise ValueError(f"{field} '{s}' is not a valid number")
    if feet < 0 or inch < 0:
        raise ValueError(f"{field} cannot be negative")
    if inch >= 12:
        raise ValueError(f"{field} '{s}': inches must be below 12")
    return feet + inch / 12, (f"{feet}' {inch}\"" if inch else f"{feet}'")


def calc_rows(rows):
    """Any two of Length / Width / Height -> area (sq ft); all three -> volume (cu ft)."""
    out = []
    for r in rows:
        d = {"item_no": r.item_no, "item_name": r.item_name, "qty": r.qty, "remark": r.remark,
             "ok": False, "error": None, "result": None, "unit": None,
             "l_label": "", "w_label": "", "h_label": ""}
        try:
            l, d["l_label"] = parse(r.length, "Length")
            w, d["w_label"] = parse(r.width, "Width")
            h, d["h_label"] = parse(r.height, "Height")
        except ValueError as e:
            d["error"] = str(e)
            out.append(d)
            continue
        dims = [x for x in (l, w, h) if x is not None]
        if not dims:
            out.append(d)  # a blank row is silently skipped
            continue
        if len(dims) < 2:
            d["error"] = "Enter at least two of Length, Width, Height"
            out.append(d)
            continue
        prod = 1.0
        for x in dims:
            prod *= x
        d["result"] = prod * r.qty
        d["unit"] = "sq ft" if len(dims) == 2 else "cu ft"
        d["ok"] = True
        out.append(d)
    return out


def totals(meas, less):
    res = {}
    for unit in ("sq ft", "cu ft"):
        m = [r["result"] for r in meas if r["ok"] and r["unit"] == unit]
        l = [r["result"] for r in less if r["ok"] and r["unit"] == unit]
        if m or l:
            g, ls = sum(m), sum(l)
            res[unit] = {"gross": round(g, 2), "less": round(ls, 2), "net": round(g - ls, 2)}
    return res


DETAIL_KEYS = ("contractor", "po_no", "sheet_item_no", "civil_int", "rab_no", "jms_no", "date")


def evaluate(sheet: SheetIn):
    meas, less = calc_rows(sheet.measurements), calc_rows(sheet.less)
    details = {k: getattr(sheet, k) for k in DETAIL_KEYS}
    return {"title": sheet.title, "details": details, "measurements": meas, "less": less,
            "totals": totals(meas, less)}


def validate_for_output(ev):
    if any(r["error"] for r in ev["measurements"] + ev["less"]):
        raise HTTPException(400, "Fix the highlighted rows first.")
    if not any(r["ok"] for r in ev["measurements"]):
        raise HTTPException(400, "Add at least one measurement first.")


# ------------------------------------------------------------------ exports
def fmt_date(v: str) -> str:
    try:
        return datetime.strptime(v, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return v


def make_pdf(ev, created=None) -> bytes:
    """A4 measurement sheet laid out like the standard form:
    title, contractor / PO / item / civil block, then S.No | Item | Unit | Nos | Length | Breadth | Height | Quantity | Remark."""
    from xml.sax.saxutils import escape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    d = ev.get("details", {})
    W = [35, 140, 38, 34, 56, 56, 56, 60, 60]
    black = colors.black
    cell = ParagraphStyle("c", fontName="Times-Roman", fontSize=9, leading=10.5, alignment=TA_LEFT)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Times-Bold")
    P = lambda t, st=cell: Paragraph(escape(str(t)), st)

    rows, style = [], [
        ("GRID", (0, 0), (-1, -1), 0.7, black),
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]

    def add(row, spans=(), bold=False, bg=None, fg=None, align=None):
        i = len(rows)
        rows.append(row)
        for a, b in spans:
            style.append(("SPAN", (a, i), (b, i)))
        if bold:
            style.append(("FONTNAME", (0, i), (-1, i), "Times-Bold"))
        if bg:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(bg)))
        if fg:
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(fg)))
        return i

    blank = [""] * 9
    add(["Measurement Sheet"] + [""] * 8, [(0, 8)], bold=True)
    style.append(("ALIGN", (0, 0), (-1, 0), "CENTER"))
    add([P("Contractor :  " + d.get("contractor", "")), *[""] * 5, P("RAB. No. :  " + d.get("rab_no", "")), "", ""], [(0, 5), (6, 8)])
    add([P("P.O. No. :  " + d.get("po_no", "")), *[""] * 5, P("JMS. No. :  " + d.get("jms_no", "")), "", ""], [(0, 5), (6, 8)])
    add([P("Item No. :  " + d.get("sheet_item_no", "")), *[""] * 5, P("Date :  " + fmt_date(d.get("date", ""))), "", ""], [(0, 5), (6, 8)])
    add([P("Civil Int. -  " + d.get("civil_int", ""))] + [""] * 8, [(0, 8)])
    head = add(["S.No.", "Item", "Unit", "Nos.", "Length", "Breadth", "Height", "Quantity", "Remark"], bold=True, bg="#eeeeee")
    style.append(("ALIGN", (0, head), (-1, head), "CENTER"))
    body_start = len(rows)

    def line(r, neg=False):
        q = f"{'- ' if neg else ''}{r['result']:,.2f}"
        return [r["item_no"], P(r["item_name"]), r["unit"], r["qty"], r["l_label"], r["w_label"], r["h_label"], q, P(r["remark"])]

    meas = [r for r in ev["measurements"] if r["ok"]]
    for r in meas:
        add(line(r))
    n_units = len(ev["totals"])
    less_ok = [r for r in ev["less"] if r["ok"]]
    extra = n_units + ((1 + len(less_ok) + 2 * n_units) if less_ok else 0)  # total / LESS / net lines below
    for _ in range(max(0, 34 - len(meas) - extra)):  # blank form lines so one page looks like the printed sheet
        add(list(blank))

    for unit, t in ev["totals"].items():
        i = add([f"Total ({unit})", "", "", "", "", "", "", f"{t['gross']:,.2f}", ""], [(0, 6)], bold=True, bg="#dbe7f5")
        style.append(("ALIGN", (0, i), (0, i), "CENTER"))

    less_rows = [r for r in ev["less"] if r["ok"]]
    if less_rows:
        i = add(["LESS"] + [""] * 8, [(0, 8)], bold=True, bg="#ffb3b3", fg="#b00020")
        style.append(("ALIGN", (0, i), (0, i), "CENTER"))
        for r in less_rows:
            add(line(r, neg=True), bg="#ffd6d6", fg="#b00020")
        for unit, t in ev["totals"].items():
            if t["less"]:
                i = add([f"Total Less ({unit})", "", "", "", "", "", "", f"- {t['less']:,.2f}", ""], [(0, 6)], bold=True, bg="#ffd6d6", fg="#b00020")
                style.append(("ALIGN", (0, i), (0, i), "CENTER"))
        for unit, t in ev["totals"].items():
            i = add([f"Net Total ({unit})", "", "", "", "", "", "", f"{t['net']:,.2f}", ""], [(0, 6)], bold=True, bg="#c8f0c8")
            style.append(("ALIGN", (0, i), (0, i), "CENTER"))

    n = len(rows)
    style += [("ALIGN", (0, body_start), (0, n - 1), "CENTER"), ("ALIGN", (2, body_start), (6, n - 1), "CENTER"),
              ("ALIGN", (7, body_start), (7, n - 1), "RIGHT")]
    table = Table(rows, colWidths=W, repeatRows=head + 1)
    table.setStyle(TableStyle(style))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=28, bottomMargin=28,
                            title=ev["title"] or "Measurement Sheet")
    doc.build([table])
    return buf.getvalue()


def make_csv(ev) -> bytes:
    def safe(v):  # block spreadsheet formula injection
        s = str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") else s

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Type", "S.No", "Item", "Unit", "Nos", "Length", "Breadth", "Height", "Quantity", "Remark"])
    for typ, rows in (("Measurement", ev["measurements"]), ("Less", ev["less"])):
        for r in rows:
            if r["ok"]:
                w.writerow([typ, safe(r["item_no"]), safe(r["item_name"]), r["unit"], r["qty"], r["l_label"], r["w_label"],
                            r["h_label"], ("-" if typ == "Less" else "") + f"{r['result']:.2f}", safe(r["remark"])])
    w.writerow([])
    for unit, t in ev["totals"].items():
        w.writerow(["Total", "", "", unit, "", "", "", "", f"{t['gross']:.2f}"])
        w.writerow(["Total less", "", "", unit, "", "", "", "", f"-{t['less']:.2f}"])
        w.writerow(["Net total", "", "", unit, "", "", "", "", f"{t['net']:.2f}"])
    return ("\ufeff" + out.getvalue()).encode("utf-8")  # BOM so Excel reads it correctly


def file_response(content: bytes, media: str, title: str, ext: str):
    name = "".join(c for c in (title or "measurement_sheet") if c.isalnum() or c in " _-").strip() or "sheet"
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'})


# ------------------------------------------------------------------ API
app = FastAPI(title="Measurement Sheet")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/calculate")
def calculate(sheet: SheetIn):
    return evaluate(sheet)


@app.post("/api/pdf")
def pdf_unsaved(sheet: SheetIn):
    ev = evaluate(sheet)
    validate_for_output(ev)
    return file_response(make_pdf(ev, datetime.now().strftime("%d %b %Y")), "application/pdf", sheet.title, "pdf")


@app.post("/api/csv")
def csv_unsaved(sheet: SheetIn):
    ev = evaluate(sheet)
    validate_for_output(ev)
    return file_response(make_csv(ev), "text/csv; charset=utf-8", sheet.title, "csv")


def _prepare(sheet: SheetIn):
    ev = evaluate(sheet)
    validate_for_output(ev)
    return sheet.title.strip() or "Untitled sheet", ev


@app.post("/api/sheets")
def create_sheet(sheet: SheetIn):
    title, ev = _prepare(sheet)
    ts = now()
    with engine.begin() as conn:
        res = conn.execute(insert(sheets).values(
            title=title, created_at=ts, updated_at=ts,
            payload=json.dumps(sheet.model_dump()), summary=json.dumps(ev["totals"])))
        new_id = res.inserted_primary_key[0]
        prune(conn)
    return {"id": new_id}


@app.put("/api/sheets/{sheet_id}")
def update_sheet(sheet_id: int, sheet: SheetIn):
    title, ev = _prepare(sheet)
    with engine.begin() as conn:
        res = conn.execute(update(sheets).where(sheets.c.id == sheet_id).values(
            title=title, updated_at=now(),
            payload=json.dumps(sheet.model_dump()), summary=json.dumps(ev["totals"])))
        if res.rowcount == 0:
            raise HTTPException(404, "Sheet not found")
    return {"id": sheet_id}


@app.get("/api/sheets")
def list_sheets():
    with engine.connect() as conn:
        rows = conn.execute(select(sheets).order_by(desc(sheets.c.updated_at), desc(sheets.c.id)).limit(KEEP)).all()
    out = []
    for r in rows:
        ev = evaluate(SheetIn(**json.loads(r.payload)))
        out.append({"id": r.id, "title": r.title, "updated_at": r.updated_at, "totals": json.loads(r.summary),
                    "items": sum(1 for m in ev["measurements"] if m["ok"])})
    return out


def _get(sheet_id: int):
    with engine.connect() as conn:
        row = conn.execute(select(sheets).where(sheets.c.id == sheet_id)).first()
    if not row:
        raise HTTPException(404, "Sheet not found")
    return row


@app.get("/api/sheets/{sheet_id}")
def get_sheet(sheet_id: int):
    row = _get(sheet_id)
    return {"id": row.id, **json.loads(row.payload)}


@app.get("/api/sheets/{sheet_id}/pdf")
def sheet_pdf(sheet_id: int):
    row = _get(sheet_id)
    ev = evaluate(SheetIn(**json.loads(row.payload)))
    ev["title"] = ev["title"] or row.title
    created = datetime.fromisoformat(row.updated_at).strftime("%d %b %Y")
    return file_response(make_pdf(ev, created), "application/pdf", ev["title"], "pdf")


@app.get("/api/sheets/{sheet_id}/csv")
def sheet_csv(sheet_id: int):
    row = _get(sheet_id)
    ev = evaluate(SheetIn(**json.loads(row.payload)))
    return file_response(make_csv(ev), "text/csv; charset=utf-8", ev["title"] or row.title, "csv")


@app.delete("/api/sheets/{sheet_id}")
def delete_sheet(sheet_id: int):
    with engine.begin() as conn:
        conn.execute(delete(sheets).where(sheets.c.id == sheet_id))
    return {"ok": True}