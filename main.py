import os
import time
import threading
import requests
import pyotp
from flask import Flask, render_template_string, request, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

NIFTY_TOKEN = "99926000"
VIX_TOKEN   = "99926017"

SESSION_CACHE = {"obj": None, "logged_in_at": 0, "ttl_seconds": 3600}

ENGINE = {
    "last_update": 0, "tick_ttl": 5, "payload": None,
    "last_spot": 0.0, "velocity": 0.0,
    "status": "BLOCKED", "entry": 0.0, "target": 0.0, "sl": 0.0,
    "signal": "SYSTEM INITIALIZING...",
    "session_pnl": 0.0, "trades_total": 0, "trades_won": 0,
    "trades_lost": 0, "last_signal_sent": "",
}

# ── PAPER TRADE JOURNAL (in-memory) ──
PAPER_TRADES = []   # list of trade dicts
PAPER_OPEN   = None # currently open paper trade

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="5">
  <title>GOAT PRO V14</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');
    body  { font-family:'Space Grotesk',sans-serif; }
    .mono { font-family:'JetBrains Mono',monospace; }
    .glow-green { box-shadow:0 0 18px rgba(16,185,129,.18); }
    .glow-blue  { box-shadow:0 0 18px rgba(59,130,246,.18); }
    .glow-amber { box-shadow:0 0 18px rgba(245,158,11,.18); }
    .bar-fill   { transition:width .8s ease; }
    .tab-btn { transition:all .2s; }
    .tab-btn.active { background:#1e3a5f; color:#60a5fa; border-color:#3b82f6; }
  </style>
</head>
<body class="bg-[#060b16] text-slate-200 p-3">
<div class="max-w-sm mx-auto space-y-3">

  <!-- HEADER -->
  <div class="flex justify-between items-center pb-2 border-b border-slate-800">
    <div>
      <p class="mono text-[9px] text-slate-500 tracking-[.2em] uppercase">Nifty Exclusive · Angel One Feed</p>
      <h1 class="text-lg font-black tracking-tight">
        <span class="text-blue-500">GOAT PRO</span>
        <span class="text-slate-400 text-sm font-medium ml-1">V14</span>
      </h1>
    </div>
    <span class="mono text-[9px] px-2 py-1 rounded border border-emerald-800 bg-emerald-950 text-emerald-400 animate-pulse">⚡ 5s LIVE</span>
  </div>

  <!-- PRICE + VIX -->
  <div class="grid grid-cols-2 gap-2">
    <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
      <div class="absolute top-0 inset-x-0 h-[2px] bg-gradient-to-r from-blue-600 to-cyan-500"></div>
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Nifty Spot</p>
      <p class="mono text-2xl font-black text-white mt-0.5">₹{{ m.spot }}</p>
      <p class="mono text-[9px] mt-1 font-bold {{ 'text-emerald-400' if m.velocity > 0 else 'text-red-400' if m.velocity < 0 else 'text-slate-500' }}">
        {{ '+' if m.velocity > 0 else '' }}{{ m.velocity }} pts/tick
      </p>
    </div>
    <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
      <div class="absolute top-0 inset-x-0 h-[2px] {{ 'bg-red-500' if m.vix > 18 else 'bg-purple-500' }}"></div>
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">India VIX</p>
      <p class="mono text-2xl font-black mt-0.5 {{ 'text-red-400' if m.vix > 18 else 'text-purple-400' }}">{{ m.vix }}</p>
      <p class="mono text-[9px] mt-1 text-slate-500">{{ 'HIGH RISK' if m.vix > 18 else 'STABLE' }}</p>
    </div>
  </div>

  <!-- EXECUTION MATRIX -->
  <div class="bg-slate-900 border rounded-lg p-3 space-y-2
    {% if m.status=='TRADE_ACTIVE' %}border-emerald-600 glow-green
    {% elif m.status=='SETUP_READY' %}border-blue-600 glow-blue
    {% else %}border-slate-700{% endif %}">
    <div class="flex justify-between items-center">
      <span class="mono text-[9px] font-bold text-slate-400 tracking-widest uppercase">Execution Matrix</span>
      {% if m.status=='TRADE_ACTIVE' %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">🟢 LIVE POSITION</span>
      {% elif m.status=='SETUP_READY' %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-blue-950 text-blue-400 border border-blue-800">⏳ AWAITING REVERSAL</span>
      {% else %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-red-950 text-red-500 border border-red-900">🚫 BLOCKED</span>
      {% endif %}
    </div>
    <div class="bg-black/40 rounded border border-slate-800 px-3 py-2 text-center">
      <p class="mono text-[8px] text-slate-600 tracking-widest uppercase mb-1">Signal</p>
      <p class="font-black text-sm {{ 'text-emerald-400' if m.status=='TRADE_ACTIVE' else 'text-white' if m.status=='SETUP_READY' else 'text-red-400' }}">{{ m.signal }}</p>
    </div>
    <div class="grid grid-cols-3 gap-1.5 text-center">
      {% for label,val,color in [('ENTRY',m.entry,'text-blue-400'),('TARGET',m.target,'text-emerald-400'),('STOPLOSS',m.sl,'text-red-400')] %}
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600 uppercase">{{ label }}</p>
        <p class="mono text-xs font-black {{ color }}">{% if val > 0 %}₹{{ val }}{% else %}---{% endif %}</p>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- 5/5 CHECKLIST -->
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
    <div class="flex justify-between items-center mb-2">
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Strict 5/5 Gate</p>
      <p class="mono text-[9px] font-bold {{ 'text-emerald-400' if m.pass_count==5 else 'text-amber-400' if m.pass_count>=3 else 'text-red-400' }}">{{ m.pass_count }}/5 PASS</p>
    </div>
    <ul class="space-y-1.5">
      {% for label,result in [('Price above trend base',m.chk[0]),('Order flow positive (velocity > 0)',m.chk[1]),('Away from whipsaw zone (>10 pts from round)',m.chk[2]),('VIX stable (< 18.0)',m.chk[3]),('No overextension (< 0.8% from base)',m.chk[4])] %}
      <li class="flex justify-between items-center">
        <span class="mono text-[9px] text-slate-400">{{ label }}</span>
        <span class="mono text-[9px] font-bold {{ 'text-emerald-500' if result else 'text-red-500' }}">{{ 'PASS ✓' if result else 'FAIL ✗' }}</span>
      </li>
      {% endfor %}
    </ul>
  </div>

  <!-- SESSION PERFORMANCE -->
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 space-y-2">
    <div class="flex justify-between items-center">
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Session Performance</p>
      <p class="mono text-[9px] font-black {{ 'text-emerald-400' if m.win_rate>=60 else 'text-amber-400' if m.win_rate>=40 else 'text-red-400' }}">{{ m.win_rate }}% WIN-RATE</p>
    </div>
    <div class="w-full bg-slate-950 rounded-full h-1.5 border border-slate-800">
      <div class="bar-fill h-full rounded-full {{ 'bg-emerald-500' if m.win_rate>=60 else 'bg-amber-500' if m.win_rate>=40 else 'bg-red-500' }}" style="width:{{ m.win_rate }}%"></div>
    </div>
    <div class="grid grid-cols-4 gap-1 text-center">
      {% for lbl,val,col in [('TRADES',m.total,'text-white'),('WINS',m.wins,'text-emerald-400'),('LOSS',m.losses,'text-red-400'),('P&L pts',m.pnl,('text-emerald-400' if m.pnl>=0 else 'text-red-400'))] %}
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600">{{ lbl }}</p>
        <p class="mono text-xs font-black {{ col }}">{{ ('+' if val>=0 else '') if lbl=='P&L pts' else '' }}{{ val }}</p>
      </div>
      {% endfor %}
    </div>
    {% if m.total==0 %}<p class="mono text-[8px] text-slate-600 text-center">No trades this session. Counters reset on app restart.</p>
    {% elif m.win_rate>=60 %}<p class="mono text-[8px] text-emerald-600 text-center">✓ System running above expectation</p>
    {% elif m.win_rate>=40 %}<p class="mono text-[8px] text-amber-600 text-center">⚠ Marginal edge — review setup conditions</p>
    {% else %}<p class="mono text-[8px] text-red-600 text-center">✗ Poor session — consider halting new entries</p>{% endif %}
  </div>

  <!-- ════════════════════════════════════════ -->
  <!--          PAPER TRADING SECTION          -->
  <!-- ════════════════════════════════════════ -->
  <div class="bg-slate-900 border border-amber-900/50 rounded-lg overflow-hidden" id="paperSection">

    <!-- Header -->
    <div class="flex items-center justify-between px-3 py-2 border-b border-slate-800 bg-amber-950/20">
      <div class="flex items-center gap-2">
        <span class="text-amber-400 text-sm">📋</span>
        <p class="mono text-[10px] font-black text-amber-400 tracking-widest uppercase">Paper Trade Lab</p>
      </div>
      {% if paper.open %}
      <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 animate-pulse">● POSITION OPEN</span>
      {% else %}
      <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 border border-slate-700">NO POSITION</span>
      {% endif %}
    </div>

    <!-- Tab bar -->
    <div class="flex border-b border-slate-800" id="tabBar">
      <button onclick="showTab('entry')" id="tab-entry"
        class="tab-btn active flex-1 mono text-[9px] py-2 border-r border-slate-800 text-slate-400 border-b-2 border-b-transparent">
        ENTRY
      </button>
      <button onclick="showTab('exit')" id="tab-exit"
        class="tab-btn flex-1 mono text-[9px] py-2 border-r border-slate-800 text-slate-400 border-b-2 border-b-transparent">
        EXIT
      </button>
      <button onclick="showTab('journal')" id="tab-journal"
        class="tab-btn flex-1 mono text-[9px] py-2 border-r border-slate-800 text-slate-400 border-b-2 border-b-transparent">
        JOURNAL
      </button>
      <button onclick="showTab('analysis')" id="tab-analysis"
        class="tab-btn flex-1 mono text-[9px] py-2 text-slate-400 border-b-2 border-b-transparent">
        ANALYSIS
      </button>
    </div>

    <!-- TAB: ENTRY -->
    <div id="tab-entry-content" class="p-3 space-y-2">
      {% if paper.open %}
      <!-- Open trade info -->
      <div class="bg-emerald-950/30 border border-emerald-800/40 rounded-lg p-3 space-y-1.5">
        <p class="mono text-[9px] text-emerald-400 font-bold uppercase tracking-wider">Active Paper Trade</p>
        <div class="grid grid-cols-2 gap-2 text-[10px] mono">
          <div><span class="text-slate-500">Direction:</span> <span class="font-black {{ 'text-emerald-400' if paper.open.direction=='LONG' else 'text-red-400' }}">{{ paper.open.direction }}</span></div>
          <div><span class="text-slate-500">Entry:</span> <span class="font-black text-blue-400">₹{{ paper.open.entry_price }}</span></div>
          <div><span class="text-slate-500">Target:</span> <span class="font-black text-emerald-400">₹{{ paper.open.target }}</span></div>
          <div><span class="text-slate-500">SL:</span> <span class="font-black text-red-400">₹{{ paper.open.sl }}</span></div>
          <div><span class="text-slate-500">Qty:</span> <span class="font-black text-white">{{ paper.open.qty }}</span></div>
          <div><span class="text-slate-500">Setup:</span> <span class="font-black text-amber-400">{{ paper.open.setup }}</span></div>
        </div>
        {% set unreal = (m.spot - paper.open.entry_price) if paper.open.direction=='LONG' else (paper.open.entry_price - m.spot) %}
        <div class="mt-2 bg-black/40 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-500 uppercase">Unrealized P&L</p>
          <p class="mono text-lg font-black {{ 'text-emerald-400' if unreal >= 0 else 'text-red-400' }}">
            {{ '+' if unreal >= 0 else '' }}{{ '%.1f'|format(unreal) }} pts
          </p>
        </div>
      </div>
      <p class="mono text-[9px] text-slate-600 text-center">Close current trade before opening new one → go to EXIT tab</p>
      {% else %}
      <!-- New entry form -->
      <form onsubmit="submitEntry(event)" class="space-y-2">
        <!-- Direction toggle -->
        <div class="grid grid-cols-2 gap-2">
          <label class="cursor-pointer">
            <input type="radio" name="direction" value="LONG" class="sr-only" checked>
            <div class="dir-btn text-center py-2 rounded border border-emerald-800 bg-emerald-950/40 text-emerald-400 mono text-[10px] font-black" onclick="setDir(this,'LONG')">
              ▲ LONG (BUY)
            </div>
          </label>
          <label class="cursor-pointer">
            <input type="radio" name="direction" value="SHORT" class="sr-only">
            <div class="dir-btn text-center py-2 rounded border border-slate-700 bg-slate-800/40 text-slate-400 mono text-[10px] font-black" onclick="setDir(this,'SHORT')">
              ▼ SHORT (SELL)
            </div>
          </label>
        </div>

        <!-- Auto-fill from system -->
        <button type="button" onclick="autoFill()"
          class="w-full py-1.5 rounded border border-blue-800 bg-blue-950/40 mono text-[9px] text-blue-400 font-bold hover:bg-blue-900/40">
          ⚡ Auto-fill from System Signal
        </button>

        <!-- Fields -->
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="mono text-[8px] text-slate-500 uppercase">Entry Price</label>
            <input id="f-entry" name="entry_price" type="number" step="0.05"
              value="{{ m.spot }}"
              class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-blue-500 outline-none">
          </div>
          <div>
            <label class="mono text-[8px] text-slate-500 uppercase">Qty (lots)</label>
            <input id="f-qty" name="qty" type="number" min="1" value="1"
              class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-blue-500 outline-none">
          </div>
          <div>
            <label class="mono text-[8px] text-slate-500 uppercase">Target</label>
            <input id="f-target" name="target" type="number" step="0.05"
              value="{{ m.target if m.target > 0 else '' }}"
              class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-emerald-500 outline-none">
          </div>
          <div>
            <label class="mono text-[8px] text-slate-500 uppercase">Stoploss</label>
            <input id="f-sl" name="sl" type="number" step="0.05"
              value="{{ m.sl if m.sl > 0 else '' }}"
              class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-red-500 outline-none">
          </div>
        </div>

        <!-- Setup type -->
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase">Setup Type</label>
          <select name="setup" id="f-setup"
            class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-amber-500 outline-none">
            <option value="VWAP Bounce">VWAP Bounce</option>
            <option value="Breakout">Breakout</option>
            <option value="Reversal">Reversal</option>
            <option value="Pullback">Pullback</option>
            <option value="Gap Fill">Gap Fill</option>
            <option value="Support Hold">Support Hold</option>
            <option value="Momentum">Momentum</option>
            <option value="Custom">Custom</option>
          </select>
        </div>

        <!-- Notes -->
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase">Pre-Trade Note (Why?)</label>
          <textarea id="f-note" name="note" rows="2" placeholder="Why am I taking this trade? What's the thesis?"
            class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-[10px] text-white focus:border-amber-500 outline-none resize-none"></textarea>
        </div>

        <input type="hidden" id="f-dir" name="direction" value="LONG">

        <button type="submit"
          class="w-full py-2 rounded bg-blue-600 hover:bg-blue-500 mono text-[10px] font-black text-white tracking-wider">
          📋 ENTER PAPER TRADE
        </button>
      </form>
      {% endif %}
    </div>

    <!-- TAB: EXIT -->
    <div id="tab-exit-content" class="p-3 space-y-2 hidden">
      {% if paper.open %}
      <div class="bg-slate-800/40 rounded-lg p-3 space-y-1 mono text-[10px]">
        <p class="text-slate-400 font-bold uppercase text-[9px] mb-2">Closing: {{ paper.open.direction }} @ ₹{{ paper.open.entry_price }}</p>
        <div class="grid grid-cols-2 gap-1 text-[9px]">
          <div>TGT: <span class="text-emerald-400 font-bold">₹{{ paper.open.target }}</span></div>
          <div>SL: <span class="text-red-400 font-bold">₹{{ paper.open.sl }}</span></div>
        </div>
      </div>
      <form onsubmit="submitExit(event)" class="space-y-2">
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase">Exit Price</label>
          <input id="e-price" name="exit_price" type="number" step="0.05" value="{{ m.spot }}"
            class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-blue-500 outline-none">
        </div>
        <!-- Exit reason -->
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase">Exit Reason</label>
          <select name="exit_reason"
            class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-xs text-white focus:border-amber-500 outline-none">
            <option value="Target Hit">Target Hit ✅</option>
            <option value="Stoploss Hit">Stoploss Hit ❌</option>
            <option value="Manual Exit - Profit">Manual Exit — Profit</option>
            <option value="Manual Exit - Loss">Manual Exit — Loss</option>
            <option value="Time Exit">Time Exit (EOD)</option>
            <option value="Setup Invalid">Setup Invalidated</option>
          </select>
        </div>
        <!-- Post-trade note -->
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase">Post-Trade Analysis (What happened?)</label>
          <textarea name="post_note" rows="3" placeholder="What went right? What went wrong? What would I do differently?"
            class="w-full mt-0.5 bg-[#060b16] border border-slate-700 rounded px-2 py-1.5 mono text-[10px] text-white focus:border-amber-500 outline-none resize-none"></textarea>
        </div>
        <!-- Emotion tag -->
        <div>
          <label class="mono text-[8px] text-slate-500 uppercase block mb-1">Emotion During Trade</label>
          <div class="flex gap-1.5 flex-wrap">
            {% for e in ['Calm','Confident','Anxious','FOMO','Greedy','Disciplined','Revenge'] %}
            <label class="cursor-pointer">
              <input type="radio" name="emotion" value="{{ e }}" class="sr-only" {{ 'checked' if e=='Calm' else '' }}>
              <span class="mono text-[8px] px-2 py-0.5 rounded border border-slate-700 text-slate-400 hover:border-amber-600 hover:text-amber-400 emotion-tag">{{ e }}</span>
            </label>
            {% endfor %}
          </div>
        </div>
        <button type="submit"
          class="w-full py-2 rounded bg-amber-600 hover:bg-amber-500 mono text-[10px] font-black text-white tracking-wider">
          🔒 CLOSE TRADE & SAVE
        </button>
      </form>
      {% else %}
      <div class="text-center py-8">
        <p class="mono text-[10px] text-slate-600">No open paper trade.</p>
        <p class="mono text-[9px] text-slate-700 mt-1">Go to ENTRY tab to start one.</p>
      </div>
      {% endif %}
    </div>

    <!-- TAB: JOURNAL -->
    <div id="tab-journal-content" class="p-3 space-y-2 hidden">
      {% if paper.trades %}
        {% for t in paper.trades|reverse %}
        <div class="border border-slate-800 rounded-lg p-2.5 space-y-1.5 {{ 'border-l-2 border-l-emerald-600' if t.pnl > 0 else 'border-l-2 border-l-red-600' }}">
          <div class="flex justify-between items-start">
            <div>
              <span class="mono text-[8px] px-1.5 py-0.5 rounded font-black {{ 'bg-emerald-950 text-emerald-400' if t.direction=='LONG' else 'bg-red-950 text-red-400' }}">{{ t.direction }}</span>
              <span class="mono text-[8px] text-slate-500 ml-1">{{ t.setup }}</span>
            </div>
            <span class="mono text-sm font-black {{ 'text-emerald-400' if t.pnl > 0 else 'text-red-400' }}">
              {{ '+' if t.pnl > 0 else '' }}{{ '%.1f'|format(t.pnl) }} pts
            </span>
          </div>
          <div class="grid grid-cols-3 gap-1 mono text-[8px] text-slate-500">
            <div>In: <span class="text-white">₹{{ t.entry_price }}</span></div>
            <div>Out: <span class="text-white">₹{{ t.exit_price }}</span></div>
            <div>Qty: <span class="text-white">{{ t.qty }}</span></div>
          </div>
          {% if t.exit_reason %}<p class="mono text-[8px] text-amber-500">Exit: {{ t.exit_reason }}</p>{% endif %}
          {% if t.emotion %}<p class="mono text-[8px] text-slate-500">🧠 {{ t.emotion }}</p>{% endif %}
          {% if t.note %}<p class="mono text-[8px] text-slate-400 italic border-t border-slate-800 pt-1 mt-1">Pre: {{ t.note }}</p>{% endif %}
          {% if t.post_note %}<p class="mono text-[8px] text-slate-400 italic">Post: {{ t.post_note }}</p>{% endif %}
        </div>
        {% endfor %}
      {% else %}
      <div class="text-center py-8">
        <p class="mono text-[10px] text-slate-600">No completed trades yet.</p>
        <p class="mono text-[9px] text-slate-700 mt-1">Enter and close your first paper trade.</p>
      </div>
      {% endif %}
    </div>

    <!-- TAB: ANALYSIS -->
    <div id="tab-analysis-content" class="p-3 space-y-3 hidden">
      {% if paper.stats.total > 0 %}
      <!-- Summary stats -->
      <div class="grid grid-cols-2 gap-2">
        {% set stats = paper.stats %}
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Win Rate</p>
          <p class="mono text-xl font-black {{ 'text-emerald-400' if stats.win_rate>=60 else 'text-amber-400' if stats.win_rate>=40 else 'text-red-400' }}">{{ stats.win_rate }}%</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Total P&L</p>
          <p class="mono text-xl font-black {{ 'text-emerald-400' if stats.total_pnl>=0 else 'text-red-400' }}">{{ '+' if stats.total_pnl>=0 else '' }}{{ '%.1f'|format(stats.total_pnl) }}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Avg Winner</p>
          <p class="mono text-lg font-black text-emerald-400">+{{ '%.1f'|format(stats.avg_win) }}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Avg Loser</p>
          <p class="mono text-lg font-black text-red-400">{{ '%.1f'|format(stats.avg_loss) }}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Best Trade</p>
          <p class="mono text-lg font-black text-emerald-400">+{{ '%.1f'|format(stats.best) }}</p>
        </div>
        <div class="bg-[#060b16] border border-slate-800 rounded p-2 text-center">
          <p class="mono text-[8px] text-slate-600 uppercase">Worst Trade</p>
          <p class="mono text-lg font-black text-red-400">{{ '%.1f'|format(stats.worst) }}</p>
        </div>
      </div>

      <!-- Expectancy -->
      <div class="bg-[#060b16] border border-slate-800 rounded p-2.5">
        <p class="mono text-[8px] text-slate-500 uppercase mb-1">System Expectancy (per trade)</p>
        <p class="mono text-base font-black {{ 'text-emerald-400' if stats.expectancy>=0 else 'text-red-400' }}">
          {{ '+' if stats.expectancy>=0 else '' }}{{ '%.2f'|format(stats.expectancy) }} pts
        </p>
        <p class="mono text-[8px] text-slate-600 mt-0.5">
          {% if stats.expectancy > 5 %}Edge is STRONG — keep trading this setup
          {% elif stats.expectancy > 0 %}Edge exists but marginal — need more data
          {% else %}Negative expectancy — DO NOT trade live yet{% endif %}
        </p>
      </div>

      <!-- Setup breakdown -->
      {% if stats.by_setup %}
      <div>
        <p class="mono text-[8px] text-slate-500 uppercase mb-2">Setup Breakdown</p>
        <div class="space-y-1">
          {% for setup, sd in stats.by_setup.items() %}
          <div class="flex justify-between items-center bg-[#060b16] border border-slate-800 rounded px-2 py-1.5">
            <span class="mono text-[9px] text-slate-300">{{ setup }}</span>
            <div class="flex gap-3 mono text-[8px]">
              <span class="text-slate-500">{{ sd.count }}T</span>
              <span class="{{ 'text-emerald-400' if sd.win_rate>=50 else 'text-red-400' }}">{{ sd.win_rate }}%W</span>
              <span class="{{ 'text-emerald-400' if sd.pnl>=0 else 'text-red-400' }}">{{ '+' if sd.pnl>=0 else '' }}{{ '%.1f'|format(sd.pnl) }}pts</span>
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}

      <!-- Emotion breakdown -->
      {% if stats.by_emotion %}
      <div>
        <p class="mono text-[8px] text-slate-500 uppercase mb-2">Emotion vs P&L</p>
        <div class="space-y-1">
          {% for emo, ed in stats.by_emotion.items() %}
          <div class="flex justify-between items-center bg-[#060b16] border border-slate-800 rounded px-2 py-1.5">
            <span class="mono text-[9px] text-slate-300">🧠 {{ emo }}</span>
            <div class="flex gap-3 mono text-[8px]">
              <span class="text-slate-500">{{ ed.count }}T</span>
              <span class="{{ 'text-emerald-400' if ed.pnl>=0 else 'text-red-400' }}">{{ '+' if ed.pnl>=0 else '' }}{{ '%.1f'|format(ed.pnl) }}pts</span>
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
      {% endif %}

      <!-- Clear button -->
      <button onclick="clearTrades()"
        class="w-full py-1.5 rounded border border-red-900 bg-red-950/30 mono text-[9px] text-red-500 hover:bg-red-900/40">
        🗑 Clear All Paper Trades
      </button>

      {% else %}
      <div class="text-center py-8">
        <p class="mono text-[10px] text-slate-600">No data yet.</p>
        <p class="mono text-[9px] text-slate-700 mt-1">Complete at least one paper trade to see analysis.</p>
      </div>
      {% endif %}
    </div>

  </div><!-- end paper section -->

</div><!-- end max-w -->

<script>
// ── Tab switching ──
function showTab(name) {
  ['entry','exit','journal','analysis'].forEach(t => {
    document.getElementById('tab-'+t+'-content').classList.add('hidden');
    document.getElementById('tab-'+t).classList.remove('active');
  });
  document.getElementById('tab-'+name+'-content').classList.remove('hidden');
  document.getElementById('tab-'+name).classList.add('active');
}

// ── Direction selector ──
let selectedDir = 'LONG';
function setDir(el, dir) {
  selectedDir = dir;
  document.getElementById('f-dir').value = dir;
  document.querySelectorAll('.dir-btn').forEach(b => {
    b.className = b.className.replace(/border-emerald-800|bg-emerald-950\/40|text-emerald-400|border-red-800|bg-red-950\/40|text-red-400|border-slate-700|bg-slate-800\/40|text-slate-400/g, '');
  });
  if (dir === 'LONG') {
    el.classList.add('border-emerald-800','bg-emerald-950/40','text-emerald-400');
    document.querySelectorAll('.dir-btn')[1].classList.add('border-slate-700','bg-slate-800/40','text-slate-400');
  } else {
    el.classList.add('border-red-800','bg-red-950/40','text-red-400');
    document.querySelectorAll('.dir-btn')[0].classList.add('border-slate-700','bg-slate-800/40','text-slate-400');
  }
}

// ── Auto-fill from system signal ──
function autoFill() {
  const spot   = {{ m.spot }};
  const target = {{ m.target if m.target > 0 else 'null' }};
  const sl     = {{ m.sl if m.sl > 0 else 'null' }};
  document.getElementById('f-entry').value  = spot;
  if (target) document.getElementById('f-target').value = target;
  if (sl)     document.getElementById('f-sl').value     = sl;
}

// ── Submit entry ──
function submitEntry(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  fetch('/paper/entry', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      direction:   document.getElementById('f-dir').value,
      entry_price: parseFloat(fd.get('entry_price')),
      target:      parseFloat(fd.get('target')) || 0,
      sl:          parseFloat(fd.get('sl')) || 0,
      qty:         parseInt(fd.get('qty')) || 1,
      setup:       fd.get('setup'),
      note:        fd.get('note'),
    })
  }).then(()=>location.reload());
}

// ── Submit exit ──
function submitExit(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  fetch('/paper/exit', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      exit_price:  parseFloat(fd.get('exit_price')),
      exit_reason: fd.get('exit_reason'),
      post_note:   fd.get('post_note'),
      emotion:     fd.get('emotion'),
    })
  }).then(()=>{ showTab('journal'); location.reload(); });
}

// ── Clear trades ──
function clearTrades() {
  if(confirm('Clear all paper trades? This cannot be undone.'))
    fetch('/paper/clear',{method:'POST'}).then(()=>location.reload());
}

// ── Emotion radio visual ──
document.querySelectorAll('input[name="emotion"]').forEach(r => {
  r.addEventListener('change', () => {
    document.querySelectorAll('.emotion-tag').forEach(t => {
      t.classList.remove('border-amber-600','text-amber-400');
      t.classList.add('border-slate-700','text-slate-400');
    });
    r.nextElementSibling.classList.add('border-amber-600','text-amber-400');
    r.nextElementSibling.classList.remove('border-slate-700','text-slate-400');
  });
});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# PAPER TRADE HELPERS
# ─────────────────────────────────────────────
def calc_paper_stats():
    if not PAPER_TRADES:
        return {"total":0,"win_rate":0,"total_pnl":0,"avg_win":0,"avg_loss":0,
                "best":0,"worst":0,"expectancy":0,"by_setup":{},"by_emotion":{}}
    wins   = [t for t in PAPER_TRADES if t["pnl"] > 0]
    losses = [t for t in PAPER_TRADES if t["pnl"] <= 0]
    total  = len(PAPER_TRADES)
    wr     = round(len(wins)/total*100) if total else 0
    tot_pnl = sum(t["pnl"] for t in PAPER_TRADES)
    avg_win  = sum(t["pnl"] for t in wins)/len(wins)  if wins   else 0
    avg_loss = sum(t["pnl"] for t in losses)/len(losses) if losses else 0
    best  = max(t["pnl"] for t in PAPER_TRADES)
    worst = min(t["pnl"] for t in PAPER_TRADES)
    expectancy = (wr/100)*avg_win - ((100-wr)/100)*abs(avg_loss)

    by_setup = {}
    for t in PAPER_TRADES:
        s = t.get("setup","Unknown")
        if s not in by_setup: by_setup[s] = {"count":0,"wins":0,"pnl":0.0}
        by_setup[s]["count"] += 1
        by_setup[s]["pnl"]   += t["pnl"]
        if t["pnl"] > 0: by_setup[s]["wins"] += 1
    for s in by_setup:
        by_setup[s]["win_rate"] = round(by_setup[s]["wins"]/by_setup[s]["count"]*100)

    by_emotion = {}
    for t in PAPER_TRADES:
        e = t.get("emotion","—")
        if not e: continue
        if e not in by_emotion: by_emotion[e] = {"count":0,"pnl":0.0}
        by_emotion[e]["count"] += 1
        by_emotion[e]["pnl"]   += t["pnl"]

    return {"total":total,"win_rate":wr,"total_pnl":tot_pnl,
            "avg_win":avg_win,"avg_loss":avg_loss,"best":best,"worst":worst,
            "expectancy":expectancy,"by_setup":by_setup,"by_emotion":by_emotion}


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def _tg_send(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":chat,"text":msg,"parse_mode":"HTML"},timeout=4)
    except: pass

def telegram_alert(msg):
    threading.Thread(target=_tg_send,args=(msg,),daemon=True).start()


# ─────────────────────────────────────────────
# BROKER SESSION
# ─────────────────────────────────────────────
def get_broker_session():
    global SESSION_CACHE
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None
    api_key     = os.environ.get("ANGEL_API_KEY")
    client_id   = os.environ.get("ANGEL_CLIENT_ID")
    mpin        = os.environ.get("ANGEL_MPIN")
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET")
    if not all([api_key, client_id, mpin, totp_secret]):
        return None, "ENV VARIABLES MISSING"
    try:
        totp    = pyotp.TOTP(totp_secret).now()
        obj     = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        if not session.get("status"): return None, "BROKER LOGIN FAILED"
        SESSION_CACHE["obj"] = obj
        SESSION_CACHE["logged_in_at"] = now
        return obj, None
    except Exception as e:
        return None, f"LOGIN EXCEPTION: {e}"


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def run_pipeline():
    global ENGINE
    now = time.time()
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]
    obj, err = get_broker_session()
    if err: return ENGINE["payload"] or {"error": err}
    try:
        n_res = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
        v_res = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
    except Exception as e:
        SESSION_CACHE["logged_in_at"] = 0
        return ENGINE["payload"] or {"error": f"LTP FETCH FAILED: {e}"}
    if not n_res.get("status") or "data" not in n_res:
        return ENGINE["payload"] or {"error": "NIFTY FEED DEAD"}
    spot = float(n_res["data"]["ltp"])
    try:
        vix = float(v_res["data"]["ltp"]) if v_res.get("status") else 15.0
    except: vix = 15.0

    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    vix_mult = vix / 15.0
    sl_pts   = round(40 * vix_mult, 1)
    tgt_pts  = round(90 * vix_mult, 1)

    base100 = (spot // 100) * 100
    base50  = (spot // 50) * 50
    dist_from_round = abs(spot - base50)
    chk = [
        spot > base100,
        vel > 0,
        dist_from_round > 10,
        vix < 18.0,
        (spot - base100) < (0.008 * spot)
    ]
    all_pass   = all(chk)
    pass_count = sum(chk)

    if ENGINE["status"] == "TRADE_ACTIVE":
        if spot >= ENGINE["target"]:
            pts = round(ENGINE["target"] - ENGINE["entry"], 1)
            ENGINE["trades_won"] += 1; ENGINE["trades_total"] += 1
            ENGINE["session_pnl"] += pts; ENGINE["status"] = "BLOCKED"
            ENGINE["signal"] = f"🎯 TARGET HIT +{pts} pts — Scanning next setup"
            telegram_alert(f"🎯 <b>TARGET HIT</b>\n+{pts} pts at ₹{spot}")
        elif spot <= ENGINE["sl"]:
            pts = round(ENGINE["entry"] - ENGINE["sl"], 1)
            ENGINE["trades_lost"] += 1; ENGINE["trades_total"] += 1
            ENGINE["session_pnl"] -= pts; ENGINE["status"] = "BLOCKED"
            ENGINE["signal"] = f"🛑 SL HIT -{pts} pts — Risk managed"
            telegram_alert(f"🛑 <b>SL HIT</b>\n-{pts} pts at ₹{spot}")
    elif ENGINE["status"] in ("BLOCKED","SETUP_READY"):
        if not all_pass:
            ENGINE["status"] = "BLOCKED"; ENGINE["signal"] = "NO TRADE ZONE — checklist not cleared"
            ENGINE["entry"] = ENGINE["target"] = ENGINE["sl"] = 0.0
        else:
            entry = round(spot - 8, 2)
            ENGINE["status"]  = "SETUP_READY"
            ENGINE["entry"]   = entry
            ENGINE["target"]  = round(entry + tgt_pts, 2)
            ENGINE["sl"]      = round(entry - sl_pts, 2)
            ENGINE["signal"]  = f"SETUP READY — Pullback to ₹{entry}"
            if spot <= (entry + 5) and vel > 0.5:
                ENGINE["status"] = "TRADE_ACTIVE"
                ENGINE["signal"] = f"🔥 LONG EXECUTED at ₹{spot}"
                sig_key = f"LONG_{entry}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key
                    telegram_alert(f"🟢 <b>LONG EXECUTED</b>\n₹{spot} | TGT:₹{ENGINE['target']} SL:₹{ENGINE['sl']}")

    total    = ENGINE["trades_total"]
    wins     = ENGINE["trades_won"]
    win_rate = round(wins/total*100) if total else 0
    payload  = {
        "spot":round(spot,2),"vix":round(vix,2),"velocity":vel,
        "status":ENGINE["status"],"signal":ENGINE["signal"],
        "entry":ENGINE["entry"],"target":ENGINE["target"],"sl":ENGINE["sl"],
        "chk":chk,"pass_count":pass_count,
        "total":total,"wins":wins,"losses":ENGINE["trades_lost"],
        "pnl":round(ENGINE["session_pnl"],1),"win_rate":win_rate,
    }
    ENGINE["last_update"] = now
    ENGINE["payload"]     = payload
    return payload


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return (f"<div style='background:#060b16;color:#f87171;font-family:monospace;"
                f"padding:40px;min-height:100vh;display:flex;flex-direction:column;"
                f"justify-content:center;align-items:center;text-align:center;'>"
                f"<h2>🚨 ENGINE HALTED</h2><p style='color:#94a3b8;margin-top:12px'>{data['error']}</p>"
                f"<p style='color:#334155;font-size:11px;margin-top:20px'>"
                f"Check Render → Environment Variables</p></div>")
    paper_ctx = {
        "open":   PAPER_OPEN,
        "trades": PAPER_TRADES,
        "stats":  calc_paper_stats(),
    }
    return render_template_string(TEMPLATE, m=data, paper=paper_ctx)


@app.route("/paper/entry", methods=["POST"])
def paper_entry():
    global PAPER_OPEN
    if PAPER_OPEN:
        return jsonify({"error": "Close current trade first"}), 400
    d = request.get_json()
    PAPER_OPEN = {
        "direction":   d.get("direction","LONG"),
        "entry_price": d.get("entry_price", 0),
        "target":      d.get("target", 0),
        "sl":          d.get("sl", 0),
        "qty":         d.get("qty", 1),
        "setup":       d.get("setup","Custom"),
        "note":        d.get("note",""),
        "entry_time":  time.strftime("%H:%M:%S"),
    }
    return jsonify({"status":"ok"})


@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    global PAPER_OPEN, PAPER_TRADES
    if not PAPER_OPEN:
        return jsonify({"error": "No open trade"}), 400
    d = request.get_json()
    exit_price = d.get("exit_price", 0)
    if PAPER_OPEN["direction"] == "LONG":
        pnl = round(exit_price - PAPER_OPEN["entry_price"], 2)
    else:
        pnl = round(PAPER_OPEN["entry_price"] - exit_price, 2)
    trade = {**PAPER_OPEN,
             "exit_price":  exit_price,
             "exit_time":   time.strftime("%H:%M:%S"),
             "exit_reason": d.get("exit_reason",""),
             "post_note":   d.get("post_note",""),
             "emotion":     d.get("emotion",""),
             "pnl":         pnl}
    PAPER_TRADES.append(trade)
    PAPER_OPEN = None
    return jsonify({"status":"ok","pnl":pnl})


@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    global PAPER_OPEN, PAPER_TRADES
    PAPER_OPEN   = None
    PAPER_TRADES = []
    return jsonify({"status":"ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
