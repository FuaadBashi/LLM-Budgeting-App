# Running it

## On this Mac — one click

Double-click **`Start Finance OS.command`** in Finder. It starts Postgres if
needed, then the API and the app, waits until both actually answer, and opens the
browser. Closing the window stops both.

To get a real app icon in the Dock rather than a browser tab: open
`http://localhost:3000` in Chrome or Edge, then **Install** from the address bar.
It gets its own window with no URL bar. It still needs the servers running, so
the launcher is what you click first.

## On your phone — same Wi-Fi

1. Double-click the launcher on the Mac. It prints the phone URL, e.g.
   `http://192.168.1.247:3000`.
2. Open that on the phone.
3. **iPhone**: Share → Add to Home Screen. **Android**: menu → Install app.

You get an icon that opens full-screen with no browser chrome. Photographing a
receipt opens the camera directly.

Two things to know. The Mac must be awake and the launcher running — the phone is
a window onto it, not a copy. And the IP can change when the router reassigns it;
give the Mac a static lease if that becomes annoying.

## From anywhere — Tailscale

LAN only works at home, and phones will not treat plain HTTP as fully installable
on Android. [Tailscale](https://tailscale.com) is free for personal use and fixes
both: it puts the Mac and the phone on one private network with real HTTPS
certificates, reachable from anywhere, without exposing anything to the internet.

    tailscale serve --bg 3000

Then use the `https://…ts.net` address it prints. With HTTPS you should also set
`COOKIE_SECURE=true` in `backend/.env`.

This is the setup worth having. `deploy/` covers a public domain instead, which
is more work and more exposure than a personal ledger usually justifies.
