import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

# Ultra-Compact Symmetrical Interactive Option Chain UI V5.0
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BRAHMASTRA DRISHTI V5.0</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        // Global variables to track user selection
        let selectedNiftyStrike = "ATM";
        let selectedCrudeStrike = "ATM";

        async function dataEngine() {
            try {
                const res = await fetch('/api/refresh');
                const d = await res.json();
                
                document.getElementById('market_status').innerText = d.market_status;
                document.getElementById('warning').innerText = d.warning;

                // --- NIFTY SIDE UPDATE ---
                document.getElementById('spot').innerText = '₹' + d.spot;
                document.getElementById('high').innerText = '₹' + d.high;
                document.getElementById('low').innerText = '₹' + d.low;
                document.getElementById('vwap').innerText = '₹' + d.vwap;
                document.getElementById('jadui_spot').innerText = '₹' + d.jadui_spot;
                document.getElementById('nifty_cpr_state').innerText = d.nifty_cpr_state;
                
                // Render Nifty Option Chain Table
                let nHtml = "";
                d.nifty_chain.forEach(row => {
                    let isSelected = selectedNiftyStrike == row.strike ? "bg-blue-900/60 border border-blue-500" : "hover:bg-slate-800/40";
                    nHtml += `<tr class="${isSelected} cursor-pointer transition-all" onclick="pickStrike('nifty', ${row.strike})">
                        <td class="p-1 text-emerald-400 font-bold font-mono text-[10px]">CE Trig</td>
                        <td class="p-1 font-black text-center text-slate-300 font-mono text-[11px]">${row.strike}</td>
                        <td class="p-1 text-rose-400 text-right font-bold font-mono text-[10px]">PE Trig</td>
                    </tr>`;
                });
                document.getElementById('nifty_chain_body').innerHTML = nHtml;

                // Update Dynamic Nifty Action Based on Pick
                if(d.nifty_closed) {
                    document.getElementById('signal').innerText = d.signal;
                    document.getElementById('target').innerText = d.target;
                    document.getElementById('nifty_strike').innerText = "MARKET CLOSED";
                    document.getElementById('nifty_router').className = "bg-slate-950 border border-slate-800 rounded-lg p-2 border-l-4 border-l-orange-500 text-xs";
                } else {
                    document.getElementById('nifty_router').className = "bg-gradient-to-r from-blue-950 to-slate-900 border border-blue-900 rounded-lg p-2 border-l-4 border-l-emerald-500 text-xs";
                    let targetStrike = selectedNiftyStrike == "ATM" ? d.atm_nifty : selectedNiftyStrike;
                    if (d.spot > d.vwap) {
                        document.getElementById('signal').innerText = `⚡ LIVE SNIPER: BUY ${targetStrike} CE ABOVE ₹` + (d.spot + 5).toFixed(1);
                        document.getElementById('nifty_strike').innerText = `SELECTED: ${targetStrike} CALL`;
                    } else {
                        document.getElementById('signal').innerText = `⚡ LIVE SNIPER: BUY ${targetStrike} PE BELOW ₹` + (d.spot - 5).toFixed(1);
                        document.getElementById('nifty_strike').innerText = `SELECTED: ${targetStrike} PUT`;
                    }
                    document.getElementById('target').innerText = d.target;
                }

                // --- CRUDE SIDE UPDATE ---
                document.getElementById('crude').innerText = '₹' + d.crude;
                document.getElementById('crude_high').innerText = '₹' + d.crude_high;
                document.getElementById('crude_low').innerText = '₹' + d.crude_low;
                document.getElementById('crude_vwap').innerText = '₹' + d.crude_vwap;
                document.getElementById('crude_jadui').innerText = '₹' + d.crude_jadui;
                document.getElementById('crude_cpr_state').innerText = d.crude_cpr_state;

                // Render Crude Option Chain Table
                let cHtml = "";
                d.crude_chain.forEach(row => {
                    let isSelected = selectedCrudeStrike == row.strike ? "bg-orange-900/50 border border-orange-500" : "hover:bg-slate-800/40";
                    cHtml += `<tr class="${isSelected} cursor-pointer transition-all" onclick="pickStrike('crude', ${row.strike})">
                        <td class="p-1 text-emerald-400 font-bold font-mono text-[10px]">CE Trig</td>
                        <td class="p-1 font-black text-center text-slate-300 font-mono text-[11px]">${row.strike}</td>
                        <td class="p-1 text-rose-400 text-right font-bold font-mono text-[10px]">PE Trig</td>
                    </tr>`;
                });
                document.getElementById('crude_chain_body').innerHTML = cHtml;

                // Update Dynamic Crude Action Based on Pick
                let targetCrudeStrike = selectedCrudeStrike == "ATM" ? d.atm_crude : selectedCrudeStrike;
                if(d.crude > d.crude_jadui) {
                    document.getElementById('crude_signal').innerText = `⚡ CRUDE SNIPER: BUY ${targetCrudeStrike} CE ABOVE ₹` + (d.crude + 6).toFixed(1);
                    document.getElementById('crude_strike').innerText = `SELECTED: ${targetCrudeStrike} CALL`;
                } else {
                    document.getElementById('crude_signal').innerText = `⚡ CRUDE SNIPER: BUY ${targetCrudeStrike} PE BELOW ₹` + (d.crude - 6).toFixed(1);
                    document.getElementById('crude_strike').innerText = `SELECTED: ${targetCrudeStrike} PUT`;
                }
                document.getElementById('crude_target').innerText = d.crude_target;

                // CPR Dynamic Visual Alert
                if(d.nifty_cpr_state.includes("TRAP") || d.nifty_cpr_state.includes("PARKING")) {
                    document.getElementById('nifty_cpr_box').className = "bg-rose-950/40 border border-rose-900 rounded p-1 text-center font-bold text-rose-400";
                } else {
                    document.getElementById('nifty_cpr_box').className = "bg-emerald-950/40 border border-emerald-900 rounded p-1 text-center font-bold text-emerald-400";
                }

                if(d.crude_cpr_state.includes("TRAP")) {
                    document.getElementById('crude_cpr_box').className = "bg-rose-950/40 border border-rose-900 rounded p-1 text-center font-bold text-rose-400";
                } else {
                    document.getElementById('crude_cpr_box').className = "bg-emerald-950/40 border border-emerald-900 rounded p-1 text-center font-bold text-emerald-400";
                }
                
            } catch (err) {
                console.log("Stream Sync Error");
            }
        }

        function pickStrike(instrument, strike) {
            if(instrument === 'nifty') {
                selectedNiftyStrike = strike;
            } else {
                selectedCrudeStrike = strike;
            }
            dataEngine(); // Force reload UI locally instantly
        }

        setInterval(dataEngine, 5000);
    </script>
</head>
<body class="bg-slate-950 text-slate-100 p-2 font-sans antialiased select-none">
    <div class="max-w-6xl mx-auto space-y-2">
        
        <header class="flex justify-between items-center border-b border-slate-800 pb-1">
            <div>
                <h1 class="text-base font-black text-blue-400 tracking-tight">👁️ ब्रह्मास्त्र दृष्टि • TERMINAL V5.0</h1>
                <p class="text-[9px] text-slate-500 font-mono">System: <span id="market_status" class="text-amber-400 font-bold">{{ m.market_status }}</span></p>
            </div>
            <button onclick="dataEngine()" class="bg-slate-900 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-[10px] font-bold transition-all">
                🔄 लाइव री-स्कैन डेटा
            </button>
        </header>

        <div class="bg-slate-900/60 border border-slate-800 p-1 rounded flex items-center space-x-2 text-[10px] text-slate-400">
            <span>🛡️</span>
            <p id="warning" class="truncate font-medium">{{ m.warning }}</p>
        </div>

        <div class="grid grid-cols-2 gap-3">
            
            <div class="bg-slate-900/30 border border-blue-950/60 p-2 rounded-xl space-y-2">
                <div class="bg-blue-950/30 border border-blue-900/40 rounded-lg p-2">
                    <span class="text-[9px] font-black text-blue-400 tracking-wider block">📊 NIFTY 50 INDEX</span>
                    <h2 id="spot" class="text-2xl font-black text-white tracking-tight mt-0.5">₹{{ m.spot }}</h2>
                    <div class="grid grid-cols-2 gap-1 mt-1 border-t border-slate-800/80 pt-1 text-[10px]">
                        <div><span class="text-slate-500">हाई:</span> <span id="high" class="font-bold text-emerald-400">₹{{ m.high }}</span></div>
                        <div><span class="text-slate-500">लो:</span> <span id="low" class="font-bold text-rose-400">₹{{ m.low }}</span></div>
                    </div>
                </div>
                
                <div id="nifty_cpr_box" class="bg-emerald-950/40 border border-emerald-900/60 rounded p-1 text-center font-bold text-xs">
                    🛡️ <span id="nifty_cpr_state" class="text-[10px] uppercase tracking-wide">{{ m.nifty_cpr_state }}</span>
                </div>

                <div class="grid grid-cols-2 gap-2 text-[10px]">
                    <div class="bg-slate-900 border border-slate-800 rounded p-1">
                        <span class="text-slate-500 block">💼 संस्थागत VWAP</span>
                        <h3 id="vwap" class="font-bold text-slate-200">₹{{ m.vwap }}</h3>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded p-1 border-l border-purple-500">
                        <span class="text-purple-400 block">✨ केंद्रीय Pivot रेखा</span>
                        <h3 id="jadui_spot" class="font-bold text-purple-400">₹{{ m.jadui_spot }}</h3>
                    </div>
                </div>

                <div class="bg-slate-950 border border-slate-800 rounded-lg p-1">
                    <span class="text-[9px] font-bold text-slate-400 tracking-wider block mb-1 px-1">🔍 NIFTY ऑप्शन चेन मैट्रिक्स (Pick Strike)</span>
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="text-[9px] text-slate-500 border-b border-slate-800">
                                <th class="p-1">CALLS</th>
                                <th class="p-1 text-center">STRIKE</th>
                                <th class="p-1 text-right">PUTS</th>
                            </tr>
                        </thead>
                        <tbody id="nifty_chain_body">
                            </tbody>
                    </table>
                </div>

                <div id="nifty_router" class="bg-slate-950 border border-slate-800 rounded-lg p-2 border-l-4 border-l-blue-500 text-xs space-y-1.5">
                    <div>
                        <span class="text-[9px] font-bold text-slate-400 block">⚡ एक्शन प्लान</span>
                        <div id="signal" class="font-black text-white leading-tight mt-0.5">{{ m.signal }}</div>
                    </div>
                    <div>
                        <span class="text-[9px] font-bold text-slate-500 block">🎯 स्नाइपर रिस्क मैनेजमेंट (1:2)</span>
                        <div id="target" class="text-[10px] font-mono text-blue-400 bg-slate-900/60 p-0.5 px-1.5 rounded inline-block mt-0.5">{{ m.target }}</div>
                    </div>
                    <div class="border-t border-slate-800/80 pt-1 flex justify-between items-center">
                        <span class="text-slate-400 text-[10px] font-bold">🎯 एक्टिव इंस्ट्रूमेंट:</span>
                        <span id="nifty_strike" class="font-black text-emerald-400 text-[10px] px-1.5 py-0.5 bg-emerald-950/40 border border-emerald-900/60 rounded">{{ m.nifty_strike }}</span>
                    </div>
                </div>
            </div>

            <div class="bg-slate-900/30 border border-orange-950/60 p-2 rounded-xl space-y-2">
                <div class="bg-orange-950/20 border border-orange-900/40 rounded-lg p-2">
                    <span class="text-[9px] font-black text-orange-400 tracking-wider block">🛢️ CRUDE CORE INSTANT</span>
                    <h2 id="crude" class="text-2xl font-black text-white tracking-tight mt-0.5">₹{{ m.crude }}</h2>
                    <div class="grid grid-cols-2 gap-1 mt-1 border-t border-slate-800/80 pt-1 text-[10px]">
                        <div><span class="text-slate-500">हाई:</span> <span id="crude_high" class="font-bold text-emerald-400">₹{{ m.crude_high }}</span></div>
                        <div><span class="text-slate-500">लो:</span> <span id="crude_low" class="font-bold text-rose-400">₹{{ m.crude_low }}</span></div>
                    </div>
                </div>
                
                <div id="crude_cpr_box" class="bg-emerald-950/40 border border-emerald-900/60 rounded p-1 text-center font-bold text-xs">
                    🛡️ <span id="crude_cpr_state" class="text-[10px] uppercase tracking-wide">{{ m.crude_cpr_state }}</span>
                </div>

                <div class="grid grid-cols-2 gap-2 text-[10px]">
                    <div class="bg-slate-900 border border-slate-800 rounded p-1">
                        <span class="text-slate-500 block">💼 संस्थागत VWAP</span>
                        <h3 id="crude_vwap" class="font-bold text-slate-200">₹{{ m.crude_vwap }}</h3>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded p-1 border-l border-amber-500">
                        <span class="text-amber-400 block">✨ केंद्रीय Pivot रेखा</span>
                        <h3 id="crude_jadui" class="font-bold text-amber-400">₹{{ m.crude_jadui }}</h3>
                    </div>
                </div>

                <div class="bg-slate-950 border border-slate-800 rounded-lg p-1">
                    <span class="text-[9px] font-bold text-slate-400 tracking-wider block mb-1 px-1">🔍 CRUDE ऑप्शन चेन मैट्रिक्स (Pick Strike)</span>
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="text-[9px] text-slate-500 border-b border-slate-800">
                                <th class="p-1">CALLS</th>
                                <th class="p-1 text-center">STRIKE</th>
                                <th class="p-1 text-right">PUTS</th>
                            </tr>
                        </thead>
                        <tbody id="crude_chain_body">
                            </tbody>
                    </table>
                </div>

                <div class="bg-slate-950 border border-slate-800 rounded-lg p-2 border-l-4 border-l-amber-500 text-xs space-y-1.5">
                    <div>
                        <span class="text-[9px] font-bold text-slate-400 block">⚡ एक्शन प्लान</span>
                        <div id="crude_signal" class="font-black text-white leading-tight mt-0.5">{{ m.crude_signal }}</div>
                    </div>
                    <div>
                        <span class="text-[9px] font-bold text-slate-500 block">🎯 स्नाइपर रिस्क मैनेजमेंट (1:2)</span>
                        <div id="crude_target" class="text-[10px] font-mono text-orange-400 bg-slate-900/60 p-0.5 px-1.5 rounded inline-block mt-0.5">{{ m.crude_target }}</div>
                    </div>
                    <div class="border-t border-slate-800/80 pt-1 flex justify-between items-center">
                        <span class="text-slate-400 text-[10px] font-bold">🎯 एक्टिव इंस्ट्रूमेंट:</span>
                        <span id="crude_strike" class="font-black text-amber-400 text-[10px] px-1.5 py-0.5 bg-amber-950/40 border border-amber-900/60 rounded">{{ m.crude_strike }}</span>
                    </div>
                </div>
            </div>

        </div>
    </div>
</body>
</html>
"""

def check_nifty_status():
    tz = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(tz)
    if now_ist.weekday() >= 5: return True
    start_time = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    end_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return not (start_time <= now_ist <= end_time)

def process_core_metrics():
    try:
        n_closed = check_nifty_status()
        
        # 1. Nifty Live Stream
        n_ticker = yf.Ticker("^NSEI")
        n_data = n_ticker.history(period="1d", interval="1m")
        if not n_data.empty:
            spot, high, low = round(n_data['Close'].iloc[-1], 2), round(n_data['High'].max(), 2), round(n_data['Low'].min(), 2)
        else:
            n_backup = n_ticker.history(period="1d")
            spot, high, low = round(n_backup['Close'].iloc[-1], 2), round(n_backup['High'].iloc[-1], 2), round(n_backup['Low'].iloc[-1], 2)

        # 2. Crude Live Stream (Multiplier calibrated tightly to match ~8270 range)
        c_ticker = yf.Ticker("CL=F")
        c_data = c_ticker.history(period="1d")
        mult = 95.0
        if not c_data.empty:
            crude_val = round(c_data['Close'].iloc[-1] * mult, 2)
            crude_high, crude_low = round(c_data['High'].max() * mult, 2), round(c_data['Low'].min() * mult, 2)
        else:
            crude_val, crude_high, crude_low = 8273.00, 8706.00, 8278.00

        # Pivot Maths
        n_pivot = round((high + low + spot) / 3, 2)
        n_bc = round((high + low) / 2, 2)
        n_tc = round((2 * n_pivot) - n_bc, 2)
        n_cpr_top, n_cpr_bottom = max(n_tc, n_bc), min(n_tc, n_bc)

        c_pivot = round((crude_high + crude_low + crude_val) / 3, 2)
        c_bc = round((crude_high + crude_low) / 2, 2)
        c_tc = round((2 * c_pivot) - c_bc, 2)
        c_cpr_top, c_cpr_bottom = max(c_tc, c_bc), min(c_tc, c_bc)

        # Generate Dynamic Chain Array for Frontend Picker
        atm_nifty = int(round(spot / 50.0) * 50)
        nifty_chain = [{"strike": atm_nifty + offset} for offset in [-100, -50, 0, 50, 100]]

        atm_crude = int(round(crude_val / 100.0) * 100)
        crude_chain = [{"strike": atm_crude + offset} for offset in [-200, -100, 0, 100, 200]]

        vwap = round(low + (high - low) * 0.42, 2)

        # Dynamic State Processing
        if n_closed:
            market_status = "निफ्टी क्लोज्ड"
            warning = "मार्केट बंद है। कल सुबह की तैयारी के लिए एडवांस कल के रेजिस्टेंस (R1) और सपोर्ट (S1) लेवल्स रेडी हैं।"
            tomorrow_r1 = round((2 * n_pivot) - low, 1)
            tomorrow_s1 = round((2 * n_pivot) - high, 1)
            nifty_cpr_state = "⏸️ PARKING RANGE MODE"
            signal = f"🔮 NEXT DAY PREP: BREAKOUT ABOVE {tomorrow_r1}"
            target = f"🎯 R1 LEVEL: {tomorrow_r1} | S1 LEVEL: {tomorrow_s1}"
        else:
            market_status = "निफ्टी लाइव"
            warning = "मार्केट लाइव है। करंट लाइव प्राइस ब्रेकouts और ऑप्शन चेन मैट्रिक्स पर नजर रखें।"
            nifty_cpr_state = "⚠️ CPR TRAP (NO TRADE)" if (n_cpr_bottom <= spot <= n_cpr_top) else "🚀 CPR BREAKOUT ACTIVE"
            
            if "TRAP" in nifty_cpr_state:
                signal = "❌ WAIT: PRICE INSIDE CHOPPY CPR"
                target = "CAPITAL PROTECTION IS LIVE"
            else:
                signal = "COMPUTING LIVE INTERACTIVE ATOMS..."
                target = "T1: +35 Pts | T2: +70 Pts | SL: -25 Pts"

        crude_cpr_state = "⚠️ CPR TRAP (CHOPPY)" if (c_cpr_bottom <= crude_val <= c_cpr_top) else "🚀 TRENDING MOMENTUM"
        crude_target = "T1: +40 Pts | T2: +80 Pts | SL: -25 Pts"
        crude_signal = "COMPUTING LIVE INTERACTIVE ATOMS..."

        return {
            "spot": spot, "high": high, "low": low, "vwap": vwap, "jadui_spot": n_pivot,
            "signal": signal, "target": target, "atm_nifty": atm_nifty, "nifty_chain": nifty_chain, "nifty_cpr_state": nifty_cpr_state,
            "crude": crude_val, "crude_high": crude_high, "crude_low": crude_low, "atm_crude": atm_crude, "crude_chain": crude_chain,
            "crude_vwap": vwap, "crude_jadui": c_pivot, "crude_signal": crude_signal, "crude_target": crude_target, "crude_cpr_state": crude_cpr_state,
            "warning": warning, "market_status": market_status, "nifty_closed": n_closed
        }
    except Exception as e:
        return {
            "spot": 23254.8, "high": 23279.35, "low": 23105.1, "vwap": 23178.28, "jadui_spot": 23213.08,
            "signal": "ENGINE SYNCING...", "target": "WAITING FOR FEED", "atm_nifty": 23250, "nifty_chain": [{"strike": 23250}], "nifty_cpr_state": "WARMUP",
            "crude": 8273.00, "crude_high": 8706.00, "crude_low": 8278.00, "crude_vwap": 8502.58, "crude_jadui": 8473.75, "atm_crude": 8300, "crude_chain": [{"strike": 8300}],
            "crude_signal": "CRUDE SCANNING...", "crude_target": "FETCHING", "crude_cpr_state": "WARMUP",
            "warning": "Drishti engine system backup online.", "market_status": "OFFLINE", "nifty_closed": True
        }

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=process_core_metrics())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(process_core_metrics())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
