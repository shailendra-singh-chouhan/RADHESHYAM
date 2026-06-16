import sys
import locale
from flask import Flask, make_response

# ====================== UTF-8 FIX ======================
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

def clean_surrogates(text):
    """Fix lone surrogates that cause UnicodeEncodeError on Render"""
    if not isinstance(text, str):
        text = str(text)
    try:
        # Best method: roundtrip through bytes
        return text.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
    except:
        # Fallback
        return text.encode('utf-8', errors='replace').decode('utf-8')


# ====================== FLASK APP ======================
app = Flask(__name__)

@app.route('/')
def home():
    try:
        # ================== YOUR CONTENT HERE ==================
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Stock Dashboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; }}
                h1 {{ color: #2c3e50; }}
                .card {{ background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <h1>🚀 Minimal Stock Dashboard</h1>
            <p>Deployment successful with Unicode fix applied.</p>
            
            <div class="card">
                <h2>Current Time (IST)</h2>
                <p>{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>

            <!-- Example: You can add yfinance or SmartAPI here later -->
            <div class="card">
                <h2>Ready for Market Data</h2>
                <p>Add your yfinance / SmartAPI logic inside this route.</p>
            </div>
        </body>
        </html>
        """

        # Clean before returning
        cleaned_html = clean_surrogates(html_content)

        response = make_response(cleaned_html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response

    except Exception as e:
        print(f"Error in home route: {e}")
        import traceback
        traceback.print_exc()
        return "<h2>Internal Server Error</h2><p>Please check logs.</p>", 500


# Optional: Health check endpoint
@app.route('/health')
def health():
    return {"status": "healthy", "message": "Unicode fix applied"}, 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
