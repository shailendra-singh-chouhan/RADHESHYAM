import os
import random
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# 🎨 PREMIUM BLUE & WHITE THEME UI (Top-Line Indicators + Hindi Layout)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO Command Center</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        async function refreshData() {
            const btn = document.getElementById('refresh-btn');
            btn.innerText = '🔄 डेटा सिंक हो रहा है...';
            try {
                const response = await fetch('/api/refresh');
                const data = await response.json();
                
                // Live Bhav Blocks
                document.getElementById('spot-price').innerText = '₹' + data.spot;
                document.getElementById('day-high').innerText = '₹' + data.day_high;
                document.getElementById('day-low').innerText = '₹' + data.day_low;
                document.getElementById('vwap-val').innerText = '₹' + data.vwap;
                document.getElementById('jadui-val').innerText = '₹' + data.jadui_spot;
                
                // Indicators & Strategy 
                document.getElementById('pcr-val').innerText = data.pcr;
                document.getElementById('rsi-val').innerText = data.rsi + ' (' + data.rsi_status + ')';
                document.getElementById('trend-tag').innerText = data.trend;
                document.getElementById('scalp-action').innerText = data.scalp_action;
                document.getElementById('intraday-prompt').innerText = data.intraday_prompt;
                document.getElementById('directional-long').innerText = data.directional_long;
                
                // PCR Box Color Filter
                const pcrVal = document.getElementById('pcr-val');
                if(data.pcr >= 0.75) {
                    pcrVal.className = "text-3xl md:text-4xl font-black font-mono text-emerald-600 tracking-tight mt-1";
                } else {
                    pcrVal.className = "text-3xl md:text-4xl font-black font-mono text-rose-600 tracking-tight mt-1";
                }
                
                // Laxman Rekha Box Color Filter
                const jaduiContainer = document.getElementById('jadui-container');
                const jaduiVal = document.getElementById('jadui-val');
                if(data.spot < data.jadui_spot) {
                    jaduiContainer.className = "bg-rose-500 border border-rose-600 text-white p-5 rounded-xl animate-pulse flex flex-col justify-between shadow-md h-full transition-all duration-300";
                    jaduiVal.className = "font-mono font-black text-3xl md:text-4xl text-white mt-2";
                } else {
                    jaduiContainer.className = "bg-emerald-50 border border-emerald-400 text-slate-800 p-5 rounded-xl flex flex-col justify-between shadow-sm h-full transition-all duration-300";
                    jaduiVal.className = "font-mono font-black text-3xl md:text-4xl text-emerald-600 mt-2";
                }
                
            } catch (err) {
                console.error('Data pipeline error:', err);
            }
            btn.innerText = '🔄 डेटा रिफ्रेश करें';
        }
        setInterval(refreshData, 15000);
    </script>
</head>
<body class="bg-slate-50 text-slate-800 font-sans min-h-screen antialiased">

    <header class="border-b border-blue-100 bg-white sticky top-0 z-50 px-6 py-4 flex flex-wrap justify-between items-center gap-4 shadow-sm">
        <div class="flex items-center gap-3">
            <div class="h-3 w-3 rounded-full bg-blue-600 animate-pulse"></div>
            <h1 class="text-xl md:text-2xl font-black tracking-wider text-slate-900 font-mono">⚡ GOAT PRO <span class="text-xs bg-blue-50 text-blue-600 px-2.5 py-1 rounded border border-blue-200 ml-2 font-bold">हिंदी डेटा कोर</span></h1>
        </div>
        <button id="refresh-btn" onclick="refreshData()" class="bg-blue-600 hover:bg-blue-700 text-white active:scale-95 px-6 py-2.5 rounded-xl font-mono text-sm font-bold tracking-wider transition-all duration-150 shadow-md">
            🔄 डेटा रिफ्रेश करें
        </button>
    </header>

    <main class="max-w-7xl mx-auto p-4 md:p-6 space-y-6">
        
        <div class="bg-blue-50 border border-blue-200 rounded-xl p-5 shadow-sm flex items-start gap-4">
            <div class="text-2xl">📢</div>
            <div>
                <h3 class="font-bold text-blue-900 text-sm tracking-widest uppercase font-mono">लाइव मार्केट चेतावनी</h3>
                <p id="intraday-prompt" class="text-slate-800 mt-1 text-sm md:text-base font-bold tracking-wide leading-relaxed">{{ m.intraday_prompt }}</p>
            </div>
        </div>

        <section class="space-y-3">
            <h2 class="text-base md:text-lg font-black text-blue-900 uppercase tracking-wider border-l-4 border-indigo-600 pl-2 font-mono">🧠 लाइव इंडिकेटर्स (सीधी लंबी लाइन)</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-5 items-stretch">
                <div class="bg-white border border-slate-200 rounded-xl p-5 flex justify-between items-center shadow-sm min-h-[100px]">
                    <div class="flex flex-col">
                        <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">📊 असली PCR (मार्केट का मूड)</span>
                        <span id="pcr-val" class="text-3xl md:text-4xl font-black tracking-tight font-mono mt-1 {{ 'text-emerald-600' if m.pcr >= 0.75 else 'text-rose-600' }}">{{ m.pcr }}</span>
                    </div>
                    <span id="trend-tag" class="text-xs font-black uppercase tracking-wider font-mono bg-slate-100 px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600">{{ m.trend }}</span>
                </div>
                
                <div class="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-center shadow-sm min-h-[100px]">
                    <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">🚀 RSI मोमेंटम (स्पीडोमीटर)</span>
                    <span id="rsi-val" class="text-xl md:text-2xl font-black text-slate-800 font-mono mt-1"><span class="mr-1">{{ m.rsi_color }}</span> {{ m.rsi }} <span class="text-xs md:text-sm text-slate-500 font-bold">({{ m.rsi_status }})</span></span>
                </div>
            </div>
        </section>

        <section class="space-y-3 pt-1">
            <h2 class="text-base md:text-lg font-black text-blue-900 uppercase tracking-wider border-l-4 border-blue-600 pl-2 font-mono">📊 निफ्टी लाइव भाव (मेन सेगमेंट)</h2>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-5 items-stretch">
                <div class="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between shadow-sm min-h-[150px]">
                    <span class="text-xs
