# The tailscale sidecar

The review app is reachable only on Tom's personal tailnet, at `http://argelander:8000`
(spec §10). The mini itself is on no tailnet: like Navidrome and the personal Uptime Kuma,
the app has its own node, a `tailscale/tailscale` container. Unlike theirs, nothing is
proxied. The `api` service runs inside the sidecar's network namespace
(`network_mode: service:tailscale`), and the sidecar runs tailscaled in userspace mode,
which hands each inbound tailnet connection to `127.0.0.1` in that namespace. The api
publishes no port, so 8000 is closed to the LAN and to the mini's own localhost.

The namespace also has an address on the Compose network, which the api needs for Postgres.
So the `web` command (M6) must listen on `127.0.0.1:8000`, not `0.0.0.0`: bound to every
interface, the app would also answer on that bridge address, from the mini's host and from
every container in the project as `tailscale:8000`. Loopback is all tailscaled needs.
(Connections over the node's IPv6 tailnet address are dialed to `[::1]` and refused;
clients fall back to the IPv4 address.)

| Piece | Where |
| --- | --- |
| `tailscale` service | `compose.yaml`: node name `argelander` (`TS_HOSTNAME`), userspace networking, 128 MB |
| Node key | The `tailscale-state` named volume (`argelander_tailscale-state` on the host), internal disk |
| Auth key, tags | `TS_AUTHKEY` and `TS_EXTRA_ARGS` in the mini's `.env`, both blank by default |

Both services are part of the prod stack, so `task deploy` starts them and keeps them
current. Without a node key on the state volume and without `TS_AUTHKEY`, the sidecar waits
for an interactive login and prints its URL in its logs, so put the auth key in `.env`
before the first deploy that starts it (*First start* below).

## First start

1. In the personal tailnet's admin console, *Settings → Keys → Generate auth key*:
   one-off (not reusable), not ephemeral, with a short expiry, since it is used once.
   Add the tag `tag:argelander` only if the tailnet uses tags (see ACLs below).
2. On the mini, put it in `.env` (`chmod 600 .env`):

   ```sh
   TS_AUTHKEY=tskey-auth-...
   # TS_EXTRA_ARGS=--advertise-tags=tag:argelander   # only with a tagged key
   ```

3. Start the sidecar and the app:

   ```sh
   docker compose up -d tailscale api
   docker compose exec tailscale tailscale status | head -1
   ```

   The first line shows `argelander` with its `100.x` address.
4. Blank `TS_AUTHKEY=` in `.env`. `TS_AUTH_ONCE` means the key is only read when the
   state volume has no node key, so leaving it set would only leak it.
5. Untagged node: in the admin console's *Machines*, choose *Disable key expiry* on
   `argelander`, as for `kuma` and `navidrome`. A tagged node has key expiry disabled
   already.

### ACLs

The personal tailnet's default policy lets every device reach every other on any port, so
an untagged node needs nothing more. If the policy is restricted, tag the node and grant
the devices that review Proposals access to port 8000 only:

```json
"tagOwners": { "tag:argelander": ["autogroup:admin"] },
"grants": [
  { "src": ["autogroup:member"], "dst": ["tag:argelander"], "ip": ["tcp:8000"] }
]
```

The node needs no outbound access to the tailnet: the api reaches Postgres over the
Compose network, which the namespace keeps.

## Check it

From a device on the personal tailnet (MagicDNS):

```sh
curl -fsS http://argelander:8000/healthz      # 200
```

On the mini, the port must be closed to everything else, including the namespace's address
on the Compose network:

```sh
curl -m 3 http://127.0.0.1:8000/healthz       # connection refused: nothing published
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' argelander-tailscale-1)
curl -m 3 "http://$ip:8000/healthz"           # connection refused: web binds loopback
```

and from another machine on the LAN, `curl -m 3 http://<the mini's LAN address>:8000/healthz`
is refused too.

## When the sidecar restarts on its own

The api lives in the sidecar's namespace. When Compose restarts or recreates the sidecar
(`docker compose restart tailscale`, or an `up -d` after pulling a new image), it restarts
or recreates the api with it (`depends_on: … restart: true`). When the sidecar restarts any other way,
a crash, an out-of-memory kill or `docker restart`, the api keeps the old, dead namespace:
`http://argelander:8000` is refused, the api cannot resolve `postgres`, and `docker compose
up -d` does not repair it, because the api's configuration has not changed. Restart the
api, which rejoins the sidecar's current namespace:

```sh
docker compose restart api
```

Only a check that comes in over the tailnet notices this, such as the `/healthz` monitor
NEU-1727 adds: the api's own healthcheck probes loopback inside the dead namespace and can
stay green. After a reboot of the mini, check the URL once for the same reason.

## Key rotation

Two keys are involved, and neither needs routine rotation:

- **The auth key** is used once, on the first start, and then blanked from `.env`. A
  one-off key cannot be used again, so there is nothing to rotate. If one leaked before it
  was used, revoke it under *Settings → Keys*.
- **The node key** lives in the state volume. With key expiry disabled (or a tagged node)
  it does not expire. To rotate it anyway, for example after the volume may have been
  copied off the mini, re-register the node as below: that issues a new node key and the
  old one dies with the deleted machine.

If key expiry was left on and the node key expired, the admin console shows `argelander`
as *Expired* and the URL stops answering. Re-register it the same way, then disable key
expiry this time.

## Re-registering the node (state volume lost)

If the `tailscale-state` volume is lost, removed (`docker compose down -v` removes it, so
never run that on the mini) or must be replaced, the sidecar starts with no node key and
waits for a login: `docker compose logs tailscale` shows `NeedsLogin` and a
`https://login.tailscale.com/a/…` URL, and the app is unreachable. Nothing else is lost:
the volume holds only the node identity.

1. In the admin console's *Machines*, delete the old `argelander`. Otherwise the new node
   registers as `argelander-1`, and `http://argelander:8000` keeps pointing at the dead
   one.
2. Either open the login URL from the logs in a browser signed in to the personal tailnet,
   or generate a new one-off auth key and follow *First start* from step 2.
3. Recreate the pair, so the sidecar reads the key and the api joins its namespace:

   ```sh
   docker compose up -d --force-recreate tailscale api
   ```

4. Blank `TS_AUTHKEY=`, disable key expiry on the new node, and run *Check it*. The node
   has a new `100.x` address; anything that used the address rather than the name
   (the Kuma monitor should use the name) needs updating.
