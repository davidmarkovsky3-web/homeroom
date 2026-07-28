"""Development entry point.

    python run.py            # seeds on first run, then serves on http://localhost:9997
    python run.py --reset    # wipe the database and reseed before serving
"""

import sys

from homeroom import create_app
from homeroom.extensions import db
from homeroom.models import User
from homeroom.seed import seed_database

PORT = 9997

app = create_app()

if __name__ == "__main__":
    reset = "--reset" in sys.argv

    with app.app_context():
        if reset or User.query.first() is None:
            seed_database(reset=reset)
            print("Seeded demo data.")

    print(f"\n  Homeroom running at http://localhost:{PORT}")
    print("  Marketing site:  /")
    print("  Sign in:         /auth/login")
    print("\n  Demo accounts")
    print("    admin    principal@homeroom.edu    admin1234")
    print("    teacher  mdelacroix@homeroom.edu   teach1234")
    print("    student  student@homeroom.edu      study1234\n")

    app.run(host="127.0.0.1", port=PORT, debug=True)
