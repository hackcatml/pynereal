# HTTPS Access

Optional same-host Caddy reverse proxy for PyneReal. No domain is required:
a public IPv4 address can be used directly. A public DNS name also works.
This configuration targets Ubuntu with the official Caddy systemd service.

```text
Browser / installed app -> HTTPS :443 -> Caddy -> HTTP 127.0.0.1:9001
```

This does not install anything through PyneReal Setup/Update, change the
data-service listener, restart runners, or modify exchange connections.
Caddy runs independently of Python and handles certificate issuance/renewal.
There is no external traffic relay, Service Worker, proxy response cache, or
additional polling. Keep the proxy running for certificate renewal even when
data_service is stopped.

## Local Mobile Test (No Public IP)

Starting data_service starts **only HTTP 9001**, not an HTTPS proxy. For a phone
on the same trusted Wi-Fi, use `Caddyfile.local` and a separately started Caddy.
It binds to the specified LAN address, rejects non-private source addresses,
and does not configure public certificates or install trust on any device.
Unlike the public configuration, this test configuration has **no login**: use
it only on a trusted LAN, and never forward 8443/8444 through your router.

Prepare an ignored `.runtime/pwa-https/.env` with your actual LAN address and
an absolute path to a certificate-only download directory:

```text
PYNE_LOCAL_HOST=192.168.1.29
PYNE_HTTP_PORT=9001
PYNE_LOCAL_TRUST_DIR=/absolute/path/to/pynereal/.runtime/pwa-https/trust
```

From the repository root, with Caddy installed (or use its local binary path):

```bash
mkdir -p .runtime/pwa-https/trust
export XDG_DATA_HOME="$PWD/.runtime/pwa-https/data"
export XDG_CONFIG_HOME="$PWD/.runtime/pwa-https/config"
caddy start --config data_service/deploy/https/Caddyfile.local --adapter caddyfile --envfile .runtime/pwa-https/.env --pidfile .runtime/pwa-https/caddy.pid > .runtime/pwa-https/proxy.log 2>&1
openssl x509 -in "$XDG_DATA_HOME/caddy/pki/authorities/local/root.crt" -outform DER -out .runtime/pwa-https/trust/pynereal-local-ca.cer
openssl x509 -in "$XDG_DATA_HOME/caddy/pki/authorities/local/root.crt" -noout -fingerprint -sha256
```

Check that 8443, 8444 and localhost 2020 are unused before starting. The generated
CA may take a moment to appear; check `proxy.log` before exporting it. Only the
public `.cer` file goes in the download directory. Keep the CA private key in
the separate `data/` tree and never share it or commit runtime state.

On iPhone/iPad, use Safari on the same Wi-Fi:

1. Download `http://<LAN-IP>:8444/pynereal-local-ca.cer`. This endpoint serves only
   the public certificate, not the Hub or its APIs. Verify the certificate's
   SHA-256 fingerprint against the local terminal output before trusting it,
   or transfer the local certificate file directly using AirDrop.
2. Install the downloaded certificate profile in **Settings > General > VPN &
   Device Management** (or **Profile Downloaded**).
3. Enable it in **Settings > General > About > Certificate Trust Settings**.
   The CA name is **PyneReal Local Test CA**. A profile install alone does not
   enable TLS trust. See [Apple's instructions](https://support.apple.com/en-us/102390).
4. Open **`https://<LAN-IP>:8443/`**, without a certificate warning, then add it
   to the home screen. Use the Mac's LAN IP, not the phone's localhost.

Android's user CA installation steps depend on the device. Install the CA via
the device's security/credential settings and verify Chrome accepts it before
testing installation. This local trust setup is not needed for production with
a valid public certificate. Remove the test CA profile when testing is finished.

Stop **only this proxy** with `caddy stop --address 127.0.0.1:2020`. It does not
stop data_service. Conversely, stopping/restarting data_service does not stop or
start Caddy: an active proxy will return 502 while the backend is unavailable.
If the LAN IP changes, update the local env file, stop and start this proxy again,
and use the new URL; keep the CA data directory to avoid reinstalling trust.

### Local Watchlist Connection Diagnostics

Both the local and public configurations use HTTP/1.1 and HTTP/2, with HTTP/3
disabled following the Safari/PWA Watchlist connection issue. They return
`Alt-Svc: clear` to invalidate earlier HTTP/3 advertisements. HTTPS and certificate
verification remain enabled. The limited connection log below is local-only;
it is not enabled by the public configuration.

Limited connection metadata is written to
`.runtime/pwa-https/connections.jsonl` for `/`, `/static/dashboard.js`, `/ws/hub`,
and `/ws/watchlist`. Each file rolls at 2 MB, keeping two rotated files. Headers,
query strings, and message bodies are not logged; the browser User-Agent is
recorded separately. A successful HTTP/1.1 WebSocket has status `101`. WebSocket
access entries are written when the connection ends, so an empty log does not
by itself prove that an open WebSocket never reached Caddy.

If Watchlist keeps reconnecting without a data_service `[accepted]` entry,
compare these proxy logs with the browser before changing exchange collection
code. Fully close and reopen the installed app after changing proxy protocols.
An isolated Chromium test is not sufficient to confirm iPhone PWA behavior.

## Prerequisites

- Caddy **2.11.4 or later** from the [official distribution](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
  Check `caddy version`; older distro packages may not support IP certificates
  correctly. No custom Caddy build or Certbot installation is needed.
- A stable public IPv4 address assigned/forwarded to this server, or a DNS name
  resolving to it. Do not use `192.168.*`, `10.*`, `127.0.0.1`, or CGNAT addresses
  for public certificate issuance. This guide does not cover IPv6-only setups.
- TCP **80 and 443** forwarded to this server and available for Caddy, plus
  outbound HTTPS for the certificate authority. This configuration deliberately
  uses HTTP-01 validation on port 80. Keep that port reachable for renewals;
  normal HTTP page requests redirect to HTTPS. Do not send login credentials
  over HTTP. No UDP port is required for HTTP/1.1, HTTP/2, or WebSocket support.
- Restrict port **9001** to the server itself or a trusted private network.
  Otherwise clients can bypass the proxy password using the original HTTP URL.
  Keep the Caddy administration port **2019** local-only as configured.

Do not blindly enable/reset a firewall: preserve SSH and existing services.
If another proxy already owns 80/443, integrate with it instead of installing
a second listener or replacing its configuration.

## Ubuntu Setup

Run these commands on the Ubuntu server, not on your Mac or phone. First apply
the PyneReal version containing this directory, then run the file-install commands
from the repository root. Installing the proxy does not install/update PyneReal.
The Caddy package starts a default HTTP welcome page, but it does not expose
PyneReal until configured below.
If Caddy already serves other applications, merge this site into the existing
configuration instead of using these replacement commands.

### Install Caddy

For a first-time installation, use the
[official stable Ubuntu repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

```bash
sudo apt update
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
caddy version
```

Require version 2.11.4 or later for this configuration. If the package/repository
is already installed, do not overwrite its key or repository files blindly.
Use the existing installation's upgrade procedure. Check whether ports 80/443
are in use before installing; do not stop another application's proxy.

### Configure PyneReal HTTPS

1. Generate a strong, unique login password hash interactively:

   ```bash
   caddy hash-password
   ```

   The password is not echoed. Do not pass it with `--plaintext`, put it in a
   URL, or publish the resulting hash. This is a proxy login, not an exchange
   credential or a new PyneReal account system.

2. Install the configuration and local variables. For an existing deployment,
   preserve `/etc/caddy/Caddyfile` and any existing drop-ins before replacing them.
   Do not repeat the example-env copy on an already configured server.

   ```bash
   sudo install -o root -g caddy -m 0640 data_service/deploy/https/Caddyfile /etc/caddy/Caddyfile
   sudo install -o root -g caddy -m 0640 data_service/deploy/https/pynereal.env.example /etc/caddy/pynereal.env
   sudoedit /etc/caddy/pynereal.env
   ```

   Fill in all three blank values. For `PYNE_HTTPS_HOST`, enter the actual
   public IPv4 address (for example `203.0.113.10` is only a documentation
   placeholder), or a DNS name. Do not include `https://` or `:9001`.
   Use a simple login name without whitespace. Paste the whole bcrypt hash,
   including its `$` characters, without shell expansion. Keep this file out
   of Git; do not source it as a shell script.

   Example structure (replace the address, name, and hash with your own values):

   ```text
   PYNE_HTTPS_HOST=203.0.113.10
   PYNE_HTTPS_USER=pynereal
   PYNE_HTTPS_PASSWORD_HASH=<complete output from caddy hash-password>
   PYNE_HTTP_PORT=9001
   ```

   Do not enter the plaintext password or leave the placeholder as the hash.
   Ensure the host/cloud firewall allows inbound TCP 80/443, keeps SSH accessible,
   and restricts public access to 9001 before exposing this configuration. If
   changing `[hub].host` to `127.0.0.1`, apply it during the planned data-service
   restart. The local test ports 8443/8444 are not used on the public server.

3. Validate before starting, then enable the dedicated configuration:

   ```bash
   sudo -u caddy caddy validate --config /etc/caddy/Caddyfile --envfile /etc/caddy/pynereal.env
   sudo install -d -m 0755 /etc/systemd/system/caddy.service.d
   sudo install -m 0644 data_service/deploy/https/pynereal.conf /etc/systemd/system/caddy.service.d/pynereal.conf
   sudo systemctl daemon-reload
   sudo systemctl enable caddy
   sudo systemctl restart caddy
   sudo journalctl -u caddy -n 50 --no-pager
   ```

   Missing/invalid authentication values fail validation rather than providing
   anonymous access. Validation does not prove certificate issuance: wait for
   Caddy's certificate-success log. If validation fails, do not start/reload it.
   The supplied service override avoids `--environ` so the password hash is not
   printed at startup. Do not use `caddy environ` for shared diagnostics.

4. Open **`https://<public-ip>/`** (or the configured DNS name), authenticate,
   then add it to the home screen. Do not append `:9001`: that remains HTTP.
   Do not bypass a certificate warning; check the Caddy logs, reachability,
   clock, and configured IP instead. Each browser or installed app may prompt
   for the proxy login separately.

The HTTPS layer can connect to an already-running data_service. Changing its
bind address, or applying PyneReal's new PWA routes, still requires the usual
data-service restart. No restart is performed by these proxy configuration files.

## Prevent Direct HTTP Bypass

For same-host deployment, the preferred Hub configuration in
`workdir/config/realtime_trade.toml` is:

```toml
[hub]
host = "127.0.0.1"
port = 9001
```

Edit the existing section, keeping other settings. Apply at the next planned
data-service restart. Until then, restrict public access to 9001 using the
existing host/cloud firewall. This changes direct LAN/public HTTP access;
verify any existing integrations before changing the bind address. Do not
expose the service until this bypass is closed.

## Streaming and Performance

Caddy forwards WebSocket upgrades automatically. The Hub/chart already choose
`wss:` when opened via HTTPS. AI's `text/event-stream` responses are flushed
immediately by Caddy; no global `flush_interval -1`, request-body buffering,
or forced response buffering is added. Client cancellation retains its normal
behavior. The proxy does not retry trading actions via a custom retry policy.

The public listener permits HTTP/1.1 and HTTP/2 only and sends `Alt-Svc: clear`,
matching the local Safari/PWA connection mitigation. This leaves TLS and
WebSocket support enabled and does not require a UDP 443 rule. After replacing
an older proxy configuration, fully close and reopen the installed app to drop
existing connections. Production issuance and real-device behavior still need
to be checked on the actual server; local success is not a deployment check.

The HTTP upstream idle keep-alive is four seconds, below Uvicorn's current
five-second default, to reduce stale-connection resets. This is not a four-second
request timeout and does not expire active WebSockets or long-running API calls.
There are no added read/stream timeouts. TLS and proxying consume some CPU;
separate processes are not CPU isolation. Measure on the actual server before
claiming unchanged latency. A proxy config reload may reconnect browser streams;
it does not restart strategy runners.

## Certificates and Updates

[Let's Encrypt IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability.html)
use the `shortlived` profile and last **160 hours**, so renewal must be automatic.
The explicit ACME issuer avoids Caddy's default local/private CA for IP hosts.
Caddy manages renewal while running; do not delete its persistent storage
(normally `/var/lib/caddy/.local/share/caddy` with the official service).
Do not schedule manual issuance every time data_service starts.

Normal PyneReal Updates do not overwrite `/etc/caddy` or update Caddy. Review
future proxy-template changes and apply them deliberately. After changing the
site or password in the environment file, validate as above, then:

```bash
sudo systemctl reload caddy
```

Systemd supplies the environment file to each reload command as well as startup.
Changing the systemd drop-in itself requires `daemon-reload` and a Caddy restart.
Upgrade the Caddy package independently to receive security fixes. A failed
certificate renewal will eventually prevent trusted HTTPS access even if the
Python server remains healthy; monitor `journalctl -u caddy` and certificate expiry.

## Checks and Rollback

- Unauthenticated HTTPS `/`, `/api/sessions`, `/manifest.webmanifest`, and
  WebSocket handshakes must be denied. Authenticated manifest/icons must load.
- Hub, chart, Account, Watchlist WebSockets and AI streaming must keep working.
- Confirm certificate trust without browser warnings and test actual mobile
  home-screen installation, CSV uploads, server Update, and background/resume.
- Verify that the original public `http://<ip>:9001` cannot bypass authentication.
- A new HTTPS origin has separate local storage from HTTP. Recheck browser-side
  preferences; server-side sessions and history remain in the same data_service.
- To roll back only the proxy, stop Caddy if it serves only PyneReal, or restore
  its previous configuration and reload. This does not stop data_service.
  Keep the backend restricted; use local access or an SSH tunnel for diagnosis
  rather than reopening unauthenticated HTTP to the Internet.

References: [Caddy TLS issuer](https://caddyserver.com/docs/caddyfile/directives/tls),
[reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[authentication](https://caddyserver.com/docs/caddyfile/directives/basic_auth),
[systemd](https://caddyserver.com/docs/running).
