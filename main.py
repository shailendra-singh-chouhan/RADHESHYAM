from flask import Flask, send_file
import os

app = Flask(__name__)

@app.route('/')
def home():
    # Ye tumhari dashboard.html file ko serve karega
    return send_file('dashboard.html')

if __name__ == '__main__':
    # Render ke liye zaroori configuration
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
