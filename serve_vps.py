"""
Production entrypoint for the VPS — serves the Flask app via waitress (the Flask
dev server is not for internet exposure). The passcode gate activates when MHRA_PW
is set (run_vps.ps1 sets it from secret_pw.txt).

    set MHRA_PW=...   set PORT=8090   python serve_vps.py
"""
import os

from waitress import serve

import app  # noqa: E402  (imports read MHRA_PW at import time)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    host = os.environ.get("HOST", "0.0.0.0")
    gate = "passcode ON" if os.environ.get("MHRA_PW") else "OPEN — no passcode!"
    print(f"MHRA Archive serving on http://{host}:{port}  [{gate}]")
    # threads bounded so a burst of traffic can't monopolise the trading box
    serve(app.app, host=host, port=port, threads=4, connection_limit=50,
          channel_timeout=60)
