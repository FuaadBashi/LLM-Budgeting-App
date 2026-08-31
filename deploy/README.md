# Deploying

The app is safe on localhost as-is. Exposing it to a network needs three things,
and the third is the one that is easy to forget.

## 1. HTTPS

`deploy/Caddyfile` terminates TLS and proxies both services. Caddy obtains and
renews the certificate itself.

```
caddy run --config deploy/Caddyfile
```

## 2. Tell the app it is on HTTPS

In `backend/.env`:

```
COOKIE_SECURE=true
CORS_ORIGINS=https://your-domain
```

`COOKIE_SECURE` stops the session cookie travelling in clear text and switches on
HSTS. `CORS_ORIGINS` must list the real origin — cookies are only sent to origins
on that list, so logging in silently fails without it.

Until `COOKIE_SECURE` is true the API logs a warning at every startup.

## 3. The database

Postgres should not be reachable from the network. Bind it to localhost and let
only the API talk to it. Scheduled backups write to `BACKUP_DIR` on the same
machine — copy them somewhere else, or a disk failure takes both.

## Login throttling

Failed logins back off exponentially, capped at ten minutes, and the counter is
in-process. A restart clears it. That is acceptable for a single-instance
personal app and worth knowing: an attacker who can restart the process has
already won by other means.
