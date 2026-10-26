"""Local web UI for the HST tracker demo. Standard library only; binds to localhost.

    python ui.py            # then open http://127.0.0.1:8766
"""
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hst.assess import assess_receipt
from hst.generate import generate
from hst.pipeline import process_folder, summarize
from hst.rules import load_rules

HOST, PORT = "127.0.0.1", 8766
ROOT = Path(__file__).parent
OUT = ROOT / "output"
LOCK = threading.Lock()
RULES = load_rules()
STATE = {"seen": set(), "receipts": [], "bank": [], "decisions": {}}

SAMPLE = """STAPLES BUSINESS DEPOT
Store #0123 Toronto ON
HST# 123456789RT0001
Date: 2025-03-14
1 x item

Subtotal 100.00
HST (13%) 13.00
TOTAL 113.00
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.replace("__SAMPLE__", json.dumps(SAMPLE)), "text/html")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            with LOCK:
                if self.path == "/api/check":
                    text = str(body.get("text", ""))[:20000]
                    self._send(200, json.dumps(assess_receipt(text, RULES, STATE["seen"])))
                elif self.path == "/api/reset":
                    STATE["seen"].clear()
                    self._send(200, json.dumps({"ok": True}))
                elif self.path == "/api/batch":
                    n = max(10, min(int(body.get("n", 60)), 300))
                    with tempfile.TemporaryDirectory() as tmp:
                        generate(Path(tmp), n=n, seed=int(body.get("seed", 7)))
                        STATE["receipts"], STATE["bank"] = process_folder(Path(tmp), RULES)
                    STATE["decisions"] = {}
                    self._send(200, json.dumps(self._payload()))
                elif self.path == "/api/review":
                    f, action = body.get("file"), body.get("action")
                    known = {r["file"] for r in STATE["receipts"] if r["status"] == "review"}
                    if f not in known or action not in ("approved", "rejected"):
                        self._send(400, json.dumps({"error": "bad request"}))
                        return
                    STATE["decisions"][f] = action
                    OUT.mkdir(exist_ok=True)
                    with open(OUT / "reviewer_decisions.jsonl", "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"file": f, "action": action}) + "\n")
                    self._send(200, json.dumps(self._payload()))
                else:
                    self._send(404, json.dumps({"error": "not found"}))
        except (ValueError, KeyError) as exc:
            self._send(400, json.dumps({"error": str(exc)}))

    @staticmethod
    def _payload():
        return {"receipts": STATE["receipts"], "bank": STATE["bank"], "decisions": STATE["decisions"],
                "summary": summarize(STATE["receipts"], STATE["bank"], STATE["decisions"])}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HST Tracker</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5f6b7a;--line:#dde1e6;--ok:#157f3b;--okbg:#e3f4e8;--warn:#a15c00;--warnbg:#fff1d6;--bad:#b3261e;--badbg:#fde7e5;--info:#1f5fbf;--infobg:#e4edfb;--accent:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--card:#1d2126;--text:#e8eaed;--muted:#98a2b0;--line:#2f353c;--ok:#5fd187;--okbg:#17321f;--warn:#f0b455;--warnbg:#3a2c10;--bad:#ff8a80;--badbg:#3d1b18;--info:#7aa8f0;--infobg:#1b2a44;--accent:#7aa8f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1150px;margin:0 auto;padding:20px 16px 48px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 12px}
.disc{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 12px;font-size:13px;margin-bottom:14px}
.tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}.tab{padding:8px 14px;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;cursor:pointer;font:inherit}.tab.on{border-color:var(--accent);color:var(--accent);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
textarea{width:100%;min-height:260px;font:13px/1.45 ui-monospace,Consolas,monospace;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px;resize:vertical}
button.btn,label.btn{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;font:inherit;display:inline-block}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.btn:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:14px}th{color:var(--muted);font-weight:600}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:600;font-size:13px;white-space:nowrap}
.claim{background:var(--okbg);color:var(--ok)}.partial{background:var(--infobg);color:var(--info)}.none{background:var(--badbg);color:var(--bad)}.review{background:var(--warnbg);color:var(--warn)}.neutral{background:var(--bg);color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}.stat b{font-size:22px;display:block}.stat span{color:var(--muted);font-size:13px}
.list{max-height:540px;overflow:auto}.item{padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between;gap:8px;align-items:center}.item.sel{background:var(--bg)}
.muted{color:var(--muted)}pre{white-space:pre-wrap;font:12.5px/1.45 ui-monospace,Consolas,monospace;background:var(--bg);padding:10px;border-radius:8px;border:1px solid var(--line);max-height:200px;overflow:auto;margin:0 0 10px}
ul.why{margin:6px 0 10px;padding-left:18px}.note{font-size:13px;color:var(--muted);margin-top:10px}
</style></head><body><main>
<h1>HST Tracker (Ontario, 13%)</h1>
<p class="sub">Receipts and bank lines in, HST paid, claimable credits and missing receipts out. Demo on synthetic data.</p>
<div class="disc">Demo only. Rules are simplified and are not tax advice: an accountant must review them before real use.</div>
<div class="tabs"><button class="tab on" id="t1">Check a receipt</button><button class="tab" id="t2">Period dashboard</button></div>

<section id="p1"><div class="grid">
<div class="card"><label for="txt"><b>Receipt text</b></label><textarea id="txt" spellcheck="false"></textarea>
<div class="row"><button class="btn primary" id="go">Check</button><button class="btn" id="sample">Load sample</button>
<label class="btn" for="file">Open .txt file</label><input id="file" type="file" accept=".txt,text/plain" hidden>
<button class="btn" id="reset">Reset duplicates</button></div>
<p class="note">Try: change the vendor to The Keg Steakhouse (meals: 50%), delete the HST line, remove the registration number, or submit the same receipt twice. Reading a photo (OCR or a vision model) is the next step: this demo takes the receipt text.</p></div>
<div class="card" id="out"><p class="muted">Result appears here.</p></div></div></section>

<section id="p2" hidden>
<div class="row" style="margin:0 0 12px"><label>Receipts <input id="n" type="number" value="60" min="10" max="300" style="width:80px"></label>
<label>Seed <input id="seed" type="number" value="7" style="width:70px"></label><button class="btn primary" id="gen">Generate &amp; process</button></div>
<div class="stats" id="stats"></div>
<div class="tabs"><button class="tab on" data-v="receipts">Receipts</button><button class="tab" data-v="bank">Bank lines</button><button class="tab" data-v="missing">Missing receipts</button></div>
<div class="grid"><div class="card"><div class="list" id="list"><p class="muted">Click Generate &amp; process.</p></div></div>
<div class="card" id="detail"><p class="muted">Select a row.</p></div></div></section>
</main>
<script>
const $=id=>document.getElementById(id);const SAMPLE=__SAMPLE__;
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json());
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const money=x=>'$'+Number(x).toLocaleString('en-CA',{minimumFractionDigits:2,maximumFractionDigits:2});
const STATUS={claim:'Claim in full',partial:'Partial claim',none:'Not claimable',review:'Needs review'};
function show(n){$('p1').hidden=n!==1;$('p2').hidden=n!==2;$('t1').classList.toggle('on',n===1);$('t2').classList.toggle('on',n===2)}
$('t1').onclick=()=>show(1);$('t2').onclick=()=>show(2);

function sbadge(r,decision){
  if(decision)return el('span','badge '+(decision==='approved'?'claim':'none'),'Reviewer: '+decision);
  return el('span','badge '+r.status,STATUS[r.status])}
function renderReceipt(t,r,decision){
  t.replaceChildren();const h=el('div','row');h.style.marginTop='0';h.append(sbadge(r,decision));t.append(h);
  const f=r.fields,tb=el('table');
  [['Vendor',f.vendor],['Date',f.date],['Registration no.',f.registration_number],['Category',r.category],['Total',f.total&&money(f.total)],
   ['HST in total',money(r.hst_amount)+(r.hst_source==='back_calculated'?' (calculated)':'')],
   ['Claimable now',money(r.itc_claimable)],['Held for review',money(r.itc_pending)]].forEach(([k,v])=>{
    const tr=el('tr');tr.append(el('td',null,k),el('td',v?null:'muted',v||'not found'));tb.append(tr)});
  t.append(el('div',null,''),tb);
  const ul=el('ul','why');r.reasons.forEach(x=>ul.append(el('li',null,x)));t.append(el('b',null,'Why'),ul)}
$('txt').value=SAMPLE;$('sample').onclick=()=>{$('txt').value=SAMPLE};
$('file').onchange=e=>{const f=e.target.files[0];if(f)f.text().then(x=>{$('txt').value=x})};
$('go').onclick=async()=>{$('go').disabled=true;try{renderReceipt($('out'),await post('/api/check',{text:$('txt').value}))}finally{$('go').disabled=false}};
$('reset').onclick=async()=>{await post('/api/reset');$('out').replaceChildren(el('p','muted','Duplicate memory cleared.'))};

let data=null,view='receipts',sel=null;
$('gen').onclick=async()=>{$('gen').disabled=true;try{data=await post('/api/batch',{n:+$('n').value,seed:+$('seed').value});sel=null;draw()}finally{$('gen').disabled=false}};
document.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{view=b.dataset.v;sel=null;document.querySelectorAll('[data-v]').forEach(x=>x.classList.toggle('on',x===b));draw()});
function stats(){
  const a=data.summary.amounts,c=data.summary.counts,st=$('stats');st.replaceChildren();
  [['HST paid (receipts)',money(a.hst_paid)],['Claimable credits',money(a.claimable)],['Held for review',money(a.pending)+' ('+(c.review-Object.keys(data.decisions).length)+')'],
   ['Not claimable',money(a.not_claimable)],['Missing receipts',c.missing_receipts+' / est. '+money(a.missing_est_itc)]].forEach(([k,v])=>{
    const d=el('div','card stat');d.append(el('b',null,v),el('span',null,k));st.append(d)})}
function draw(){
  if(!data)return;stats();const list=$('list'),det=$('detail');list.replaceChildren();det.replaceChildren();
  if(view==='receipts'){
    data.receipts.forEach(r=>{const dup=r.flags.includes('DUPLICATE');
      const it=el('div','item'+(sel===r?' sel':''));const l=el('div');
      l.append(el('div',null,r.file+'  '+(r.fields.vendor||'(unreadable)')),el('div','muted',(r.fields.total?money(r.fields.total):'?')+(dup?'  duplicate':'')));
      it.append(l,sbadge(r,data.decisions[r.file]));it.onclick=()=>{sel=r;draw()};list.append(it)});
    if(!sel){det.append(el('p','muted','Select a receipt.'));return}
    det.append(el('b',null,sel.file),el('pre',null,sel.text));const box=el('div');det.append(box);renderReceipt(box,sel,data.decisions[sel.file]);
    if(sel.status==='review'&&!data.decisions[sel.file]){const row=el('div','row');
      [['approved','Approve claim'],['rejected','Reject']].forEach(([a,t])=>{const b=el('button','btn'+(a==='approved'?' primary':''),t);
        b.onclick=async()=>{data=await post('/api/review',{file:sel.file,action:a});sel=data.receipts.find(x=>x.file===sel.file);draw()};row.append(b)});
      det.append(row)}
  }else{
    const rows=data.bank.filter(b=>view==='bank'||b.kind==='missing_receipt');
    const KIND={matched:['claim','Matched'],weak_match:['review','Weak match'],missing_receipt:['review','No receipt'],non_taxable:['neutral','No HST'],deposit:['neutral','Deposit'],unknown:['review','Unknown payee']};
    rows.forEach(b=>{const it=el('div','item'+(sel===b?' sel':''));const l=el('div');
      l.append(el('div',null,b.date+'  '+b.description),el('div','muted',money(Math.abs(b.amount))));
      const k=KIND[b.kind];it.append(l,el('span','badge '+k[0],k[1]));it.onclick=()=>{sel=b;draw()};list.append(it)});
    if(!sel){det.append(el('p','muted',view==='missing'?'These payments have no receipt: get one before claiming.':'Select a bank line.'));return}
    det.append(el('b',null,sel.description),el('p',null,sel.note));
    if(sel.receipt)det.append(el('p',null,'Receipt: '+sel.receipt));
    if(sel.kind==='missing_receipt')det.append(el('p',null,'Estimated HST '+money(sel.est_hst)+', estimated credit '+money(sel.est_itc)+' (not claimable without a receipt).'))}}
</script></body></html>"""


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"HST Tracker UI on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
