import os
import time
import threading
import sqlite3
import json
import requests
import pyotp
from flask import Flask, render_template_string, request, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# ─────────────────────────────────────────────
# TOKENS
# ─────────────────────────────────────────────
NIFTY_TOKEN = "99926000"
VIX_TOKEN   = "99926017"

# ─────────────────────────────────────────────
# SQLITE — persistent paper trades (survives Render restarts)
# ─────────────────────────────────────────────
DB_PATH = "/tmp/goat_paper.db"

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            direction   TEXT,
            entry_price REAL,
            exit_price  REAL,
            target      REAL,
            sl          REAL,
            qty         INTEGER DEFAULT 1,
            setup       TEXT,
            source      TEXT DEFAULT 'MANUAL',
            note        TEXT,
            post_note   TEXT,
            exit_reason TEXT,
            emotion     TEXT,
            entry_time  TEXT,
            exit_time   TEXT,
            pnl         REAL,
            status      TEXT DEFAULT 'OPEN'
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS introspection (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT,
            rule_followed INTEGER,
            sl_skip       TEXT,
            revenge       TEXT,
            discipline    INTEGER,
            tomorrow_rule TEXT,
            created_at    TEXT
        )
    """)
    con.commit()
    con.close()

db_init()

def db_open_trade():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row: return None
    cols = [d[0] for d in con.description] if False else [
        'id','direction','entry_price','exit_price','target','sl','qty',
        'setup','source','note','post_note','exit_reason','emotion',
        'entry_time','exit_time','pnl','status']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty',
            'setup','source','note','post_note','exit_reason','emotion',
            'entry_time','exit_time','pnl','status']
    return [dict(zip(cols, r)) for r in rows]

def db_insert_trade(t):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT INTO paper_trades
        (direction,entry_price,target,sl,qty,setup,source,note,entry_time,status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (t['direction'],t['entry_price'],t['target'],t['sl'],
          t.get('qty',1),t['setup'],t.get('source','MANUAL'),
          t.get('note',''),t['entry_time'],'OPEN'))
    con.commit()
    con.close()

def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        UPDATE paper_trades
        SET exit_price=?,exit_reason=?,post_note=?,emotion=?,
            exit_time=?,pnl=?,status='CLOSED'
        WHERE id=?
    """, (exit_price, exit_reason, post_note, emotion,
          time.strftime("%H:%M:%S"), pnl, trade_id))
    con.commit()
    con.close()

def db_clear_trades():
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM paper_trades")
    con.commit()
    con.close()

def db_add_intro(data):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT INTO introspection
        (date,rule_followed,sl_skip,revenge,discipline,tomorrow_rule,created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (data['date'], data['rule_followed'], data['sl_skip'],
          data['revenge'], data['discipline'], data['tomorrow_rule'],
          time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()

def db_get_intros(limit=10):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT * FROM introspection ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    cols = ['id','date','rule_followed','sl_skip','revenge','discipline','tomorrow_rule','created_at']
    return [dict(zip(cols, r)) for r in rows]

def calc_stats(trades):
    if not trades:
        return dict(total=0,wins=0,losses=0,win_rate=0,total_pnl=0,
                    avg_win=0,avg_loss=0,best=0,worst=0,expectancy=0,
                    by_setup={},by_emotion={})
    wins   = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    total  = len(trades)
    wr     = round(len(wins)/total*100) if total else 0
    tot_pnl= round(sum(t['pnl'] or 0 for t in trades), 1)
    avg_win  = round(sum(t['pnl'] for t in wins)/len(wins), 1)   if wins   else 0
    avg_loss = round(sum(t['pnl'] for t in losses)/len(losses),1) if losses else 0
    best  = round(max(t['pnl'] or 0 for t in trades), 1)
    worst = round(min(t['pnl'] or 0 for t in trades), 1)
    exp   = round((wr/100)*avg_win - ((100-wr)/100)*abs(avg_loss), 2)
    by_setup = {}
    for t in trades:
        s = t.get('setup','?')
        if s not in by_setup: by_setup[s] = {'count':0,'wins':0,'pnl':0.0}
        by_setup[s]['count'] += 1
        by_setup[s]['pnl']    = round(by_setup[s]['pnl'] + (t['pnl'] or 0), 1)
        if (t['pnl'] or 0) > 0: by_setup[s]['wins'] += 1
    for s in by_setup:
        c = by_setup[s]['count']
        by_setup[s]['win_rate'] = round(by_setup[s]['wins']/c*100) if c else 0
    by_emo = {}
    for t in trades:
        e = t.get('emotion') or '—'
        if e not in by_emo: by_emo[e] = {'count':0,'pnl':0.0}
        by_emo[e]['count'] += 1
        by_emo[e]['pnl']    = round(by_emo[e]['pnl'] + (t['pnl'] or 0), 1)
    return dict(total=total,wins=len(wins),losses=len(losses),win_rate=wr,
                total_pnl=tot_pnl,avg_win=avg_win,avg_loss=avg_loss,
                best=best,worst=worst,expectancy=exp,
                by_setup=by_setup,by_emotion=by_emo)

# ─────────────────────────────────────────────
# SESSION CACHE
# ─────────────────────────────────────────────
SESSION_CACHE = {"obj": None, "logged_in_at": 0, "ttl_seconds": 3600}

ENGINE = {
    "last_update":0,"tick_ttl":5,"payload":None,
    "last_spot":0.0,"velocity":0.0,
    "status":"BLOCKED","entry":0.0,"target":0.0,"sl":0.0,
    "signal":"SYSTEM INITIALIZING...",
    "session_pnl":0.0,"trades_total":0,"trades_won":0,"trades_lost":0,
    "last_signal_sent":"","last_auto_entry":0,
}

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def _tg(msg):
    tok  = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id":chat,"text":msg,"parse_mode":"HTML"},timeout=4)
    except: pass

def tg(msg): threading.Thread(target=_tg,args=(msg,),daemon=True).start()

# ─────────────────────────────────────────────
# BROKER SESSION
# ─────────────────────────────────────────────
def get_session():
    global SESSION_CACHE
    now = time.time()
    if SESSION_CACHE["obj"] and (now-SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None
    for k in ["ANGEL_API_KEY","ANGEL_CLIENT_ID","ANGEL_MPIN","ANGEL_TOTP_SECRET"]:
        if not os.environ.get(k): return None, "ENV MISSING"
    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj  = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s    = obj.generateSession(os.environ["ANGEL_CLIENT_ID"],os.environ["ANGEL_MPIN"],totp)
        if not s.get("status"): return None,"LOGIN FAILED"
        SESSION_CACHE.update({"obj":obj,"logged_in_at":now})
        return obj, None
    except Exception as e: return None, str(e)

# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────
def run_pipeline():
    global ENGINE
    now = time.time()
    if ENGINE["payload"] and (now-ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]
    obj, err = get_session()
    if err: return ENGINE["payload"] or {"error": err}
    try:
        nr = obj.ltpData("NSE","NIFTY",NIFTY_TOKEN)
        vr = obj.ltpData("NSE","INDIAVIX",VIX_TOKEN)
    except Exception as e:
        SESSION_CACHE["logged_in_at"] = 0
        return ENGINE["payload"] or {"error": str(e)}
    if not nr.get("status") or "data" not in nr:
        return ENGINE["payload"] or {"error":"NIFTY FEED DEAD"}

    spot = float(nr["data"]["ltp"])
    try:    vix = float(vr["data"]["ltp"]) if vr.get("status") else 15.0
    except: vix = 15.0

    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot-ENGINE["last_spot"],2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    vix_mult = vix/15.0
    sl_pts   = round(40*vix_mult,1)
    tgt_pts  = round(90*vix_mult,1)

    base100 = (spot//100)*100
    base50  = (spot//50)*50
    dist_round = abs(spot-base50)
    chk = [
        spot > base100,
        vel  > 0,
        dist_round > 10,
        vix  < 18.0,
        (spot-base100) < (0.008*spot)
    ]
    all_pass   = all(chk)
    pass_count = sum(chk)

    # ── Auto paper trade management ──
    open_t = db_open_trade()

    if ENGINE["status"] == "TRADE_ACTIVE":
        if open_t and open_t['source'] == 'AUTO':
            if spot >= ENGINE["target"]:
                pnl = round(ENGINE["target"]-open_t['entry_price'],1)
                db_close_trade(open_t['id'],spot,'Target Hit ✅','Auto-closed by system','Calm',pnl)
                ENGINE["trades_won"]+=1; ENGINE["trades_total"]+=1
                ENGINE["session_pnl"]+=pnl; ENGINE["status"]="BLOCKED"
                ENGINE["signal"]=f"🎯 TARGET HIT +{pnl} pts"
                tg(f"🎯 <b>TARGET HIT</b>\n+{pnl} pts at ₹{spot}")
            elif spot <= ENGINE["sl"]:
                pnl = round(open_t['entry_price']-ENGINE["sl"],1)
                db_close_trade(open_t['id'],spot,'Stoploss Hit ❌','Auto-closed by system','Calm',-pnl)
                ENGINE["trades_lost"]+=1; ENGINE["trades_total"]+=1
                ENGINE["session_pnl"]-=pnl; ENGINE["status"]="BLOCKED"
                ENGINE["signal"]=f"🛑 SL HIT -{pnl} pts"
                tg(f"🛑 <b>SL HIT</b>\n-{pnl} pts at ₹{spot}")
    elif ENGINE["status"] in ("BLOCKED","SETUP_READY"):
        if not all_pass:
            ENGINE.update({"status":"BLOCKED","signal":"NO TRADE ZONE — checklist not cleared",
                           "entry":0.0,"target":0.0,"sl":0.0})
        else:
            entry = round(spot-8,2)
            ENGINE.update({"status":"SETUP_READY","entry":entry,
                           "target":round(entry+tgt_pts,2),"sl":round(entry-sl_pts,2),
                           "signal":f"SETUP READY — Pullback to ₹{entry}"})
            if spot <= (entry+5) and vel > 0.5 and not open_t:
                sig_key = f"LONG_{entry}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key
                    ENGINE["status"]  = "TRADE_ACTIVE"
                    ENGINE["signal"]  = f"🔥 LONG AUTO-ENTERED at ₹{spot}"
                    # Auto paper trade entry
                    db_insert_trade({
                        "direction":"LONG","entry_price":spot,
                        "target":ENGINE["target"],"sl":ENGINE["sl"],
                        "qty":1,"setup":"System Signal","source":"AUTO",
                        "note":f"Auto-entered by GOAT PRO signal. VIX:{vix} Vel:{vel}",
                        "entry_time":time.strftime("%H:%M:%S")
                    })
                    tg(f"🟢 <b>AUTO LONG</b>\n₹{spot} | TGT:₹{ENGINE['target']} SL:₹{ENGINE['sl']}")

    total    = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"]/total*100) if total else 0
    payload  = dict(
        spot=round(spot,2), vix=round(vix,2), velocity=vel,
        status=ENGINE["status"], signal=ENGINE["signal"],
        entry=ENGINE["entry"], target=ENGINE["target"], sl=ENGINE["sl"],
        chk=chk, pass_count=pass_count,
        total=total, wins=ENGINE["trades_won"], losses=ENGINE["trades_lost"],
        pnl=round(ENGINE["session_pnl"],1), win_rate=win_rate,
    )
    ENGINE.update({"last_update":now,"payload":payload})
    return payload

# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="5">
<title>GOAT PRO V15</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700;900&family=JetBrains+Mono:wght@400;700&display=swap');
body{font-family:'Space Grotesk',sans-serif;background:#060b16;color:#e2e8f0}
.mono{font-family:'JetBrains Mono',monospace}
.glow-g{box-shadow:0 0 16px rgba(16,185,129,.2)}
.glow-b{box-shadow:0 0 16px rgba(59,130,246,.2)}
.glow-a{box-shadow:0 0 16px rgba(245,158,11,.15)}
.tab-btn{transition:all .15s;cursor:pointer}
.tab-btn.on{background:#1e3a5f;color:#60a5fa;border-color:#3b82f6}
.emo-btn{cursor:pointer;transition:all .15s}
.emo-btn.on{border-color:#b45309;background:#1c1007;color:#fbbf24}
</style>
</head>
<body class="p-3">
<div class="max-w-sm mx-auto space-y-3">

<!-- HEADER -->
<div class="flex justify-between items-center pb-2 border-b border-slate-800">
  <div>
    <p class="mono text-[9px] text-slate-500 tracking-[.2em] uppercase">Nifty Exclusive · Angel One</p>
    <h1 class="text-lg font-black"><span class="text-blue-500">GOAT PRO</span> <span class="text-slate-400 text-sm font-medium">V15</span></h1>
  </div>
  <span class="mono text-[9px] px-2 py-1 rounded border border-emerald-800 bg-emerald-950 text-emerald-400 animate-pulse">⚡ 5s LIVE</span>
</div>

<!-- PRICE + VIX -->
<div class="grid grid-cols-2 gap-2">
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
    <div class="absolute top-0 inset-x-0 h-[2px] bg-gradient-to-r from-blue-600 to-cyan-500"></div>
    <p class="mono text-[9px] text-slate-500 uppercase tracking-widest">Nifty Spot</p>
    <p class="mono text-2xl font-black text-white mt-0.5">₹{{ m.spot }}</p>
    <p class="mono text-[9px] mt-1 font-bold {{ 'text-emerald-400' if m.velocity>0 else 'text-red-400' if m.velocity<0 else 'text-slate-500' }}">
      {{ '+' if m.velocity>0 else '' }}{{ m.velocity }} pts/tick
    </p>
  </div>
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
    <div class="absolute top-0 inset-x-0 h-[2px] {{ 'bg-red-500' if m.vix>18 else 'bg-purple-500' }}"></div>
    <p class="mono text-[9px] text-slate-500 uppercase tracking-widest">India VIX</p>
    <p class="mono text-2xl font-black mt-0.5 {{ 'text-red-400' if m.vix>18 else 'text-purple-400' }}">{{ m.vix }}</p>
    <p class="mono text-[9px] mt-1 text-slate-500">{{ 'HIGH RISK' if m.vix>18 else 'STABLE' }}</p>
  </div>
</div>

<!-- EXECUTION MATRIX -->
<div class="bg-slate-900 border rounded-lg p-3 space-y-2
  {% if m.status=='TRADE_ACTIVE' %}border-emerald-600 glow-g
  {% elif m.status=='SETUP_READY' %}border-blue-600 glow-b
  {% else %}border-slate-700{% endif %}">
  <div class="flex justify-between items-center">
    <span class="mono text-[9px] font-bold text-slate-400 tracking-widest uppercase">Execution Matrix</span>
    {% if m.status=='TRADE_ACTIVE' %}
      <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">🟢 AUTO-TRADE ACTIVE</span>
    {% elif m.status=='SETUP_READY' %}
      <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-blue-950 text-blue-400 border border-blue-800">⏳ SETUP READY</span>
    {% else %}
      <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-red-950 text-red-500 border border-red-900">🚫 BLOCKED</span>
    {% endif %}
  </div>
  <div class="bg-black/40 rounded border border-slate-800 px-3 py-2 text-center">
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest mb-1">Signal</p>
    <p class="font-black text-sm {{ 'text-emerald-400' if m.status=='TRADE_ACTIVE' else 'text-white' if m.status=='SETUP_READY' else 'text-red-400' }}">{{ m.signal }}</p>
  </div>
  <div class="grid grid-cols-3 gap-1.5 text-center">
    {% for lbl,val,col in [('ENTRY',m.entry,'text-blue-400'),('TARGET',m.target,'text-emerald-400'),('SL',m.sl,'text-red-400')] %}
    <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
      <p class="mono text-[8px] text-slate-600 uppercase">{{ lbl }}</p>
      <p class="mono text-xs font-black {{ col }}">{% if val>0 %}₹{{ val }}{% else %}---{% endif %}</p>
    </div>
    {% endfor %}
  </div>
</div>

<!-- 5/5 CHECKLIST -->
<div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
  <div class="flex justify-between items-center mb-2">
    <p class="mono text-[9px] text-slate-500 uppercase tracking-widest">Strict 5/5 Gate</p>
    <p class="mono text-[9px] font-bold {{ 'text-emerald-400' if m.pass_count==5 else 'text-amber-400' if m.pass_count>=3 else 'text-red-400' }}">{{ m.pass_count }}/5 PASS</p>
  </div>
  <ul class="space-y-1.5">
    {% for lbl,res in [('Price above trend base',m.chk[0]),('Order flow positive',m.chk[1]),('Away from whipsaw zone',m.chk[2]),('VIX stable (< 18)',m.chk[3]),('No overextension',m.chk[4])] %}
    <li class="flex justify-between">
      <span class="mono text-[9px] text-slate-400">{{ lbl }}</span>
      <span class="mono text-[9px] font-bold {{ 'text-emerald-500' if res else 'text-red-500' }}">{{ 'PASS ✓' if res else 'FAIL ✗' }}</span>
    </li>
    {% endfor %}
  </ul>
</div>

<!-- SESSION PERF -->
<div class="bg-slate-900 border border-slate-800 rounded-lg p-3 space-y-2">
  <div class="flex justify-between items-center">
    <p class="mono text-[9px] text-slate-500 uppercase tracking-widest">Session Performance</p>
    <p class="mono text-[9px] font-black {{ 'text-emerald-400' if m.win_rate>=60 else 'text-amber-400' if m.win_rate>=40 else 'text-red-400' }}">{{ m.win_rate }}% WIN-RATE</p>
  </div>
  <div class="w-full bg-slate-950 rounded-full h-1.5 border border-slate-800">
    <div class="h-full rounded-full {{ 'bg-emerald-500' if m.win_rate>=60 else 'bg-amber-500' if m.win_rate>=40 else 'bg-red-500' }}" style="width:{{ m.win_rate }}%;transition:width .8s"></div>
  </div>
  <div class="grid grid-cols-4 gap-1 text-center">
    {% for lbl,val,col in [('TRADES',m.total,'text-white'),('WINS',m.wins,'text-emerald-400'),('LOSS',m.losses,'text-red-400'),('P&L',m.pnl,('text-emerald-400' if m.pnl>=0 else 'text-red-400'))] %}
    <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
      <p class="mono text-[8px] text-slate-600">{{ lbl }}</p>
      <p class="mono text-xs font-black {{ col }}">{{ ('+' if val>=0 else '') if lbl=='P&L' else '' }}{{ val }}</p>
    </div>
    {% endfor %}
  </div>
  {% if m.total==0 %}
  <p class="mono text-[8px] text-slate-600 text-center">No trades this session.</p>
  {% elif m.win_rate>=60 %}
  <p class="mono text-[8px] text-emerald-600 text-center">✓ System running above expectation</p>
  {% elif m.win_rate>=40 %}
  <p class="mono text-[8px] text-amber-600 text-center">⚠ Marginal — review setup conditions</p>
  {% else %}
  <p class="mono text-[8px] text-red-600 text-center">✗ Poor session — halt new entries</p>
  {% endif %}
</div>

<!-- ══════════════════════════════════════════ -->
<!--          PAPER TRADE LAB                  -->
<!-- ══════════════════════════════════════════ -->
<div class="bg-slate-900 border border-amber-900/40 rounded-lg overflow-hidden glow-a">
  <!-- header -->
  <div class="flex items-center justify-between px-3 py-2 border-b border-slate-800 bg-amber-950/10">
    <div class="flex items-center gap-2">
      <span class="text-amber-400">📋</span>
      <p class="mono text-[10px] font-black text-amber-400 uppercase tracking-widest">Paper Trade Lab</p>
    </div>
    {% if open_trade %}
    <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 animate-pulse">● POSITION OPEN</span>
    {% else %}
    <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 border border-slate-700">NO POSITION</span>
    {% endif %}
  </div>

  <!-- tabs -->
  <div class="grid grid-cols-4 border-b border-slate-800" id="ptTabs">
    {% for t in ['Journal','Exit','Analysis','Intro'] %}
    <button onclick="ptTab('{{ t.lower() }}')" id="ptt-{{ t.lower() }}"
      class="tab-btn mono text-[9px] py-2 text-slate-400 border-b-2 border-transparent {{ 'on border-blue-500' if loop.first else '' }}
             {% if not loop.last %}border-r border-slate-800{% endif %}">
      {{ t.upper() }}
    </button>
    {% endfor %}
  </div>

  <!-- JOURNAL TAB -->
  <div id="pt-journal" class="p-3 space-y-2">
    {% if open_trade %}
    <div class="bg-emerald-950/20 border border-emerald-800/40 rounded-lg p-3 space-y-2">
      <div class="flex justify-between items-center">
        <span class="mono text-[9px] font-black text-emerald-400 uppercase">Active: {{ open_trade.direction }} · {{ open_trade.setup }}</span>
        <span class="mono text-[8px] text-slate-500">{{ open_trade.entry_time }}</span>
      </div>
      <div class="grid grid-cols-3 gap-1.5 text-center">
        <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
          <p class="mono text-[8px] text-slate-600">ENTRY</p>
          <p class="mono text-xs font-black text-blue-400">₹{{ open_trade.entry_price }}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
          <p class="mono text-[8px] text-slate-600">TARGET</p>
          <p class="mono text-xs font-black text-emerald-400">{% if open_trade.target %}₹{{ open_trade.target }}{% else %}---{% endif %}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
          <p class="mono text-[8px] text-slate-600">SL</p>
          <p class="mono text-xs font-black text-red-400">{% if open_trade.sl %}₹{{ open_trade.sl }}{% else %}---{% endif %}</p>
        </div>
      </div>
      {% set unreal = (m.spot - open_trade.entry_price) if open_trade.direction=='LONG' else (open_trade.entry_price - m.spot) %}
      <div class="bg-black/40 rounded border border-slate-800 py-2 text-center">
        <p class="mono text-[8px] text-slate-500 uppercase">Unrealized P&L</p>
        <p class="mono text-xl font-black {{ 'text-emerald-400' if unreal>=0 else 'text-red-400' }}">
          {{ '+' if unreal>=0 else '' }}{{ '%.1f'|format(unreal) }} pts
        </p>
      </div>
      <p class="mono text-[8px] text-slate-600 text-center">Source: {{ open_trade.source }} · {{ open_trade.note[:50] if open_trade.note else '' }}</p>
    </div>
    {% endif %}

    {% if closed_trades %}
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest">Completed Trades</p>
    {% for t in closed_trades %}
    <div class="border rounded-lg p-2.5 {{ 'border-emerald-900/60 bg-emerald-950/10' if (t.pnl or 0)>0 else 'border-red-900/60 bg-red-950/10' }}">
      <div class="flex justify-between items-center mb-1.5">
        <div class="flex gap-1.5 items-center">
          <span class="mono text-[8px] font-black px-1.5 py-0.5 rounded {{ 'bg-emerald-950 text-emerald-400' if t.direction=='LONG' else 'bg-red-950 text-red-400' }}">{{ t.direction }}</span>
          <span class="mono text-[8px] text-slate-500">{{ t.setup }}</span>
          {% if t.source=='AUTO' %}<span class="mono text-[7px] text-purple-400">AUTO</span>{% endif %}
        </div>
        <span class="mono text-sm font-black {{ 'text-emerald-400' if (t.pnl or 0)>0 else 'text-red-400' }}">
          {{ '+' if (t.pnl or 0)>0 else '' }}{{ '%.1f'|format(t.pnl or 0) }} pts
        </span>
      </div>
      <div class="grid grid-cols-3 gap-1 mono text-[8px] text-slate-500">
        <div>In: <span class="text-white">₹{{ t.entry_price }}</span></div>
        <div>Out: <span class="text-white">₹{{ t.exit_price or '--' }}</span></div>
        <div>{{ t.entry_time }}→{{ t.exit_time or '--' }}</div>
      </div>
      {% if t.exit_reason %}<p class="mono text-[8px] text-amber-500 mt-1">{{ t.exit_reason }}</p>{% endif %}
      {% if t.emotion   %}<p class="mono text-[8px] text-purple-400">🧠 {{ t.emotion }}</p>{% endif %}
      {% if t.post_note %}<p class="mono text-[8px] text-slate-500 italic mt-1">"{{ t.post_note[:80] }}"</p>{% endif %}
    </div>
    {% endfor %}
    {% else %}
    <div class="text-center py-6 mono text-[10px] text-slate-600">
      No completed trades yet.<br>
      <span class="text-[9px] text-slate-700">System will auto-enter when 5/5 pass + velocity confirmed.</span>
    </div>
    {% endif %}
  </div>

  <!-- EXIT TAB -->
  <div id="pt-exit" class="p-3 hidden">
    {% if open_trade %}
    <p class="mono text-[9px] text-slate-500 uppercase tracking-widest mb-3">Close: {{ open_trade.direction }} @ ₹{{ open_trade.entry_price }}</p>
    <form onsubmit="submitExit(event)" class="space-y-2">
      <div>
        <label class="mono text-[8px] text-slate-500 uppercase">Exit Price</label>
        <input name="exit_price" type="number" step="0.05" value="{{ m.spot }}"
          class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white outline-none focus:border-blue-500">
      </div>
      <div>
        <label class="mono text-[8px] text-slate-500 uppercase">Exit Reason</label>
        <select name="exit_reason" class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white outline-none">
          <option>Target Hit ✅</option><option>Stoploss Hit ❌</option>
          <option>Manual — Profit</option><option>Manual — Loss</option>
          <option>Time Exit (EOD)</option><option>Setup Invalidated</option>
        </select>
      </div>
      <div>
        <label class="mono text-[8px] text-slate-500 uppercase">Post-Trade Note</label>
        <textarea name="post_note" rows="2" placeholder="What happened? What to improve?"
          class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-[10px] text-white outline-none resize-none"></textarea>
      </div>
      <div>
        <label class="mono text-[8px] text-slate-500 uppercase block mb-1">Emotion During Trade</label>
        <div class="grid grid-cols-4 gap-1">
          {% for e in ['Calm','Confident','Anxious','FOMO','Greedy','Disciplined','Revenge','Fearful'] %}
          <div class="emo-btn mono text-[8px] text-center py-1 rounded border border-slate-700 text-slate-500 {{ 'on' if e=='Calm' else '' }}"
               onclick="selEmo(this,'{{ e }}')">{{ e }}</div>
          {% endfor %}
        </div>
      </div>
      <input type="hidden" name="trade_id" value="{{ open_trade.id }}">
      <input type="hidden" name="direction" value="{{ open_trade.direction }}">
      <input type="hidden" name="entry_price" value="{{ open_trade.entry_price }}">
      <input type="hidden" id="emoVal" name="emotion" value="Calm">
      <button type="submit" class="w-full py-2 rounded bg-amber-600 hover:bg-amber-500 mono text-[10px] font-black text-white">
        🔒 CLOSE & SAVE TRADE
      </button>
    </form>
    {% else %}
    <div class="text-center py-8 mono text-[10px] text-slate-600">No open trade to close.</div>
    {% endif %}
  </div>

  <!-- ANALYSIS TAB -->
  <div id="pt-analysis" class="p-3 hidden">
    {% if stats.total > 0 %}
    <div class="grid grid-cols-2 gap-2 mb-3">
      {% for lbl,val,col in [
        ('Win Rate',stats.win_rate|string+'%','text-emerald-400' if stats.win_rate>=60 else 'text-amber-400' if stats.win_rate>=40 else 'text-red-400'),
        ('Total P&L',('+' if stats.total_pnl>=0 else '')|string+stats.total_pnl|string,'text-emerald-400' if stats.total_pnl>=0 else 'text-red-400'),
        ('Avg Win','+'+stats.avg_win|string,'text-emerald-400'),
        ('Avg Loss',stats.avg_loss|string,'text-red-400'),
        ('Best','+'+stats.best|string,'text-emerald-400'),
        ('Worst',stats.worst|string,'text-red-400')
      ] %}
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5 text-center">
        <p class="mono text-[8px] text-slate-600 uppercase">{{ lbl }}</p>
        <p class="mono text-lg font-black {{ col }}">{{ val }}</p>
      </div>
      {% endfor %}
    </div>
    <!-- Expectancy -->
    <div class="bg-[#060b16] border border-slate-800 rounded p-2.5 mb-3">
      <p class="mono text-[8px] text-slate-600 uppercase mb-1">System Expectancy / trade</p>
      <p class="mono text-xl font-black {{ 'text-emerald-400' if stats.expectancy>=0 else 'text-red-400' }}">
        {{ '+' if stats.expectancy>=0 else '' }}{{ stats.expectancy }} pts
      </p>
      <p class="mono text-[8px] text-slate-500 mt-1">
        {% if stats.expectancy > 5 %}Edge STRONG — scale up carefully
        {% elif stats.expectancy > 0 %}Edge marginal — need more trades
        {% else %}Negative — DO NOT go live yet{% endif %}
      </p>
    </div>
    <!-- Setup breakdown -->
    {% if stats.by_setup %}
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest mb-1">Setup Breakdown</p>
    {% for setup,sd in stats.by_setup.items() %}
    <div class="flex justify-between items-center bg-[#060b16] border border-slate-800 rounded px-2 py-1.5 mb-1">
      <span class="mono text-[9px] text-slate-300">{{ setup }}</span>
      <div class="flex gap-2 mono text-[8px]">
        <span class="text-slate-500">{{ sd.count }}T</span>
        <span class="{{ 'text-emerald-400' if sd.win_rate>=50 else 'text-red-400' }}">{{ sd.win_rate }}%W</span>
        <span class="{{ 'text-emerald-400' if sd.pnl>=0 else 'text-red-400' }}">{{ '+' if sd.pnl>=0 else '' }}{{ sd.pnl }}pts</span>
      </div>
    </div>
    {% endfor %}
    {% endif %}
    <!-- Emotion breakdown -->
    {% if stats.by_emotion %}
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest mt-3 mb-1">Emotion vs P&L</p>
    {% for emo,ed in stats.by_emotion.items() %}
    <div class="flex justify-between items-center bg-[#060b16] border border-slate-800 rounded px-2 py-1.5 mb-1">
      <span class="mono text-[9px] text-slate-300">🧠 {{ emo }}</span>
      <div class="flex gap-2 mono text-[8px]">
        <span class="text-slate-500">{{ ed.count }}T</span>
        <span class="{{ 'text-emerald-400' if ed.pnl>=0 else 'text-red-400' }}">{{ '+' if ed.pnl>=0 else '' }}{{ ed.pnl }}pts</span>
      </div>
    </div>
    {% endfor %}
    {% endif %}
    <!-- Self Insights -->
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest mt-3 mb-1">Auto Insights</p>
    {% if stats.expectancy < 0 %}
    <div class="border-l-2 border-red-600 pl-2 mb-1 mono text-[9px] text-slate-300">Negative expectancy — system ka edge nahi. Live mat jao.</div>
    {% elif stats.expectancy < 3 %}
    <div class="border-l-2 border-amber-600 pl-2 mb-1 mono text-[9px] text-slate-300">Edge marginal — zyada data lo pehle.</div>
    {% else %}
    <div class="border-l-2 border-emerald-600 pl-2 mb-1 mono text-[9px] text-slate-300">System kaam kar raha hai. Consistency maintain karo.</div>
    {% endif %}
    {% for emo,ed in stats.by_emotion.items() %}
    {% if ed.pnl < 0 %}
    <div class="border-l-2 border-red-800 pl-2 mb-1 mono text-[9px] text-slate-400">"{{ emo }}" emotion mein loss ho raha hai — in states mein rukna seekho.</div>
    {% endif %}
    {% endfor %}
    <button onclick="if(confirm('Clear all?'))fetch('/paper/clear',{method:'POST'}).then(()=>location.reload())"
      class="w-full mt-3 py-1.5 rounded border border-red-900 bg-red-950/20 mono text-[9px] text-red-500">
      🗑 Clear All Paper Trades
    </button>
    {% else %}
    <div class="text-center py-6 mono text-[10px] text-slate-600">Complete trades needed for analysis.</div>
    {% endif %}
  </div>

  <!-- INTROSPECTION TAB -->
  <div id="pt-intro" class="p-3 hidden">
    <form onsubmit="submitIntro(event)" class="space-y-3">
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5">
        <p class="mono text-[9px] text-slate-400 mb-2">1. Rule follow kiya ya emotion se trade liya? (1=emotion, 5=rules)</p>
        <div class="flex gap-2" id="iq1">
          {% for i in range(1,6) %}
          <div onclick="setScore('iq1',{{ i }})" id="iq1-{{ i }}"
            class="w-8 h-8 rounded-full border border-slate-700 bg-[#060b16] flex items-center justify-center mono text-[9px] text-slate-500 cursor-pointer hover:border-blue-600">{{ i }}</div>
          {% endfor %}
        </div>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5">
        <p class="mono text-[9px] text-slate-400 mb-2">2. SL ke bina trade liya? &nbsp; 3. Revenge trade liya?</p>
        <div class="flex gap-4">
          <div class="flex gap-2 items-center">
            <span class="mono text-[8px] text-slate-500">SL Skip:</span>
            <div onclick="setBool2('slSkip','Yes',this)" id="ss-yes" class="emo-btn mono text-[8px] px-2 py-0.5 rounded border border-slate-700 text-slate-500">Yes</div>
            <div onclick="setBool2('slSkip','No',this)"  id="ss-no"  class="emo-btn on mono text-[8px] px-2 py-0.5 rounded border border-slate-700 text-slate-500">No</div>
          </div>
          <div class="flex gap-2 items-center">
            <span class="mono text-[8px] text-slate-500">Revenge:</span>
            <div onclick="setBool2('rv','Yes',this)" id="rv-yes" class="emo-btn mono text-[8px] px-2 py-0.5 rounded border border-slate-700 text-slate-500">Yes</div>
            <div onclick="setBool2('rv','No',this)"  id="rv-no"  class="emo-btn on mono text-[8px] px-2 py-0.5 rounded border border-slate-700 text-slate-500">No</div>
          </div>
        </div>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5">
        <p class="mono text-[9px] text-slate-400 mb-2">4. Aaj ka discipline score (1–5)</p>
        <div class="flex gap-2" id="iq4">
          {% for i in range(1,6) %}
          <div onclick="setScore('iq4',{{ i }})" id="iq4-{{ i }}"
            class="w-8 h-8 rounded-full border border-slate-700 bg-[#060b16] flex items-center justify-center mono text-[9px] text-slate-500 cursor-pointer">{{ i }}</div>
          {% endfor %}
        </div>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5">
        <p class="mono text-[9px] text-slate-400 mb-1">5. Kal ke liye ek pakka rule:</p>
        <textarea name="tomorrow_rule" rows="2" placeholder="e.g. SL nahi toh trade nahi..."
          class="w-full bg-transparent border-0 mono text-[10px] text-white outline-none resize-none"></textarea>
      </div>
      <input type="hidden" id="iScore1" name="rule_followed" value="3">
      <input type="hidden" id="iScore4" name="discipline" value="3">
      <input type="hidden" id="iSlSkip" name="sl_skip" value="No">
      <input type="hidden" id="iRevenge" name="revenge" value="No">
      <button type="submit" class="w-full py-2 rounded bg-blue-700 hover:bg-blue-600 mono text-[10px] font-black text-white">
        💾 SAVE TODAY'S INTROSPECTION
      </button>
    </form>
    <!-- History -->
    {% if intros %}
    <p class="mono text-[8px] text-slate-600 uppercase tracking-widest mt-4 mb-2">Recent Entries</p>
    {% for e in intros %}
    <div class="bg-[#060b16] border border-slate-800 rounded p-2.5 mb-2">
      <div class="flex justify-between mono text-[8px] text-slate-500 mb-1">
        <span>{{ e.date }}</span>
        <span class="{{ 'text-emerald-400' if e.discipline>=4 else 'text-amber-400' if e.discipline>=3 else 'text-red-400' }}">Discipline: {{ e.discipline }}/5</span>
      </div>
      {% if e.sl_skip=='Yes' %}<p class="mono text-[8px] text-red-400">⚠ SL skip kiya</p>{% endif %}
      {% if e.revenge=='Yes' %}<p class="mono text-[8px] text-red-400">⚠ Revenge trade</p>{% endif %}
      {% if e.tomorrow_rule %}<p class="mono text-[8px] text-purple-400 mt-1">📌 {{ e.tomorrow_rule }}</p>{% endif %}
    </div>
    {% endfor %}
    {% endif %}
  </div>

</div><!-- end paper lab -->

</div><!-- max-w -->

<script>
// ── Tab ──
function ptTab(name) {
  ['journal','exit','analysis','intro'].forEach(n => {
    document.getElementById('pt-'+n).classList.toggle('hidden', n!==name);
    document.getElementById('ptt-'+n).classList.toggle('on', n===name);
  });
}

// ── Emotion ──
function selEmo(el, val) {
  document.querySelectorAll('.emo-btn').forEach(b=>b.classList.remove('on'));
  el.classList.add('on');
  document.getElementById('emoVal').value = val;
}

// ── Intro scores ──
const introScores = {iq1:3, iq4:3};
function setScore(grp, val) {
  introScores[grp] = val;
  for(let i=1;i<=5;i++){
    const el = document.getElementById(grp+'-'+i);
    el.classList.toggle('border-blue-500', i<=val);
    el.classList.toggle('bg-blue-950/40', i<=val);
    el.classList.toggle('text-blue-400', i<=val);
    el.classList.toggle('border-slate-700', i>val);
    el.classList.toggle('text-slate-500', i>val);
  }
  document.getElementById(grp==='iq1'?'iScore1':'iScore4').value = val;
}

const boolState = {slSkip:'No', rv:'No'};
function setBool2(key, val, el) {
  boolState[key] = val;
  const prefix = key==='slSkip'?'ss':'rv';
  document.getElementById(prefix+'-yes').classList.remove('on');
  document.getElementById(prefix+'-no').classList.remove('on');
  el.classList.add('on');
  document.getElementById(key==='slSkip'?'iSlSkip':'iRevenge').value = val;
}

// ── Submit exit ──
function submitExit(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  fetch('/paper/exit', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      trade_id:    parseInt(fd.get('trade_id')),
      direction:   fd.get('direction'),
      entry_price: parseFloat(fd.get('entry_price')),
      exit_price:  parseFloat(fd.get('exit_price')),
      exit_reason: fd.get('exit_reason'),
      post_note:   fd.get('post_note'),
      emotion:     fd.get('emotion'),
    })
  }).then(()=>location.reload());
}

// ── Submit introspection ──
function submitIntro(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  fetch('/paper/intro', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      rule_followed: parseInt(fd.get('rule_followed')),
      discipline:    parseInt(fd.get('discipline')),
      sl_skip:       fd.get('sl_skip'),
      revenge:       fd.get('revenge'),
      tomorrow_rule: fd.get('tomorrow_rule'),
    })
  }).then(()=>{ alert('Saved!'); location.reload(); });
}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return (f"<div style='background:#060b16;color:#f87171;font-family:monospace;"
                f"padding:40px;min-height:100vh;display:flex;flex-direction:column;"
                f"justify-content:center;align-items:center;text-align:center'>"
                f"<h2>🚨 ENGINE HALTED</h2>"
                f"<p style='color:#94a3b8;margin-top:12px'>{data['error']}</p>"
                f"<p style='color:#334155;font-size:11px;margin-top:20px'>"
                f"Check Render Environment Variables</p></div>")
    closed = db_closed_trades()
    return render_template_string(TEMPLATE,
        m=data,
        open_trade=db_open_trade(),
        closed_trades=closed,
        stats=calc_stats(closed),
        intros=db_get_intros(5))


@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    d = request.get_json()
    tid   = d.get("trade_id")
    ep    = float(d.get("exit_price", 0))
    dir_  = d.get("direction","LONG")
    enp   = float(d.get("entry_price", 0))
    pnl   = round((ep-enp) if dir_=="LONG" else (enp-ep), 2)
    db_close_trade(tid, ep, d.get("exit_reason",""), d.get("post_note",""), d.get("emotion",""), pnl)
    # update session counters
    if pnl > 0: ENGINE["trades_won"]+=1
    else:        ENGINE["trades_lost"]+=1
    ENGINE["trades_total"]+=1
    ENGINE["session_pnl"] = round(ENGINE["session_pnl"]+pnl, 1)
    return jsonify({"status":"ok","pnl":pnl})


@app.route("/paper/intro", methods=["POST"])
def paper_intro():
    d = request.get_json()
    db_add_intro({**d, "date": time.strftime("%d-%m-%Y")})
    return jsonify({"status":"ok"})


@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    db_clear_trades()
    return jsonify({"status":"ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
