import os, tempfile
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"

from fastapi.testclient import TestClient
import main

c = TestClient(main.app)
ROWS = [("47","3.6",1),("28","13",1),("28","16.5",1),("2","3",1),("47","3.3",1),("9","1.5",1),("9","18",1),
        ("5.7","9",1),("1.8","21",1),("9","1.8",1),("2.2","9",1),("2.4","47",1),("31.9","3.9",1),
        ("26","0.8",1),("8.6","1.5",3),("1","11.2",2),("7.6","1.2",2)]
SHEET = {"title": "Test",
         "measurements": [{"item_no": str(i+1), "item_name": "x", "length": l, "width": w, "qty": q}
                          for i, (l, w, q) in enumerate(ROWS)],
         "less": [{"item_no": "L1", "item_name": "door", "length": "8", "width": "5.9", "qty": 2}]}


def test_feet_inches_totals():
    t = c.post("/api/calculate", json=SHEET).json()["totals"]["sq ft"]
    assert (t["gross"], t["less"], t["net"]) == (1763.44, 92.0, 1671.44)


def test_height_gives_volume_and_separate_totals():
    r = c.post("/api/calculate", json={"measurements": [
        {"length": "2.5", "width": "9.6", "height": "1"}, {"length": "3", "width": "3"}]}).json()
    assert r["totals"]["cu ft"]["gross"] == 22.96 and r["totals"]["sq ft"]["gross"] == 9.0


def test_any_two_dimensions_are_enough():
    r = c.post("/api/calculate", json={"measurements": [
        {"length": "3", "height": "3"}, {"width": "2", "height": "4", "qty": 2},
        {"length": "5"}]}).json()
    assert r["measurements"][0]["result"] == 9.0 and r["measurements"][0]["unit"] == "sq ft"
    assert r["measurements"][1]["result"] == 16.0
    assert "at least two" in r["measurements"][2]["error"]


def test_details_round_trip_and_pdf_header():
    s = {**SHEET, "contractor": "ABC Builders", "po_no": "PO-77", "date": "2026-10-04"}
    sid = c.post("/api/sheets", json=s).json()["id"]
    assert c.get(f"/api/sheets/{sid}").json()["contractor"] == "ABC Builders"


def test_validation():
    r = c.post("/api/calculate", json={"measurements": [{"length": "3.14", "width": "2"}]}).json()
    assert "below 12" in r["measurements"][0]["error"]
    assert c.post("/api/sheets", json={"measurements": [{"length": "3.14", "width": "2"}]}).status_code == 400
    assert c.post("/api/sheets", json={"measurements": []}).status_code == 400


def test_save_update_open_export_delete():
    sid = c.post("/api/sheets", json=SHEET).json()["id"]
    assert c.get(f"/api/sheets/{sid}").json()["title"] == "Test"
    assert c.put(f"/api/sheets/{sid}", json={**SHEET, "title": "Renamed"}).status_code == 200
    assert c.get(f"/api/sheets/{sid}").json()["title"] == "Renamed"
    assert c.put("/api/sheets/99999", json=SHEET).status_code == 404
    assert c.get(f"/api/sheets/{sid}/pdf").content[:4] == b"%PDF"
    assert b"Net total" in c.get(f"/api/sheets/{sid}/csv").content
    assert c.post("/api/pdf", json=SHEET).content[:4] == b"%PDF"
    assert c.delete(f"/api/sheets/{sid}").json() == {"ok": True}
    assert c.get(f"/api/sheets/{sid}").status_code == 404


def test_only_last_10_kept_and_updates_refresh_order():
    for i in range(12):
        c.post("/api/sheets", json={**SHEET, "title": f"S{i}"})
    lst = c.get("/api/sheets").json()
    assert len(lst) == 10 and lst[0]["title"] == "S11" and lst[-1]["title"] == "S2"
    oldest = lst[-1]["id"]
    c.put(f"/api/sheets/{oldest}", json={**SHEET, "title": "Touched"})
    assert c.get("/api/sheets").json()[0]["title"] == "Touched"


def test_csv_formula_injection_blocked():
    s = {"measurements": [{"item_name": "=HYPERLINK(1)", "length": "1", "width": "1"}]}
    assert b"'=HYPERLINK" in c.post("/api/csv", json=s).content


def test_pages():
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/api/health").json() == {"status": "ok"}