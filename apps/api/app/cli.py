"""Operator commands.

  python -m app.cli migrate                 apply database migrations (stamps a legacy create_all schema at 0001 first)
  python -m app.cli check                   validate production config + DB at head (non-zero exit on problems)
  python -m app.cli seed                    upsert plans, presets, bots, AI agents
  python -m app.cli gen-key                 print a new MT5_CREDENTIALS_KEY (Fernet)
  python -m app.cli create-admin EMAIL NAME             create/promote a super-admin (password read from ADMIN_PASSWORD env)
  python -m app.cli create-worker NAME [MAX_INSTANCES]  register a worker host and print its token once
"""
import os
import sys

from sqlalchemy import select


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    from .config import get_settings
    settings = get_settings()
    if cmd == "gen-key":
        from .security import new_fernet_key
        print(new_fernet_key())
        return 0
    if cmd == "migrate":
        from .db import current_revision, migrate_to_head
        migrate_to_head()
        print("database at", current_revision())
        return 0
    if cmd == "check":
        from .db import current_revision, head_revision
        problems = settings.validate_for_production()
        cur, head = current_revision(), head_revision()
        if cur != head:
            problems.append(f"database revision {cur} is not head {head}: run migrate")
        for p in problems:
            print("PROBLEM:", p)
        print("OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    from .db import SessionLocal
    if cmd == "seed":
        _seed(SessionLocal)
        print("seeded")
        return 0
    if cmd == "create-admin":
        if len(args) < 2:
            print("usage: create-admin EMAIL NAME  (password from ADMIN_PASSWORD env var)")
            return 2
        pw = os.environ.get("ADMIN_PASSWORD", "")
        if len(pw) < 12:
            print("Set ADMIN_PASSWORD (12+ characters) in the environment; it is not accepted on the command line.")
            return 2
        from .models import Tenant, User
        from .security import encrypt, hash_password, new_totp_secret, totp_uri
        with SessionLocal() as db:
            u = db.scalar(select(User).where(User.email == args[0].lower()))
            if not u:
                t = Tenant(name=f"{args[1]} (platform staff)")
                db.add(t)
                db.flush()
                u = User(tenant_id=t.id, email=args[0].lower(), name=args[1], password_hash=hash_password(pw), email_verified=True)
                db.add(u)
            else:
                u.password_hash = hash_password(pw)
            secret = new_totp_secret()
            u.role, u.status, u.totp_secret_enc, u.totp_enabled = "admin", "active", encrypt(secret), True
            u.token_version = (u.token_version or 0) + 1
            db.commit()
            print("Admin ready:", u.email)
            print("TOTP secret (add to an authenticator app now; it is not shown again):", secret)
            print("otpauth URI:", totp_uri(secret, u.email))
        return 0
    if cmd == "create-worker":
        if not args:
            print("usage: create-worker NAME [MAX_INSTANCES]")
            return 2
        from .models import Worker
        from .security import new_worker_token, worker_token_hash
        with SessionLocal() as db:
            if db.scalar(select(Worker).where(Worker.name == args[0])):
                print("worker exists; rotate its token in the admin console")
                return 1
            token = new_worker_token()
            db.add(Worker(name=args[0], token_hash=worker_token_hash(token), max_instances=int(args[1]) if len(args) > 1 else 4, capabilities={}))
            db.commit()
            print("Worker registered:", args[0])
            print("WORKER_TOKEN (shown once):", token)
        return 0
    print(__doc__)
    return 2


def _seed(SessionLocal) -> None:
    from .bots.registry import seed as seed_bots
    from .services import agents, catalog
    with SessionLocal() as db:
        catalog.seed(db)
        seed_bots(db)
        agents.seed(db)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
