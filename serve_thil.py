"""Production entrypoint for the THIL account portal."""
import os

from waitress import serve

from thil_portal import app


if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8095")),
          threads=4, connection_limit=50, channel_timeout=60)
