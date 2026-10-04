# Measurement Sheet (FastAPI + plain HTML/JS)

Feet.inches calculator: Item No, Item name, Length, Width, Height, Qty, a red Less/Loss
section, gross / less / net totals, PDF + CSV export and your last 10 saved sheets.
Type sizes as feet.inches: 3.6 = 3 ft 6 in. Height filled = volume (cu ft), blank = area (sq ft).

## Project layout
    main.py            FastAPI backend (maths, database, PDF, CSV)
    static/            index.html, style.css, app.js  (the whole UI)
    tests/test_api.py  automated tests  ->  python -m pytest tests
    Dockerfile, render.yaml, requirements.txt

## Run locally
    pip install -r requirements.txt
    uvicorn main:app --reload        # http://127.0.0.1:8000

## Free hosting option A: Render + Neon (recommended)
1. Create a free Postgres database at https://neon.tech and copy its connection string.
2. Push this folder to a GitHub repository.
3. On https://render.com choose New > Web Service, pick the repository, then set:
   Build:  pip install -r requirements.txt
   Start:  uvicorn main:app --host 0.0.0.0 --port $PORT
   Environment variable: DATABASE_URL = your Neon connection string
4. Deploy. Your site is live at https://<name>.onrender.com
Render's free disk is wiped on restart, so without DATABASE_URL your saved sheets
would disappear. With Neon they stay.

## Free hosting option B: Hugging Face Spaces (Docker)
Create a Space with the Docker SDK, upload these files, and it builds from the Dockerfile.
Set DATABASE_URL as a Space secret if you want the history to survive restarts.

## API
    POST   /api/calculate        live results for a sheet
    POST   /api/sheets           save new            PUT /api/sheets/{id}   update
    GET    /api/sheets           last 10             GET /api/sheets/{id}   open
    DELETE /api/sheets/{id}
    GET    /api/sheets/{id}/pdf  and /csv            POST /api/pdf, /api/csv  (unsaved sheet)
    GET    /api/health
