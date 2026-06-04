const REFRESH_INTERVAL_MS = 5000;

const elements = {
  connectionStatus: document.getElementById("connectionStatus"),
  errorPanel: document.getElementById("errorPanel"),
  errorMessage: document.getElementById("errorMessage"),
  lastUpdate: document.getElementById("lastUpdate"),
  niftyPrice: document.getElementById("niftyPrice"),
  niftyChange: document.getElementById("niftyChange"),
  bankniftyPrice: document.getElementById("bankniftyPrice"),
  bankniftyChange: document.getElementById("bankniftyChange"),
  pcrValue: document.getElementById("pcrValue"),
  vixValue: document.getElementById("vixValue"),
  signalDirection: document.getElementById("signalDirection"),
  signalScore: document.getElementById("signalScore"),
  crudeValue: document.getElementById("crudeValue"),
  goldValue: document.getElementById("goldValue"),
  silverValue: document.getElementById("silverValue"),
  usdinrValue: document.getElementById("usdinrValue")
};

function formatNumber(value, decimals = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";

  return number.toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function formatChange(change, pchg) {
  const changeNumber = Number(change);
  const pchgNumber = Number(pchg);

  if (!Number.isFinite(changeNumber) || !Number.isFinite(pchgNumber)) {
    return "Change unavailable";
  }

  const sign = changeNumber > 0 ? "+" : "";
  return `${sign}${formatNumber(changeNumber)} (${sign}${formatNumber(pchgNumber)}%)`;
}

function trendClass(value) {
  const number = Number(value);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "neutral";
}

function setText(element, value) {
  element.textContent = value;
  element.classList.remove("loading");
}

function setTrend(element, value) {
  element.classList.remove("positive", "negative", "neutral");
  element.classList.add(trendClass(value));
}

function setConnectionState(state, label) {
  elements.connectionStatus.classList.remove("is-online", "is-offline");
  elements.connectionStatus.classList.add(state);
  elements.connectionStatus.querySelector("span:last-child").textContent = label;
}

function renderMarket(data) {
  const nifty = data.nifty || {};
  const banknifty = data.banknifty || {};
  const mcx = data.mcx || {};
  const signal = data.signal || {};

  setText(elements.niftyPrice, formatNumber(nifty.price));
  setText(elements.niftyChange, formatChange(nifty.change, nifty.pchg));
  setTrend(elements.niftyChange, nifty.change);

  setText(elements.bankniftyPrice, formatNumber(banknifty.price));
  setText(elements.bankniftyChange, formatChange(banknifty.change, banknifty.pchg));
  setTrend(elements.bankniftyChange, banknifty.change);

  setText(elements.pcrValue, formatNumber(data.pcr));
  setText(elements.vixValue, formatNumber(data.vix));
  setText(elements.signalDirection, signal.direction || "WAIT — No trade zone");
  setText(elements.signalScore, Number.isFinite(Number(signal.score)) ? String(signal.score) : "--");

  setText(elements.crudeValue, formatNumber(mcx.crude_inr));
  setText(elements.goldValue, formatNumber(mcx.gold_inr));
  setText(elements.silverValue, formatNumber(mcx.silver_inr));
  setText(elements.usdinrValue, formatNumber(mcx.usd_inr));
  setText(elements.lastUpdate, data.timestamp || new Date().toLocaleTimeString("en-IN", { hour12: false }));

  elements.errorPanel.classList.add("is-hidden");
  setConnectionState("is-online", "Live");
}

async function fetchMarketData() {
  try {
    const response = await fetch("/api/market", {
      method: "GET",
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }

    const data = await response.json();
    renderMarket(data);
  } catch (error) {
    elements.errorMessage.textContent = error.message || "Unable to load live market data.";
    elements.errorPanel.classList.remove("is-hidden");
    setConnectionState("is-offline", "Feed Error");
  }
}

fetchMarketData();
setInterval(fetchMarketData, REFRESH_INTERVAL_MS);
