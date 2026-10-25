# The tailscale sidecar

The review app is reachable only on Tom's personal tailnet, at
`https://argelander.tail1afe5a.ts.net` (spec §10). The app has its own node, a
`tailscale/tailscale` container, like every other app on kumadrome (Navidrome,
closinglineup, the weekly movie email); the host's own node, `kumadrome`, carries SSH only.
The sidecar runs tailscaled in userspace mode and *serve* terminates HTTPS on 443 with the
node's `*.ts.net` certificate, then proxies to `http://api:8000` over the Compose network
(`deploy/tailscale/serve.json`). The api publishes no port, so it is closed to the LAN and
to the mini's own localhost.

| Piece | Where |
| --- | --- |
| `tailscale` service | `compose.yaml`: node name `argelander` (`TS_HOSTNAME`), container hostname `argelander-tailscale`, userspace networking, 128 MB |
| Serve config | `deploy/tailscale/serve.json`, mounted as a directory at `/config`, read-only |
| Node key | The `tailscale-state` named volume (`argelander_tailscale-state` on the host), internal disk |
| Auth key | `TS_AUTHKEY` in the mini's `.env`, blank by default |
| Tag | `tag:app`, advertised through `TS_EXTRA_ARGS` (the default in `compose.yaml`) |

The container's hostname is never `api` or `argelander`: Docker maps a container's own
hostname to itself in `/etc/hosts`, so a sidecar named `api` would proxy to itself (the 502
Navidrome once had). The directory is mounted rather than `serve.json` itself because
containerboot watches it, so a `git pull` that replaces the file reaches the running
sidecar.

Both services are part of the prod stack, so `task deploy` starts them; Watchtower keeps the
api current, and `task deploy` the sidecar. Without a node key on the state volume and
without `TS_AUTHKEY`, the sidecar waits for an interactive login and prints its URL in its
logs, so put the auth key in `.env` before the first deploy that starts it (*First start*
below).

## First start

1. In the personal tailnet's admin console, under *DNS*, check MagicDNS is on and *HTTPS
   Certificates* is enabled (already the case for Navidrome). Serve needs both.
2. *Settings → Keys → Generate auth key*: one-off (not reusable), not ephemeral, with a
   short expiry, since it is used once, and tagged `tag:app`. The tailnet policy (ops
   `docs/tailnet/policy.hujson`) already owns that tag, so it needs no edit.
3. If *Machines* already lists a node named `argelander`, delete it first, or the new one
   comes up as `argelander-1`.
4. On the mini, put the key in `.env` (`chmod 600 .env`):

   ```sh
   TS_AUTHKEY=tskey-auth-...
   ```

5. Start the sidecar and the app:

   ```sh
   docker compose up -d tailscale api
   docker compose exec tailscale tailscale status | head -1
   docker compose exec tailscale tailscale serve status
   ```

   The first line shows `argelander` with its `100.x` address; `serve status` shows
   `https://argelander.tail1afe5a.ts.net` proxying to `http://api:8000`.
6. Blank `TS_AUTHKEY=` in `.env`. `TS_AUTH_ONCE` means the key is only read when the
   state volume has no node key, so leaving it set would only leak it. A tagged node's key
   does not expire, so there is no key expiry to disable.

### Access policy

The personal tailnet's policy (ops `docs/tailnet/policy.hujson`, the system of record) is
written per role tag, so the node needs no rule of its own:

- `autogroup:member` reaches everything, so Tom's devices reach the app on 443.
- `tag:kuma` reaches `tag:app` on `tcp:443`, for Uptime Kuma's check (*Monitoring* below).
- Nothing grants `tag:app` anything, so the node reaches nothing on the tailnet. It needs
  nothing: the api reaches Postgres over the Compose network.

`TS_EXTRA_ARGS` is read only when the sidecar logs in, which `TS_AUTH_ONCE` limits to a
state volume with no node key. A node registered before the tag default (the namespace
setup before NEU-1948) keeps its node key and stays untagged, and Kuma's grant does not
reach it. Tag it in the admin console instead of re-registering: *Machines* → `argelander`
→ *Edit ACL tags* → `tag:app`. Then `docker compose exec tailscale tailscale status --json
| grep -A2 '"Tags"'` shows `tag:app`.

## Check it

From a device on the personal tailnet:

```sh
curl -fsS https://argelander.tail1afe5a.ts.net/healthz      # 200
```

The first request can take a few seconds while the certificate is issued. A 502 shows in
`docker compose logs tailscale` as `proxy error: dial tcp …:8000`: the api is down or
still starting (`docker compose ps api`).

On the mini, the api must publish nothing:

```sh
docker compose port api 8000      # "no port 8000/tcp for container argelander-api-1", exit 1
docker compose ps api             # PORTS shows 8000/tcp, with no host address before it
```

and from another machine on the LAN, `curl -m 3 http://<the mini's LAN address>:8000/healthz`
is refused.

## Monitoring

Uptime Kuma (the ops instance on kumadrome) checks the app over the tailnet, through its
sidecar's SOCKS5 proxy (ops `monitoring/uptime-kuma/README.md`, *Checking tailnet-only
services*): an HTTP(s) monitor `argelander`, URL `https://argelander.tail1afe5a.ts.net/healthz`,
*Proxy* = `uptime-kuma-tailscale:1055` with protocol **SOCKS v5 (+hostname)**. Plain *SOCKS
v5* resolves the name inside Kuma's container, which has no MagicDNS, and the monitor goes
red. `/healthz` answers 503 when Postgres is down, so the monitor catches the database as
well as the sidecar. Record the monitor in ops `docs/runbooks/uptime-kuma-monitors.md`.

## Key rotation

Two keys are involved, and neither needs routine rotation:

- **The auth key** is used once, on the first start, and then blanked from `.env`. A
  one-off key cannot be used again, so there is nothing to rotate. If one leaked before it
  was used, revoke it under *Settings → Keys*.
- **The node key** lives in the state volume. A tagged node's key does not expire. To
  rotate it anyway, for example after the volume may have been copied off the mini,
  re-register the node as below: that issues a new node key and the old one dies with the
  deleted machine.

## Re-registering the node (state volume lost)

If the `tailscale-state` volume is lost, removed (`docker compose down -v` removes it, so
never run that on the mini) or must be replaced, the sidecar starts with no node key and
waits for a login: `docker compose logs tailscale` shows `NeedsLogin` and a
`https://login.tailscale.com/a/…` URL, and the app is unreachable. Nothing else is lost:
the volume holds only the node identity.

1. In the admin console's *Machines*, delete the old `argelander`. Otherwise the new node
   registers as `argelander-1`, and `https://argelander.tail1afe5a.ts.net` keeps pointing
   at the dead one.
2. Generate a new one-off, `tag:app` auth key and follow *First start* from step 4.
3. Recreate the sidecar, so it reads the key:

   ```sh
   docker compose up -d --force-recreate tailscale
   ```

4. Blank `TS_AUTHKEY=` and run *Check it*. The node has a new `100.x` address; anything
   that used the address rather than the name (the Kuma monitor uses the name) needs
   updating.
