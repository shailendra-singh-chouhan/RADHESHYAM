import os
from flask import Flask, render_template_string
from db_manager import DatabaseManager
from logzero import logger

app = Flask(__name__)
db = DatabaseManager()

@app.route('/')
def dashboard():
    try:
        stats = db.get_stats()
        logger.info(f"Dashboard Load: Wins={stats.get('wins')}")
        data = {"spot": 23165.4, "stats": stats}
        return render_template_string(HTML_TEMPLATE, m=data)
    except Exception as e:
        logger.error(f"Critical Fail: {e}")
        return "Internal Error", 500

@app.route('/health')
def health_check():
    return {"status": "ok"}, 200

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-900 text-white p-4">
    <h1 class="text-blue-500 font-black">GOAT PRO V17 (PRODUCTION)</h1>
    <div class="text-2xl">Wins: {{ m.stats.wins }} | Losses: {{ m.stats.losses }}</div>
</body>
</html>
"""

if __name__ == '__main__':
    app.run()
