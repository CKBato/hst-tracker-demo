"""HST Tracker web app: phone camera capture, PC bulk folder import, bank CSV, review dashboard.

    python ui.py                 # this PC only:   http://127.0.0.1:8766
    python ui.py --lan           # also reachable from your phone on the same Wi-Fi (prints a private link)

Standard library only. Tesseract (local OCR) is optional but needed to read photos; see README.
"""
import argparse
import csv
import hmac
import io
import json
import secrets
import socket
import tempfile
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from hst.assess import assess_receipt
from hst.generate import generate
from hst.ocr import IMAGE_EXTS, engines_available, read_image
from hst.rules import load_rules
from hst.store import Store

ROOT = Path(__file__).parent
MAX_UPLOAD = 20 * 1024 * 1024
RULES = load_rules()
STORE = Store(ROOT / "store", RULES)
CONFIG = {"token": None}
CHECK_SEEN = set()
JOB = {"running": False, "total": 0, "done": 0, "added": 0, "skipped": 0, "errors": []}
JOB_LOCK = threading.Lock()
LOCAL = ("127.0.0.1", "::1")

SAMPLE = """STAPLES BUSINESS DEPOT
Store #0123 Toronto ON
HST# 123456789RT0001
Date: 2025-03-14
1 x item

Subtotal 100.00
HST (13%) 13.00
TOTAL 113.00
"""


def run_import(folder, engine):
    files = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_EXTS)
    with JOB_LOCK:
        JOB.update(running=True, total=len(files), done=0, added=0, skipped=0, errors=[])
    for p in files:
        try:
            data = p.read_bytes()
            if STORE.has_image(data):
                with JOB_LOCK:
                    JOB["skipped"] += 1
            else:
                res = read_image(p, engine)
                STORE.add_receipt(res["text"], res["confidence"], res["engine"], "folder",
                                  p.name, data, p.suffix.lower())
                with JOB_LOCK:
                    JOB["added"] += 1
        except Exception as exc:  # keep going: one bad file must not stop a 300-photo import
            with JOB_LOCK:
                JOB["errors"].append(f"{p.name}: {exc}"[:200])
        with JOB_LOCK:
            JOB["done"] += 1
    with JOB_LOCK:
        JOB["running"] = False


def load_demo():
    with tempfile.TemporaryDirectory() as tmp:
        generate(Path(tmp), n=40, seed=7)
        for p in sorted(Path(tmp).glob("*.txt")):
            STORE.add_receipt(p.read_text(encoding="utf-8"), None, "demo", "demo", p.name)
        STORE.set_bank_csv((Path(tmp) / "bank.csv").read_text(encoding="utf-8"))


def ledger_csv(snap):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "original_name", "vendor", "date", "category", "total", "hst", "hst_source",
                "status", "reviewer", "itc_claimable", "itc_pending", "reasons"])
    for r in snap["receipts"]:
        f = r["fields"]
        w.writerow([r["id"], r["original_name"], f["vendor"], f["date"], r["category"], f["total"],
                    r["hst_amount"], r["hst_source"], r["status"], snap["decisions"].get(r["id"], ""),
                    r["itc_claimable"], r["itc_pending"], " | ".join(r["reasons"])])
    return out.getvalue()


def missing_csv(snap):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "description", "amount", "estimated_hst", "estimated_itc"])
    for b in snap["bank"]:
        if b["kind"] == "missing_receipt":
            w.writerow([b["date"], b["description"], b["amount"], b["est_hst"], b["est_itc"]])
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # ---- plumbing
    def _is_local(self):
        return self.client_address[0] in LOCAL

    def _authorized(self):
        if self._is_local():
            return True
        token = CONFIG["token"]
        if not token:
            return False
        given = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        given = given or (cookie["hst_token"].value if "hst_token" in cookie else "")
        return hmac.compare_digest(given, token)

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype if "charset" in ctype or ctype.startswith("image") else ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg):
        self._send(code, json.dumps({"error": msg}))

    def _state(self):
        snap = STORE.snapshot()
        snap["engines"] = engines_available()
        snap["local"] = self._is_local()
        snap["lan"] = bool(CONFIG["token"])
        return snap

    # ---- GET
    def do_GET(self):
        if not self._authorized():
            self._send(403, "Open the private link printed in the terminal.", "text/plain")
            return
        path = urlparse(self.path).path
        if path == "/":
            extra = {}
            if CONFIG["token"] and not self._is_local():
                extra["Set-Cookie"] = f"hst_token={CONFIG['token']}; Path=/; HttpOnly; SameSite=Strict"
            self._send(200, PAGE.replace("__SAMPLE__", json.dumps(SAMPLE)), "text/html", extra)
        elif path == "/api/state":
            self._send(200, json.dumps(self._state()))
        elif path == "/api/job":
            with JOB_LOCK:
                self._send(200, json.dumps(JOB))
        elif path.startswith("/image/"):
            img = STORE.image_path(unquote(path[7:]))
            if not img:
                self._err(404, "no image")
                return
            ctype = {".png": "image/png", ".webp": "image/webp"}.get(img.suffix.lower(), "image/jpeg")
            self._send(200, img.read_bytes(), ctype)
        elif path == "/export/ledger.csv":
            self._send(200, ledger_csv(STORE.snapshot()), "text/csv",
                       {"Content-Disposition": "attachment; filename=hst_ledger.csv"})
        elif path == "/export/missing_receipts.csv":
            self._send(200, missing_csv(STORE.snapshot()), "text/csv",
                       {"Content-Disposition": "attachment; filename=missing_receipts.csv"})
        else:
            self._err(404, "not found")

    # ---- POST
    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("File too large (20 MB max).")
        return self.rfile.read(length)

    def do_POST(self):
        if not self._authorized():
            self._err(403, "forbidden")
            return
        path = urlparse(self.path).path
        try:
            raw = self._body()
            if path == "/api/upload":
                self._upload(raw)
                return
            body = json.loads(raw or b"{}")
            if path == "/api/check":
                self._send(200, json.dumps(assess_receipt(str(body.get("text", ""))[:20000], RULES, CHECK_SEEN)))
            elif path == "/api/reset":
                CHECK_SEEN.clear()
                self._send(200, json.dumps({"ok": True}))
            elif path == "/api/review":
                rid, action = body.get("id"), body.get("action")
                known = {r["id"] for r in STORE.snapshot()["receipts"] if r["status"] == "review"}
                if rid not in known or action not in ("approved", "rejected"):
                    self._err(400, "bad request")
                    return
                STORE.decide(rid, action)
                self._send(200, json.dumps(self._state()))
            elif path == "/api/bank":
                count, skipped = STORE.set_bank_csv(str(body.get("csv", "")))
                state = self._state()
                state["bank_rows"], state["bank_skipped"] = count, skipped
                self._send(200, json.dumps(state))
            elif path == "/api/demo":
                load_demo()
                self._send(200, json.dumps(self._state()))
            elif path == "/api/clear":
                STORE.clear()
                self._send(200, json.dumps(self._state()))
            elif path == "/api/import-folder":
                if not self._is_local():
                    self._err(403, "Folder import only works from the PC running the app.")
                    return
                folder, engine = Path(str(body.get("path", "")).strip().strip('"')), body.get("engine", "tesseract")
                if not folder.is_dir():
                    self._err(400, f"Folder not found: {folder}")
                    return
                if not engines_available().get(engine):
                    self._err(400, f"Engine '{engine}' is not available (see Import tab).")
                    return
                with JOB_LOCK:
                    if JOB["running"]:
                        self._err(409, "An import is already running.")
                        return
                    JOB.update(running=True, total=0, done=0, added=0, skipped=0, errors=[])
                threading.Thread(target=run_import, args=(folder, engine), daemon=True).start()
                self._send(200, json.dumps({"started": True}))
            else:
                self._err(404, "not found")
        except (ValueError, KeyError) as exc:
            self._err(400, str(exc))

    def _upload(self, data):
        name = unquote(self.headers.get("X-Filename", "photo.jpg"))
        ext = Path(name).suffix.lower() or ".jpg"
        engine = self.headers.get("X-Engine", "tesseract")
        if ext not in IMAGE_EXTS:
            self._err(400, f"Unsupported file type {ext}. Use JPG or PNG (convert HEIC to JPG).")
            return
        if not engines_available().get(engine):
            self._err(400, f"Engine '{engine}' is not available.")
            return
        if STORE.has_image(data):
            self._send(200, json.dumps({"duplicate_upload": True}))
            return
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"upload{ext}"
            src.write_bytes(data)
            try:
                res = read_image(src, engine)
            except Exception as exc:
                self._err(502, f"Could not read the photo: {exc}")
                return
        rid, _ = STORE.add_receipt(res["text"], res["confidence"], res["engine"], "upload", name, data, ext)
        self._send(200, json.dumps({"receipt": STORE.assess_one(rid)}))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HST Tracker</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5f6b7a;--line:#dde1e6;--ok:#157f3b;--okbg:#e3f4e8;--warn:#a15c00;--warnbg:#fff1d6;--bad:#b3261e;--badbg:#fde7e5;--info:#1f5fbf;--infobg:#e4edfb;--accent:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--card:#1d2126;--text:#e8eaed;--muted:#98a2b0;--line:#2f353c;--ok:#5fd187;--okbg:#17321f;--warn:#f0b455;--warnbg:#3a2c10;--bad:#ff8a80;--badbg:#3d1b18;--info:#7aa8f0;--infobg:#1b2a44;--accent:#7aa8f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1150px;margin:0 auto;padding:14px 16px 48px}h1{font-size:20px;margin:0}.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.disc{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:6px 12px;font-size:13px;margin:8px 0 12px}
.tabs{display:flex;gap:6px;margin-bottom:12px;overflow-x:auto}.tab{padding:10px 14px;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;cursor:pointer;font:inherit;white-space:nowrap}.tab.on{border-color:var(--accent);color:var(--accent);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
textarea,input[type=text],select{width:100%;font:14px ui-monospace,Consolas,monospace;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px}
textarea{min-height:200px;resize:vertical}select{font-family:inherit;width:auto}
button.btn,label.btn{padding:12px 16px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;font:inherit;display:inline-block;text-align:center}
.primary{background:var(--accent)!important;border-color:var(--accent)!important;color:#fff!important}button.btn:disabled{opacity:.5}
.big{width:100%;padding:18px;font-size:18px;font-weight:600}.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.drop{border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;color:var(--muted);margin-top:10px}.drop.over{border-color:var(--accent);color:var(--accent)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:14px}th{color:var(--muted);font-weight:600}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:600;font-size:13px;white-space:nowrap}
.claim{background:var(--okbg);color:var(--ok)}.partial{background:var(--infobg);color:var(--info)}.none{background:var(--badbg);color:var(--bad)}.review{background:var(--warnbg);color:var(--warn)}.neutral{background:var(--bg);color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}.stat b{font-size:22px;display:block}.stat span{color:var(--muted);font-size:13px}
.list{max-height:560px;overflow:auto}.item{padding:10px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between;gap:8px;align-items:center}.item.sel{background:var(--bg)}
.thumb{width:44px;height:44px;object-fit:cover;border-radius:6px;border:1px solid var(--line);flex:none}.left{display:flex;gap:10px;align-items:center;min-width:0}.left div{min-width:0}
.muted{color:var(--muted)}.trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
pre{white-space:pre-wrap;font:12.5px/1.45 ui-monospace,Consolas,monospace;background:var(--bg);padding:10px;border-radius:8px;border:1px solid var(--line);max-height:180px;overflow:auto;margin:0 0 10px}
img.photo{max-width:100%;max-height:340px;border-radius:8px;border:1px solid var(--line);display:block;margin-bottom:10px}
ul.why{margin:6px 0 10px;padding-left:18px}.note{font-size:13px;color:var(--muted);margin-top:10px}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.chip{padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;font:inherit;font-size:13px}.chip.on{border-color:var(--accent);color:var(--accent);font-weight:600}
progress{width:100%;height:10px}.err{color:var(--bad);font-size:13px}
</style></head><body><main>
<div class="top"><h1>HST Tracker (Ontario, 13%)</h1><span class="muted" id="who"></span></div>
<div class="disc">Demo. Rules are simplified and are not tax advice: an accountant must review them before real use.</div>
<div class="tabs"><button class="tab on" data-t="cap">Capture</button><button class="tab" data-t="dash">Dashboard</button><button class="tab" data-t="bank">Bank</button><button class="tab" data-t="imp">Import &amp; tools</button><button class="tab" data-t="txt">Check text</button></div>

<section id="cap"><div class="grid"><div class="card">
<label class="btn primary big" for="cam">Take photo of receipt</label>
<input id="cam" type="file" accept="image/*" capture="environment" hidden>
<div class="row"><label class="btn" for="pick" style="flex:1">Choose photos</label><input id="pick" type="file" accept="image/*" multiple hidden>
<label>Engine <select id="engine"></select></label></div>
<div class="drop" id="drop">On a PC: drag receipt photos here</div>
<p class="note" id="engnote"></p><div id="queue"></div></div>
<div class="card"><b>This session</b><div class="list" id="recent"><p class="muted">Captured receipts appear here with the HST and claim result.</p></div></div></div></section>

<section id="dash" hidden><div class="stats" id="stats"></div>
<div class="grid"><div class="card"><div class="chips" id="chips"></div><div class="list" id="list"></div></div><div class="card" id="detail"><p class="muted">Select a receipt.</p></div></div></section>

<section id="bank" hidden><div class="grid"><div class="card"><b>Bank statement CSV</b>
<p class="note">Columns: date (YYYY-MM-DD or MM/DD/YYYY), description, and either amount (negative = money out) or debit/credit.</p>
<div class="row"><label class="btn primary" for="csvf">Choose CSV file</label><input id="csvf" type="file" accept=".csv,text/csv" hidden>
<a class="btn" href="/export/missing_receipts.csv">Download missing receipts</a><a class="btn" href="/export/ledger.csv">Download ledger</a></div><p id="bankmsg" class="note"></p></div>
<div class="card"><div class="chips" id="bchips"></div><div class="list" id="blist"><p class="muted">Upload a statement to match it against your receipts.</p></div></div></div></section>

<section id="imp" hidden><div class="grid"><div class="card"><b>Import a folder of photos (this PC only)</b>
<p class="note">Example: C:\Users\you\Documents\hst2026. Subfolders are included. Photos already imported are skipped.</p>
<input type="text" id="folder" placeholder="C:\path\to\hst2026"><div class="row"><button class="btn primary" id="imp-go">Import folder</button></div>
<progress id="prog" value="0" max="1" hidden></progress><p id="impmsg" class="note"></p><div id="errs" class="err"></div></div>
<div class="card"><b>Tools</b><div class="row"><button class="btn" id="demo">Load demo data</button><button class="btn" id="clear">Delete all receipts</button></div>
<p class="note">Originals are kept in the <code>store/images</code> folder. CRA expects receipt records to be kept, so back that folder up.</p>
<p class="note" id="engines"></p></div></div></section>

<section id="txt" hidden><div class="grid"><div class="card"><b>Receipt text</b><textarea id="t" spellcheck="false"></textarea>
<div class="row"><button class="btn primary" id="go">Check</button><button class="btn" id="sample">Load sample</button><button class="btn" id="reset">Reset duplicates</button></div></div>
<div class="card" id="out"><p class="muted">Result appears here.</p></div></div></section>
</main>
<script>
const $=id=>document.getElementById(id),SAMPLE=__SAMPLE__;
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const money=x=>'$'+Number(x).toLocaleString('en-CA',{minimumFractionDigits:2,maximumFractionDigits:2});
const STATUS={claim:'Claim in full',partial:'Partial claim',none:'Not claimable',review:'Needs review'};
const jpost=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json());
let S=null,recent=[],filter='all',sel=null,bfilter='all',bsel=null;

document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===b));
  ['cap','dash','bank','imp','txt'].forEach(id=>$(id).hidden=id!==b.dataset.t);
  if(b.dataset.t!=='cap'&&b.dataset.t!=='txt')refresh()});

async function refresh(){S=await fetch('/api/state').then(r=>r.json());drawAll()}
function badge(r){const d=S&&S.decisions[r.id];
  if(d)return el('span','badge '+(d==='approved'?'claim':'none'),'Reviewer: '+d);
  return el('span','badge '+r.status,STATUS[r.status])}
function receiptTable(t,r){
  const f=r.fields,tb=el('table');
  [['Vendor',f.vendor],['Date',f.date],['Registration no.',f.registration_number],['Category',r.category],['Total',f.total&&money(f.total)],
   ['HST in total',money(r.hst_amount)+(r.hst_source==='back_calculated'?' (calculated)':'')],['Claimable now',money(r.itc_claimable)],['Held for review',money(r.itc_pending)]].forEach(([k,v])=>{
    const tr=el('tr');tr.append(el('td',null,k),el('td',v?null:'muted',v||'not found'));tb.append(tr)});
  t.append(tb);const ul=el('ul','why');r.reasons.forEach(x=>ul.append(el('li',null,x)));t.append(el('b',null,'Why'),ul)}

/* ---------- capture ---------- */
const engSel=$('engine');
function engines(){engSel.replaceChildren();const e=(S&&S.engines)||{};
  [['tesseract','Local OCR (private)'],['claude','Claude vision']].forEach(([k,l])=>{const o=el('option',null,l+(e[k]?'':' (not set up)'));o.value=k;o.disabled=!e[k];engSel.append(o)});
  const saved=localStorage.getItem('engine');if(saved&&e[saved])engSel.value=saved;else if(e.tesseract)engSel.value='tesseract';
  $('engnote').textContent=e.claude?'Claude vision sends the photo to Anthropic and costs money per receipt.':'Photos are read on this computer. Nothing leaves it.';
  $('engines').textContent='Tesseract: '+(e.tesseract?'ready':'not installed')+'. Claude vision: '+(e.claude?'API key found':'no ANTHROPIC_API_KEY set')+'.'}
engSel.onchange=()=>localStorage.setItem('engine',engSel.value);

function shrink(file){return new Promise(res=>{
  if(!file.type.startsWith('image/')||file.type==='image/png'&&file.size<1.5e6)return res(file);
  const url=URL.createObjectURL(file),img=new Image();
  img.onload=()=>{const m=2200,k=Math.min(1,m/Math.max(img.width,img.height)),c=document.createElement('canvas');
    c.width=Math.round(img.width*k);c.height=Math.round(img.height*k);c.getContext('2d').drawImage(img,0,0,c.width,c.height);
    c.toBlob(b=>{URL.revokeObjectURL(url);res(b||file)},'image/jpeg',0.88)};
  img.onerror=()=>{URL.revokeObjectURL(url);res(file)};img.src=url})}

async function handleFiles(files){
  const list=[...files];if(!list.length)return;const q=$('queue');
  for(let i=0;i<list.length;i++){
    q.replaceChildren(el('p','muted','Reading photo '+(i+1)+' of '+list.length+'...'));
    const f=list[i],blob=await shrink(f),ext=blob.type==='image/jpeg'?'.jpg':(f.name.match(/\.[^.]+$/)||['.jpg'])[0];
    const name=f.name.replace(/\.[^.]+$/,'')+ext;
    try{const r=await fetch('/api/upload',{method:'POST',body:blob,headers:{'X-Filename':encodeURIComponent(name),'X-Engine':engSel.value}}).then(x=>x.json());
      if(r.error)recent.unshift({error:name+': '+r.error});else if(r.duplicate_upload)recent.unshift({error:name+': already uploaded'});else recent.unshift(r.receipt)}
    catch(e){recent.unshift({error:name+': upload failed'})}
    drawRecent()}
  q.replaceChildren();refresh()}
$('cam').onchange=e=>{handleFiles(e.target.files);e.target.value=''};
$('pick').onchange=e=>{handleFiles(e.target.files);e.target.value=''};
const drop=$('drop');['dragenter','dragover'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',e=>handleFiles(e.dataTransfer.files));
function drawRecent(){const box=$('recent');box.replaceChildren();
  recent.forEach(r=>{
    if(r.error){box.append(el('div','item err',r.error));return}
    const it=el('div','item'),l=el('div','left');
    if(r.image){const im=el('img','thumb');im.src='/image/'+r.id;l.append(im)}
    const d=el('div');d.append(el('div','trunc',(r.fields.vendor||'(unreadable)')+'  '+(r.fields.total?money(r.fields.total):'')),
      el('div','muted','HST '+money(r.hst_amount)+(r.reasons[0]?'  ·  '+r.reasons[0]:'')));l.append(d);it.append(l,el('span','badge '+r.status,STATUS[r.status]));
    it.onclick=()=>{sel=r.id;document.querySelector('[data-t=dash]').click()};box.append(it)})}

/* ---------- dashboard ---------- */
function stats(){const a=S.summary.amounts,c=S.summary.counts,st=$('stats');st.replaceChildren();
  [['HST paid (receipts)',money(a.hst_paid)],['Claimable credits',money(a.claimable)],
   ['Held for review',money(a.pending)+' ('+(c.review-Object.keys(S.decisions).length)+')'],['Not claimable',money(a.not_claimable)],
   ['Missing receipts',c.missing_receipts+' / est. '+money(a.missing_est_itc)]].forEach(([k,v])=>{
    const d=el('div','card stat');d.append(el('b',null,v),el('span',null,k));st.append(d)})}
function chips(box,opts,cur,fn){box.replaceChildren();opts.forEach(([k,l])=>{const b=el('button','chip'+(cur===k?' on':''),l);b.onclick=()=>fn(k);box.append(b)})}
function drawDash(){stats();
  chips($('chips'),[['all','All'],['review','Needs review'],['claim','Claimable'],['none','Not claimable']],filter,k=>{filter=k;drawDash()});
  const list=$('list');list.replaceChildren();
  const rows=S.receipts.filter(r=>filter==='all'||(filter==='claim'?['claim','partial'].includes(r.status):r.status===filter));
  if(!rows.length)list.append(el('p','muted','No receipts yet. Take a photo on the Capture tab, import a folder, or load demo data.'));
  rows.slice().reverse().forEach(r=>{const it=el('div','item'+(sel===r.id?' sel':'')),l=el('div','left');
    if(r.image){const im=el('img','thumb');im.src='/image/'+r.id;im.loading='lazy';l.append(im)}
    const d=el('div');d.append(el('div','trunc',(r.fields.vendor||'(unreadable)')),el('div','muted',(r.fields.total?money(r.fields.total):'?')+'  '+(r.fields.date||'')));l.append(d);
    it.append(l,badge(r));it.onclick=()=>{sel=r.id;drawDash();$('detail').scrollIntoView({behavior:'smooth',block:'start'})};list.append(it)});
  const det=$('detail');det.replaceChildren();const r=S.receipts.find(x=>x.id===sel);
  if(!r){det.append(el('p','muted','Select a receipt.'));return}
  const h=el('div','row');h.style.marginTop='0';h.append(badge(r),el('span','muted',r.engine+(r.confidence!=null?' · OCR '+Math.round(r.confidence)+'%':'')));det.append(h);
  if(r.image){const im=el('img','photo');im.src='/image/'+r.id;det.append(im)}
  receiptTable(det,r);
  const raw=document.createElement('details');raw.append(el('summary','muted','Text read from the receipt'),el('pre',null,r.text));det.append(raw);
  if(r.status==='review'&&!S.decisions[r.id]){const row=el('div','row');
    [['approved','Approve claim'],['rejected','Reject']].forEach(([a,t])=>{const b=el('button','btn'+(a==='approved'?' primary':''),t);
      b.onclick=async()=>{S=await jpost('/api/review',{id:r.id,action:a});drawAll()};row.append(b)});det.append(row)}}

/* ---------- bank ---------- */
const KIND={matched:['claim','Matched'],weak_match:['review','Weak match'],missing_receipt:['review','No receipt'],non_taxable:['neutral','No HST'],deposit:['neutral','Deposit'],unknown:['review','Unknown payee']};
function drawBank(){chips($('bchips'),[['all','All'],['missing_receipt','No receipt'],['weak_match','Weak match'],['unknown','Unknown']],bfilter,k=>{bfilter=k;drawBank()});
  const l=$('blist');l.replaceChildren();const rows=S.bank.filter(b=>bfilter==='all'||b.kind===bfilter);
  if(!rows.length)l.append(el('p','muted',S.bank.length?'Nothing in this view.':'Upload a statement to match it against your receipts.'));
  rows.forEach(b=>{const it=el('div','item'),d=el('div');d.append(el('div','trunc',b.date+'  '+b.description),
    el('div','muted',money(Math.abs(b.amount))+(b.kind==='missing_receipt'?'  ·  est. HST '+money(b.est_hst):'')+(b.receipt?'  ·  '+b.receipt:'')));
    const k=KIND[b.kind];it.append(d,el('span','badge '+k[0],k[1]));l.append(it)})}
$('csvf').onchange=async e=>{const f=e.target.files[0];if(!f)return;
  const r=await jpost('/api/bank',{csv:await f.text()});S=r;$('bankmsg').textContent=r.bank_rows+' lines read'+(r.bank_skipped?', '+r.bank_skipped+' skipped (unrecognised date or amount)':'')+'.';drawAll();e.target.value=''};

/* ---------- import & tools ---------- */
let poll=null;
async function pollJob(){const j=await fetch('/api/job').then(r=>r.json()),p=$('prog');p.hidden=!j.total;p.max=j.total||1;p.value=j.done;
  $('impmsg').textContent=(j.running?'Reading ':'Finished: ')+j.done+' of '+j.total+' photos. Added '+j.added+', skipped '+j.skipped+' already imported'+(j.errors.length?', '+j.errors.length+' failed':'')+'.';
  $('errs').textContent=j.errors.slice(0,5).join('  |  ');
  if(!j.running){clearInterval(poll);poll=null;$('imp-go').disabled=false;refresh()}}
$('imp-go').onclick=async()=>{$('imp-go').disabled=true;
  const r=await jpost('/api/import-folder',{path:$('folder').value,engine:engSel.value});
  if(r.error){$('impmsg').textContent=r.error;$('imp-go').disabled=false;return}
  poll=setInterval(pollJob,1000)};
$('demo').onclick=async()=>{S=await jpost('/api/demo');drawAll()};
$('clear').onclick=async()=>{if(confirm('Delete all receipts, photos and bank data from this app?')){S=await jpost('/api/clear');recent=[];sel=null;drawAll()}};

/* ---------- text check ---------- */
$('t').value=SAMPLE;$('sample').onclick=()=>{$('t').value=SAMPLE};
$('go').onclick=async()=>{const r=await jpost('/api/check',{text:$('t').value}),o=$('out');o.replaceChildren();
  const h=el('div','row');h.style.marginTop='0';h.append(el('span','badge '+r.status,STATUS[r.status]));o.append(h);receiptTable(o,r)};
$('reset').onclick=async()=>{await jpost('/api/reset');$('out').replaceChildren(el('p','muted','Duplicate memory cleared.'))};

function drawAll(){engines();drawRecent();drawDash();drawBank();$('who').textContent=S.lan&&!S.local?'phone mode':''}
refresh();
</script></body></html>"""


def lan_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="allow phones on your Wi-Fi (private link with token)")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.lan:
        CONFIG["token"] = secrets.token_urlsafe(12)
    server = ThreadingHTTPServer((host, args.port), Handler)
    print(f"HST Tracker on this PC:  http://127.0.0.1:{args.port}")
    if args.lan:
        for ip in lan_ips():
            print(f"On your phone (same Wi-Fi): http://{ip}:{args.port}/?token={CONFIG['token']}")
        print("Plain HTTP on your local network: use trusted Wi-Fi only. The link is private; don't share it.")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
