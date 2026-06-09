// GOAT PRO X v10 — Layout Blueprint (Copy Ready)

export default function GoatProPreview() {
  return (
    <main className="min-h-screen bg-[#0B111E] text-white p-6">
      {/* HEADER */}
      <section className="mb-6 rounded-2xl border border-white/10 bg-[#111827] p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-wide">
              ⚡ GOAT PRO LIVE
            </h1>
            <p className="text-sm font-semibold text-white/70">
              AI Market Intelligence Terminal
            </p>
          </div>

          <div className="flex items-center gap-4">
            <div className="rounded-xl border border-green-500/40 px-4 py-2 text-green-400 font-bold">
              ● LIVE PULSE
            </div>

            <div className="rounded-xl border border-blue-500/40 px-4 py-2 text-blue-400 font-bold">
              Updated 12:06:07
            </div>
          </div>
        </div>
      </section>

      {/* HERO */}
      <section className="grid gap-6 md:grid-cols-2 mb-6">
        <div className="rounded-2xl border border-white/10 bg-[#111827] p-8">
          <p className="text-white/60 uppercase text-sm">
            GOAT SCORE
          </p>

          <h2 className="text-7xl font-black mt-3">
            87/100
          </h2>
        </div>

        <div className="rounded-2xl border border-green-500/20 bg-[#111827] p-8">
          <p className="text-white/60 uppercase text-sm">
            MARKET REGIME
          </p>

          <h2 className="text-5xl font-black text-green-400 mt-3">
            BULLISH
          </h2>

          <p className="mt-2 text-xl font-bold text-white/70">
            Trend Expansion
          </p>
        </div>
      </section>

      {/* MARKET DNA */}
      <section className="mb-6 rounded-2xl border border-white/10 bg-[#111827] p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-2xl">🧠</span>

          <h3 className="text-2xl font-black">
            SYSTEM MARKET DNA
          </h3>
        </div>

        <div className="grid md:grid-cols-4 gap-4">
          <div className="rounded-xl border border-white/10 p-4">
            <p className="text-white/60 text-sm">VWAP</p>
            <p className="text-green-400 font-bold text-xl">✅ ACTIVE</p>
          </div>

          <div className="rounded-xl border border-white/10 p-4">
            <p className="text-white/60 text-sm">PCR</p>
            <p className="text-green-400 font-bold text-xl">✅ BULLISH</p>
          </div>

          <div className="rounded-xl border border-white/10 p-4">
            <p className="text-white/60 text-sm">EMA</p>
            <p className="text-green-400 font-bold text-xl">✅ CROSS</p>
          </div>

          <div className="rounded-xl border border-white/10 p-4">
            <p className="text-white/60 text-sm">RSI</p>
            <p className="text-yellow-400 font-bold text-xl">63.4</p>
          </div>
        </div>
      </section>

      {/* HIGH VELOCITY ZONE */}
      <section className="grid gap-6 md:grid-cols-2 mb-6">
        {/* SCALP */}
        <div className="rounded-2xl border border-green-500/30 bg-[#111827] p-6">
          <div className="flex justify-between items-center">
            <h3 className="text-2xl font-black">
              ⚡ SCALP PICKS
            </h3>

            <span className="text-green-400 font-bold">
              18s
            </span>
          </div>

          <h2 className="mt-6 text-6xl font-black text-white">
            NIFTY ATM CE
          </h2>

          <div className="mt-6 grid grid-cols-2 gap-4">
            <div>
              <p className="text-white/60 text-sm">BIAS</p>
              <p className="text-green-400 font-bold">
                BULLISH
              </p>
            </div>

            <div>
              <p className="text-white/60 text-sm">
                CONFIDENCE
              </p>
              <p className="font-black text-2xl">
                86%
              </p>
            </div>
          </div>
        </div>

        {/* INTRADAY */}
        <div className="rounded-2xl border border-blue-500/30 bg-[#111827] p-6">
          <div className="flex justify-between items-center">
            <h3 className="text-2xl font-black">
              🎯 INTRADAY PICKS
            </h3>

            <span className="text-blue-400 font-bold">
              09s
            </span>
          </div>

          <h2 className="mt-6 text-6xl font-black text-white">
            BANKNIFTY
          </h2>

          <div className="mt-4 inline-flex rounded-full bg-purple-500/20 px-4 py-2 text-purple-300 font-bold">
            ⚡ SECTOR CONFLUENCE 92%
          </div>

          <div className="mt-6 grid grid-cols-2 gap-4">
            <div>
              <p className="text-white/60 text-sm">BIAS</p>
              <p className="text-green-400 font-bold">
                BULLISH
              </p>
            </div>

            <div>
              <p className="text-white/60 text-sm">
                CONFIDENCE
              </p>
              <p className="font-black text-2xl">
                84%
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* MACRO ZONE */}
      <section className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-white/10 bg-[#111827] p-5">
          <h4 className="font-black text-lg">
            📅 WEEKLY
          </h4>
          <p className="mt-3 font-bold">
            RELIANCE
          </p>
          <p className="text-green-400">
            +6.8%
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[#111827] p-5">
          <h4 className="font-black text-lg">
            📦 SWING
          </h4>
          <p className="mt-3 font-bold">
            TATA MOTORS
          </p>
          <p className="text-green-400">
            +12%
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[#111827] p-5">
          <h4 className="font-black text-lg">
            🚀 POSITIONAL
          </h4>
          <p className="mt-3 font-bold">
            BANKING INDEX
          </p>
          <p className="text-green-400">
            BULLISH
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[#111827] p-5">
          <h4 className="font-black text-lg">
            💼 INVESTMENT
          </h4>
          <p className="mt-3 font-bold">
            HDFC BANK
          </p>
          <p className="text-pink-400">
            AI GRADE A+
          </p>
        </div>
      </section>
    </main>
  );
}
