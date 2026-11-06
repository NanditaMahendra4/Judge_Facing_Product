"""Bench Brief web app (Starlette). Run: ``python -m benchbrief serve``."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import date
from collections import defaultdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from . import ingest, pipeline, store
from .schema import SECTIONS, Brief

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["anchor"] = lambda sid: sid.replace(".", "-")
templates.env.filters["pg"] = lambda sid: f"p.{sid.split('.')[0][1:]}"
templates.env.filters["fmt_date"] = pipeline.fmt_date


MAX_UPLOAD = 15 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".txt"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

APP_JS = """(function(){
var t0=Date.now();
document.querySelectorAll("form[data-timer]").forEach(function(f){
  f.addEventListener("submit",function(){f.elements.seconds.value=((Date.now()-t0)/1000).toFixed(0)});
});
document.querySelectorAll("[data-print]").forEach(function(b){b.addEventListener("click",function(){window.print()})});
var hl=new URLSearchParams(location.search).get("hl");
if(hl){hl.split(",").forEach(function(id){
  if(!/^p\\d+\\.s\\d+$/.test(id))return;
  var el=document.getElementById(id.replace(".","-"));
  if(el)el.classList.add("hl");
})}
})();
"""


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        path = request.url.path
        if path.startswith("/filing/") or path.startswith("/case/") or path in ("/", "/metrics"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


async def app_js(request: Request):
    return Response(APP_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


async def docket(request: Request):
    with store.connect() as conn:
        cases = conn.execute(
            "SELECT c.*, COUNT(f.id) AS n FROM cases c LEFT JOIN filings f ON f.case_id=c.id GROUP BY c.id ORDER BY c.number"
        ).fetchall()
    return templates.TemplateResponse(request, "docket.html", {"cases": cases})


async def case_view(request: Request):
    cid = request.path_params["cid"]
    with store.connect() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
        if not case:
            return Response("Case not found", 404)
        rows = conn.execute("SELECT * FROM filings WHERE case_id=? ORDER BY filed_on, id", (cid,)).fetchall()
        filings = []
        for r in rows:
            note, prior_ids = pipeline.repeat_note(conn, r["id"])
            b = Brief.model_validate_json(r["brief_json"]) if r["brief_json"] else None
            filings.append({"row": r, "brief": b, "repeat": note, "prior_ids": prior_ids})
        # Thread repeats: each filing joins the thread of its earliest matched prior.
        thread_of: dict[int, int] = {}
        for f in filings:
            root = thread_of.get(f["prior_ids"][0], f["prior_ids"][0]) if f["prior_ids"] else f["row"]["id"]
            thread_of[f["row"]["id"]] = root
        threads: dict[int, list] = defaultdict(list)
        for f in filings:
            threads[thread_of[f["row"]["id"]]].append(f)
    return templates.TemplateResponse(request, "case.html", {"case": case, "threads": list(threads.values())})


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


async def upload(request: Request):
    cid = request.path_params["cid"]
    with store.connect() as conn:
        if not conn.execute("SELECT 1 FROM cases WHERE id=?", (cid,)).fetchone():
            return Response("Case not found", 404)
    form = await request.form()
    up = form.get("file")
    text = (form.get("text") or "").strip()
    filed_on = (form.get("filed_on") or "").strip()
    filer = (form.get("filer") or "").strip() or None
    title = (form.get("title") or "").strip()
    if not _ISO_DATE.match(filed_on):
        return Response("Filed-on date must be an ISO date (YYYY-MM-DD).", 400)
    try:
        date.fromisoformat(filed_on)
    except ValueError:
        return Response("Filed-on date is not a real calendar date (use YYYY-MM-DD).", 400)
    try:
        if up is not None and getattr(up, "filename", ""):
            suffix = Path(up.filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                return Response("Only .pdf and .txt files are accepted.", 415)
            data = await up.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                return Response("File is larger than the 15 MB limit.", 413)
            stem = title or Path(up.filename).stem
            if suffix == ".txt":
                doc = ingest.from_text(stem, _decode_text(data))
            else:
                with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                    tmp.write(data)
                    tmp.flush()
                    try:
                        doc = ingest.load(tmp.name, stem)
                    except ValueError:
                        raise
                    except Exception as exc:  # corrupt/unreadable PDF
                        raise ValueError("Could not read this PDF.") from exc
        elif text:
            doc = ingest.from_text(title or "Pasted filing", text)
        else:
            return Response("Attach a file or paste the filing text.", 400)
    except ValueError as exc:
        return Response(str(exc), 422)
    with store.connect() as conn:
        fid = pipeline.file_and_brief(conn, cid, doc.title, filed_on, doc, filer=filer)
    return RedirectResponse(f"/filing/{fid}", 303)


async def toggle(request: Request):
    cid = request.path_params["cid"]
    form = await request.form()
    with store.connect() as conn:
        if not conn.execute("SELECT 1 FROM cases WHERE id=?", (cid,)).fetchone():
            return Response("Case not found", 404)
        store.set_enabled(conn, cid, form.get("enabled") == "1")
    return RedirectResponse(f"/case/{cid}", 303)


def _load_filing(conn, fid):
    row = conn.execute(
        "SELECT f.*, c.number, c.caption, c.brief_enabled FROM filings f JOIN cases c ON c.id=f.case_id WHERE f.id=?", (fid,)
    ).fetchone()
    return row


async def brief_view(request: Request):
    fid = int(request.path_params["fid"])
    with store.connect() as conn:
        row = _load_filing(conn, fid)
        if not row:
            return Response("Filing not found", 404)
        if not row["brief_enabled"]:
            return RedirectResponse(f"/filing/{fid}/source", 303)
        note, prior_ids = pipeline.repeat_note(conn, fid)
        priors = [conn.execute("SELECT id, title, filed_on FROM filings WHERE id=?", (p,)).fetchone() for p in prior_ids]
        failed = conn.execute(
            "SELECT detail FROM audit WHERE filing_id=? AND event='brief_failed' ORDER BY id DESC LIMIT 1", (fid,)
        ).fetchone()
        store.audit(conn, "brief_viewed", row["case_id"], fid)
    brief = Brief.model_validate_json(row["brief_json"]) if row["brief_json"] else None
    guard = json.loads(row["guard_json"]) if row["guard_json"] else None
    doc = pipeline.doc_from_json(row["doc_json"])
    return templates.TemplateResponse(request, "brief.html", {
        "f": row, "brief": brief, "guard": guard, "sections": SECTIONS, "repeat": note, "priors": priors,
        "pages": len(doc.pages), "failed": json.loads(failed["detail"]) if failed and not brief else None,
    })


async def source_view(request: Request):
    fid = int(request.path_params["fid"])
    with store.connect() as conn:
        row = _load_filing(conn, fid)
        if not row:
            return Response("Filing not found", 404)
        store.audit(conn, "source_viewed", row["case_id"], fid)
    doc = pipeline.doc_from_json(row["doc_json"])
    return templates.TemplateResponse(request, "source.html", {"f": row, "doc": doc})


async def regenerate(request: Request):
    fid = int(request.path_params["fid"])
    with store.connect() as conn:
        if not _load_filing(conn, fid):
            return Response("Filing not found", 404)
        pipeline.generate(conn, fid)
    return RedirectResponse(f"/filing/{fid}", 303)


async def review_done(request: Request):
    """Pilot measurement: the judge's page records seconds from open to 'Done reviewing'."""
    fid = int(request.path_params["fid"])
    form = await request.form()
    try:
        secs = max(0.0, min(float(form.get("seconds", 0)), 4 * 3600))
    except ValueError:
        secs = 0.0
    with store.connect() as conn:
        row = _load_filing(conn, fid)
        if not row:
            return Response("Filing not found", 404)
        store.audit(conn, "review_done", row["case_id"], fid,
                    {"seconds": secs, "mode": form.get("mode", "brief"), "useful": form.get("useful")})
    return RedirectResponse(f"/case/{row['case_id']}", 303)


async def metrics(request: Request):
    with store.connect() as conn:
        reviews = conn.execute("SELECT detail FROM audit WHERE event='review_done'").fetchall()
        gen = conn.execute(
            "SELECT extractor, COUNT(*) n, AVG(seconds) s FROM filings WHERE brief_json IS NOT NULL GROUP BY extractor"
        ).fetchall()
        guards = conn.execute("SELECT guard_json FROM filings WHERE guard_json IS NOT NULL").fetchall()
        failed = conn.execute("SELECT COUNT(*) n FROM audit WHERE event='brief_failed'").fetchone()["n"]
    by_mode: dict[str, list[float]] = defaultdict(list)
    useful: dict[str, int] = defaultdict(int)
    for r in reviews:
        d = json.loads(r["detail"])
        by_mode[d.get("mode", "brief")].append(d["seconds"])
        if d.get("useful"):
            useful[d["useful"]] += 1
    review_stats = {m: {"n": len(v), "avg": sum(v) / len(v), "median": sorted(v)[len(v) // 2]} for m, v in by_mode.items()}
    dropped = defaultdict(int)
    for g in guards:
        for d in json.loads(g["guard_json"])["dropped"]:
            dropped[d["reason"].split(":")[0]] += 1
    return templates.TemplateResponse(request, "metrics.html", {
        "review": review_stats, "useful": dict(useful), "gen": gen, "dropped": dict(dropped), "failed": failed,
    })


routes = [
    Route("/", docket),
    Route("/case/{cid:int}", case_view),
    Route("/case/{cid:int}/upload", upload, methods=["POST"]),
    Route("/case/{cid:int}/toggle", toggle, methods=["POST"]),
    Route("/filing/{fid:int}", brief_view),
    Route("/filing/{fid:int}/source", source_view),
    Route("/filing/{fid:int}/regenerate", regenerate, methods=["POST"]),
    Route("/filing/{fid:int}/done", review_done, methods=["POST"]),
    Route("/metrics", metrics),
    Route("/static/app.js", app_js),
]

app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])
