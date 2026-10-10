# Security

## Controls in place

| Area | Control |
|---|---|
| Passwords | Argon2id; 12+ chars; 5 failures → 15 min lock; reset via emailed 6-digit code (15 min, single use, no account enumeration); reset/change revokes all sessions |
| Sessions | 15 min access JWT + 30 day refresh token bound to an `auth_sessions` row; rotation on every refresh; reuse of a rotated token revokes all sessions; `token_version` revokes instantly (logout-all, suspension, role change) |
| 2FA | TOTP; mandatory for admin/support; enforced server-side on every admin endpoint |
| Authorization | Server-side role checks (`owner/member/viewer`, `support` read-only, `admin`), tenant ownership on every resource (404 for foreign ids), PostgreSQL RLS as a second layer |
| Admin actions | Reason required for state changes; every action in `audit_log` with user, IP, target tenant |
| MT5 credentials | Encrypted with a dedicated Fernet key (`MT5_CREDENTIALS_KEY`); write-only API; sent only to the worker that holds the instance lease and only on a fresh claim; deleted on disconnect/revoke; never logged (`scrub()` redacts secret keys) |
| Workers | Per-host random token (only its HMAC stored); disable/rotate from the console; can only act on leased instances; fencing token blocks stale workers |
| Trading | Server-approved order intents, platform halt, live-trading platform gate + owner authorization, hard lot cap, SL required, daily order cap, uncertain-order blocking, idempotent client order ids |
| EA | License key (hashed), bound account, HMAC-signed expiring responses, 24 h offline trade block |
| Billing | Paid access only from verified provider events (Stripe signature / signed mock in dev); idempotent event ids; mock provider refused in production |
| Web | Strict CSP (self + API + Google Fonts only), X-Frame-Options DENY, nosniff, referrer policy; WebSocket uses a 60 s ticket, not the access token |
| API | Security headers + HSTS (prod), CORS allow-list without credentials, request size limits (nginx), rate limits (nginx + in-app), generic error bodies with correlation id, docs disabled in prod |
| Config | Startup refuses unsafe production settings (default secrets, SQLite, mock billing, dev email codes, wildcard CORS, missing credentials key) |
| Secrets | Not in source control (`.gitignore`), env files 640 root:nexfox, worker `.env` ACL'd to its user |

## Findings fixed during this work

* Plaintext MT5 account password committed in `config_new.py` — removed; **rotate that password at the broker**,
  since it existed in plain text in the project folder (and possibly in copies/backups of it).
* Unauthenticated mock billing webhook allowed anyone to grant themselves a paid subscription — now HMAC-signed and
  refused in production.
* Free trials never expired (`trialing` was valid forever) — fixed, with scheduler-driven expiry and notices.
* Admin sessions were filtered by RLS to the admin's own tenant, and the tenant setting was lost after the first
  commit in a request — fixed (per-transaction binding, none for staff).
* Unauthenticated Telegram linking callback — now requires a shared secret.
* Refresh tokens were not revocable; no logout; no account suspension — implemented.
* Access token in the WebSocket URL — replaced by short-lived tickets (and the old `?token=` fallback removed).

Found by the 2026-10-10 adversarial review (each reproduced, fixed, and covered by a regression test — see
docs/TESTING.md):

* A stolen, already-dead refresh token could sign the user out of every new session at will, even after a password
  reset (the reuse check ran before the token-version check). Rotation is now also atomic.
* A $0 `invoice.paid` (Stripe sends one when a trial starts) turned a trial into paid access, unlocking live accounts.
  Only a paid amount for the same subscription changes status now.
* Login answers revealed which emails exist and which accounts were locked; now identical responses and timing.
* No attempt limit on emailed codes; 2FA guesses did not count toward lockout. Codes now die after 5 wrong guesses.
* 2FA could be enrolled with only a stolen access token; it now needs the current password and signs out older sessions.
* A customer config rollback could undo admin-only values (e.g. magic number). Rollback now keeps locked fields.
* A revoked account still received a valid EA licence. The EA gateway now refuses it and trading is disabled.
* Trading safety on the worker:
  * a worker cut off from the platform kept managing positions after its lease could pass to another worker. It now
    stops its bots after half a lease, and every order, including break-even, partial close and trailing, is refused
    once the local lease deadline passes;
  * dry run still sent position-management orders. These are now simulated;
  * a close-all queued while a bot was stopped fired at the next start. It is now refused, and old ones are cancelled
    on start;
  * commands could be lost between "sent" and acknowledged. They are now re-delivered until acknowledged and run
    once;
  * a replayed order id could be approved again. It is now approved only while still open, re-checking pause and halt;
  * stop loss and take profit on the wrong side of the price were accepted. They are now refused;
  * the result journal could lose entries on a lease loss during replay. They are now kept.
* nginx dropped `X-Forwarded-For` on most locations, so every client looked like 127.0.0.1 to rate limits and the
  audit log. Fixed.
* `ENV=PROD` (any spelling other than the exact ones) silently disabled every production check. It now fails closed.
  Production also refuses an invalid `MT5_CREDENTIALS_KEY` and `APP_SECRET == EA_SIGNING_SECRET`.
* Telegram link tokens never rotated, so an old deep link could redirect alerts. They now rotate and are single use.
* Viewers could run diagnostics. This now needs owner or member.

## Residual risks / decisions for the operator

* Tokens are stored in `localStorage` (mitigated by the strict CSP and short access-token lifetime). Moving to
  httpOnly cookies needs same-site API/web hosting and CSRF tokens.
* In-app rate limits are per process; nginx `limit_req` is the primary limiter.
* The EA signing secret is compiled into the `.ex5`; a leaked binary exposes it (inherent to the EA model).
* Legal texts (`/terms`, `/privacy`, `/risk`) are templates and need legal review.
* Dependency audit (2026-10-10): `pip-audit` clean for API and worker. `npm audit` reports 2 production findings
  (the postcss copy bundled in Next 15; fix = Next 16) and 7 build-tool findings (Tailwind 3 chain; fix = Tailwind 4).
  Details in docs/TESTING.md. Run both audits in CI.
* **RLS fails open when no tenant is set.** Staff, scheduler and gateway sessions deliberately set no tenant, so the
  policy lets them see all rows. That means RLS only catches a forgotten tenant filter in customer requests, which
  always set one. `users`, `audit_log`, `auth_sessions`, `email_codes`, `ea_heartbeats` and `bot_config_history` have no
  policy. A stricter design: a separate DB role for staff and system work, and deny when the tenant is unset.
* **MT5 login claiming:** any workspace can bind any server+login first, which locks out the real owner until support
  intervenes, and the 409 reveals which logins are taken. A worker connection check proves control of the account
  for worker bots, but it does not yet release other workspaces' unverified claims. Planned: only verified bindings
  block others.
* Signup returns 409 for an existing email (account enumeration), and `/password/forgot` sends email synchronously
  (a timing difference). Both are acceptable for launch; queueing email removes the timing signal.
* Market candles are global and fed by EAs: a licensed customer could spoof the dashboard chart (display only; no
  trading logic reads them).
* Support staff can create diagnostic AI tasks (read-only analysis) on any instance.
