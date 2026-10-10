"""Deprecated wrapper. Use:  ADMIN_PASSWORD='...' python -m app.cli create-admin EMAIL "NAME"

The password is read from the environment (not argv, which is visible in process lists and shell history).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.cli import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) == 4 and not os.environ.get("ADMIN_PASSWORD"):
        print("Passing the password as an argument is no longer supported. Set ADMIN_PASSWORD in the environment.")
        sys.exit(2)
    sys.exit(main(["create-admin", *sys.argv[1:3]]))
