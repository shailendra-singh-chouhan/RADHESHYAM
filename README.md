# GOAT PRO — Complete Project Backup (Jul 8, 2026)

This ZIP contains the **complete, fully-fixed codebase** of your GOAT PRO Institutional Trading Dashboard.

---

## 📦 All 13 files

| File | Size | Purpose |
|------|------|---------|
| `main.py` | 1.9 KB | FastAPI app entry point — lifespan startup, routes mounting |
| `config.py` | 9.1 KB | Configuration, env vars, symbols, AUTO_TRADE_ENABLED toggle |
| `angel_client.py` | 11.4 KB | Angel One API client — login, LTP, candles, options, USDINR fallback |
| `strategy.py` | 22.7 KB | All background pollers + REAL data fetchers (NSE/yfinance) + signal engine |
| `routes.py` | 8.7 KB | All API endpoints — `/api/data`, `/api/trades`, `/api/execute_trade`, etc. |
| `stocks.py` | 3.2 KB | Stock price poller (HDFC, SBI, PNB, YES, INFY) |
| `database.py` | 5.6 KB | SQLAlchemy engine + session + migrations |
| `models.py` | 2.2 KB | Trade SQLAlchemy model |
| `trading.py` | 14.1 KB | Paper trade logic — open/close, risk, auto-signal processing |
| `indicators.py` | 2.0 KB | RSI / EMA / VWAP / MACD / Supertrend calculation helpers |
| `dashboard.html` | 50.5 KB | Full 47 KB HTML/CSS/JS dashboard UI |
| `requirements.txt` | 0.3 KB | Python dependencies for Render |
| `render.yaml` | 0.7 KB | Render Blueprint config (optional) |

---

## ✅ All fixes applied (verified on Jul 8, 2026)

| # | Fix | Status |
|---|-----|--------|
| 1 | FastAPI + PostgreSQL + full dashboard code deployed | ✅ |
| 2 | HEAD 405 warning on `/` `/health` `/ping` | ✅ |
| 3 | SHORT trade PnL bug (direction-aware calculation) | ✅ |
| 4 | Full 47 KB dashboard HTML restored | ✅ |
| 5 | Angel One rate-limit handling (retry + backoff) | ✅ |
| 6 | Price poller batching (rate-limit safe) | ✅ |
| 7 | USDINR token fallback (prefix match for CDS futures) | ✅ |
| 8 | Greeks calculation (delta + vega added) | ✅ |
| 9 | AUTO_TRADE_ENABLED env-var toggle | ✅ |
| 10 | Global indices → REAL Yahoo Finance (yfinance) | ✅ |
| 11 | OI data → REAL NSE India option-chain API | ✅ |
| 12 | FII/DII activity → REAL NSE India FII/DII API | ✅ |
| 13 | Source labels in API response (transparency) | ✅ |
| 14 | Smart alerts (only fire near real max_pain) | ✅ |

---

## 🎯 What's REAL vs APPROXIMATION vs UNAVAILABLE

| Data | Source | Status |
|------|--------|--------|
| NIFTY / BankNifty / FinNifty / Sensex / Crude / Gold / Silver / Midcap / VIX | Angel One LTP | ✅ Real |
| Stocks (HDFC / SBI / PNB / YES / INFY) | Angel One LTP | ✅ Real |
| Indicators (RSI / EMA9 / EMA21 / VWAP / MACD / Supertrend) | Calculated from real candles | ✅ Real |
| Real Signal (ORB strategy with 6-point checklist) | Based on real indicators | ✅ Real |
| Active trade (paper) + Live PnL | Real entry/exit prices | ✅ Real |
| **Global Indices (Kospi / NASDAQ / Dow)** | Yahoo Finance | ✅ Real |
| **OI (Call/Put OI, PCR, Max Pain)** | NSE India option-chain API | ✅ Real |
| **FII/DII Activity** | NSE India FII/DII API | ✅ Real |
| Greeks (IV / delta / theta / gamma / vega) | Black-Scholes approximation | ⚠️ Labeled `BS_APPROX` |
| USDINR | Needs CDS segment access on Angel One | ❌ Shows 0 |

---

## 🚀 Deploy steps (if starting fresh)

1. **Extract ZIP** to a folder
2. **Create GitHub repo** named `RADHESHYAM` (or use existing)
3. **Upload all 13 files** to the repo root
4. **On Render**:
   - Create new Web Service → connect GitHub repo
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/health`
5. **Environment Variables** on Render:
   - `ANGEL_API_KEY` — your Angel One API key
   - `ANGEL_CLIENT_ID` — your Angel One client ID
   - `ANGEL_MPIN` — your Angel One MPIN
   - `ANGEL_TOTP_SECRET` — your Angel One TOTP secret
   - `DATABASE_URL` — auto-injected if you link your PostgreSQL service
   - `AUTO_TRADE_ENABLED` — set to `true` only when ready (default: false)
6. **Create PostgreSQL database** on Render (named `GOAT_PRO`)
7. **Deploy** — wait 2-3 minutes
8. **Open** `https://your-service.onrender.com/` — dashboard should appear

---

## 📋 Environment Variables Reference

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANGEL_API_KEY` | ✅ | Angel One SmartAPI key |
| `ANGEL_CLIENT_ID` | ✅ | Angel One client ID |
| `ANGEL_MPIN` | ✅ | Angel One MPIN |
| `ANGEL_TOTP_SECRET` | ✅ | Angel One TOTP secret |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (auto from Render) |
| `AUTO_TRADE_ENABLED` | ⚠️ Optional | `true`/`false` (default: false) |
| `GAP_THRESHOLD_PERCENT` | ⚠️ Optional | Gap protector threshold (default: 0.5) |
| `PORT` | auto | Set by Render |

---

## 🎉 Project Status: COMPLETE

All bugs fixed, all fake data replaced with real sources, dashboard fully operational.
