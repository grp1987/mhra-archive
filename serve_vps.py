"""
Production entrypoint for the VPS. If cert.pem + key.pem exist it serves HTTPS
(self-signed, via cheroot — the passcode is encrypted); otherwise plain HTTP via
waitress. The passcode gate activates when MHRA_PW is set.

    set MHRA_PW=...   set PORT=8090   python serve_vps.py
"""
import os

import app  # noqa: E402  (imports read MHRA_PW at import time)

HERE = os.path.dirname(os.path.abspath(__file__))
CERT = os.path.join(HERE, "cert.pem")
KEY = os.path.join(HERE, "key.pem")


def main():
    port = int(os.environ.get("PORT", "8090"))
    host = os.environ.get("HOST", "0.0.0.0")
    gate = "passcode ON" if os.environ.get("MHRA_PW") else "OPEN — no passcode!"
    https = os.path.exists(CERT) and os.path.exists(KEY)
    scheme = "https" if https else "http"
    print(f"MHRA Archive serving on {scheme}://{host}:{port}  [{gate}]")

    if https:
        # cheroot supports TLS in-process; self-signed cert encrypts the passcode.
        from cheroot.wsgi import Server
        from cheroot.ssl.builtin import BuiltinSSLAdapter
        server = Server((host, port), app.app, numthreads=4, request_queue_size=20)
        server.ssl_adapter = BuiltinSSLAdapter(CERT, KEY)
        try:
            server.start()
        except KeyboardInterrupt:
            server.stop()
    else:
        from waitress import serve
        serve(app.app, host=host, port=port, threads=4, connection_limit=50,
              channel_timeout=60)


if __name__ == "__main__":
    main()
