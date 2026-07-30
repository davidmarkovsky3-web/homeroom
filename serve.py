"""Production-ish entry point — use this whenever the app is reachable by anyone but you.

Unlike run.py, this serves through waitress with the debugger OFF. run.py is for local
development only: it enables the Werkzeug debugger, which exposes an interactive Python
console on any unhandled exception and must never face a network.

    python serve.py              # 127.0.0.1:9997 — pair this with a tunnel
    python serve.py --lan        # 0.0.0.0:9997 — reachable from your local network

Set HOMEROOM_SECRET_KEY before exposing this anywhere; otherwise session cookies are
signed with a known default and can be forged.
"""

import os
import secrets
import sys

from waitress import serve

from homeroom import create_app
from homeroom.models import User
from homeroom.seed import seed_database

PORT = int(os.environ.get("PORT", 9997))

if not os.environ.get("HOMEROOM_SECRET_KEY"):
    # Ephemeral but unguessable. Signing out everyone on restart beats a published default.
    os.environ["HOMEROOM_SECRET_KEY"] = secrets.token_hex(32)
    print("  ! HOMEROOM_SECRET_KEY not set — generated a temporary one for this process.")
    print("    Sessions will not survive a restart.")

app = create_app()

if __name__ == "__main__":
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"

    with app.app_context():
        if User.query.first() is None:
            seed_database()
            print("  Seeded demo data.")

    print(f"\n  Homeroom serving on {host}:{PORT} (debugger disabled)")
    if host == "127.0.0.1":
        print("  Loopback only — point a tunnel at this port to share it.")
    else:
        print("  Reachable from your local network.")
    print("\n  Demo accounts")
    print("    admin    principal@homeroom.edu    admin1234")
    print("    teacher  mdelacroix@homeroom.edu   teach1234")
    print("    student  student@homeroom.edu      study1234\n")

    serve(app, host=host, port=PORT, threads=8)
