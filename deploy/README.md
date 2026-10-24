# Deploying to the Mac mini

The prod stack (`compose.yaml` alone, spec §10) runs on `kumadrome`, the 2011 Mac mini on
Ubuntu Server, from a clone of this repo at `~/argelander`. Deploys are `git pull` plus
Compose, wrapped as `task deploy`; nothing runs from CI and nothing is managed by Coolify.
Tom runs the one-time host steps below by hand, since they need `sudo`.

| Piece | Where |
| --- | --- |
| Checkout | `~/argelander` on the mini, on `main`, cloned over HTTPS (the repo is public, so no deploy key) |
| Secrets and host paths | `~/argelander/.env`, mode 600: `USB_ROOT`, `POSTGRES_PASSWORD`, the backup's restic and B2 settings, and `TS_AUTHKEY` once |
| Postgres data | the `argelander_postgres-data` named volume, on the internal SSD |
| Its nightly backup | restic to Backblaze B2: `deploy/restore.md` |
| The USB SSD | the 2 TB Samsung T7, `UUID=0e995573-d89e-44d3-8473-65ee5aa3e296`, mounted at `/mnt/music` and shared with Navidrome and the Kumas |
| Argelander's part of it | `/mnt/music/argelander`, bind-mounted at `/mnt/argelander` (`USB_ROOT`): `cache/`, `plots/`, `astropy/` |
| Docker waits for it | `/etc/systemd/system/docker.service.d/argelander-usb.conf` |
| The review app's sidecar | `deploy/tailscale.md` |

## Why a bind mount of its own

The workers' mount guard (spec §10, `docs/runbook.md` §10) relies on `USB_ROOT` being a
mount point: with the SSD missing, the directory under it is an empty one on the boot disk,
so the workers start, find no `.argelander-cache` marker and say so in their logs. The T7
is already mounted at `/mnt/music`, and Navidrome and the Kumas keep their data at its
root. Pointing `USB_ROOT` at `/mnt/music/argelander` would break the guard (with the SSD
missing that path does not exist, and Docker refuses to create the workers rather than
letting them log anything), and pointing it at `/mnt/music` would hand the workers the
music library. So the Argelander directory on the T7 gets its own mount point,
`/mnt/argelander`, through a bind mount in `/etc/fstab` that depends on `/mnt/music`.
(A directory hidden on the boot disk underneath the `/mnt/music` mount point would also
exist with the SSD missing, but nothing on the mini would show it was there, and anything
that unmounted `/mnt/music` would expose it.)

## Why Docker wants the mount rather than requiring it

The drop-in uses `WantsMountsFor=`, not the `RequiresMountsFor=` the handover names. Both
order `docker.service` after the mounts, so on a reboot where the T7 is slow to appear
Docker waits for it, up to the 30-second `x-systemd.device-timeout`, instead of starting
first and binding the empty directories underneath. They differ when the T7 is missing:
under `Requires`, a failed mount stops `docker.service` from starting at all, taking
Navidrome and both Kumas down with it and leaving the workers no way to report anything.
Under `Wants`, Docker starts after the timeout and the workers log the mount guard's error.
The cost is that Navidrome and the Kumas start too, on the empty `/mnt/music` directory on
the boot disk, as they already do today with no drop-in; under `Requires` they would stay
down.
(`WantsMountsFor=` needs systemd 256; the mini has 259.)

## One-time host setup

Run on the mini (`ssh kumadrome`).

1. **go-task.** The snap puts `task` in `/snap/bin`, which is on the `PATH` that
   non-interactive SSH sessions get, so `task deploy:remote` finds it.

   ```sh
   sudo snap install task --classic
   task --version
   ```

2. **The checkout.**

   ```sh
   git clone https://github.com/tomboone/argelander.git ~/argelander
   ```

3. **The T7's fstab line.** It is already there, from the Navidrome setup; check that it
   is by UUID with `nofail` and the device timeout:

   ```sh
   grep /mnt/music /etc/fstab
   # UUID=0e995573-d89e-44d3-8473-65ee5aa3e296  /mnt/music  ext4  defaults,nofail,x-systemd.device-timeout=30  0  2
   ```

   `nofail` keeps a missing T7 from dropping boot into emergency mode; the timeout caps
   the wait for it at 30 seconds instead of systemd's default 90. On a fresh install, find
   the UUID with `sudo blkid /dev/sda1` and add the line above.

4. **Argelander's directories on the T7, and the Cache marker.** The mini's `tom` is uid
   1000, the image's user, and owns `/mnt/music`, so this needs no `sudo` or `chown`:

   ```sh
   mkdir -p /mnt/music/argelander/{cache,plots,astropy}
   touch /mnt/music/argelander/cache/.argelander-cache
   ```

5. **The bind mount.** The mount point is created on the boot disk, where it stays an
   empty directory whenever the bind is not mounted. `x-systemd.requires-mounts-for` makes
   the bind wait for `/mnt/music` and fail with it; `nofail` keeps that failure from
   failing the boot. Append the line once:

   ```sh
   sudo mkdir -p /mnt/argelander
   echo '/mnt/music/argelander  /mnt/argelander  none  bind,nofail,x-systemd.requires-mounts-for=/mnt/music  0  0' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload
   sudo mount /mnt/argelander
   findmnt /mnt/argelander        # SOURCE /dev/sda1[/argelander]
   ls -a /mnt/argelander/cache    # .argelander-cache
   ```

6. **The Docker drop-in.** Wanting `/mnt/argelander` pulls in `/mnt/music` too, through
   the bind's dependency, so Navidrome and the Kumas get the same wait.

   ```sh
   sudo mkdir -p /etc/systemd/system/docker.service.d
   printf '[Unit]\nWantsMountsFor=/mnt/argelander\n' | sudo tee /etc/systemd/system/docker.service.d/argelander-usb.conf
   sudo systemctl daemon-reload
   systemctl show docker.service -p Wants -p After | grep -o mnt-argelander.mount
   # printed twice: once for Wants, once for After
   ```

   No Docker restart is needed; the dependency matters at boot.

7. **The `.env`.** Only the variables Compose interpolates go here; `compose.yaml` sets
   every service's own environment, so `.env.example`'s dev values do not belong in it. The
   password ends up in the `DATABASE_URL`, so keep it to URL-safe characters, as hex is.
   This one overwrites an existing `.env`:

   ```sh
   cd ~/argelander
   (umask 077; cat > .env <<EOF
   USB_ROOT=/mnt/argelander
   POSTGRES_PASSWORD=$(openssl rand -hex 24)
   EOF
   )
   ```

8. **The backup.** The B2 bucket, its key, the restic lines for `.env` and `restic init`:
   `deploy/restore.md`, steps 1–4. Without them the `backup` service refuses to start and
   restarts in a loop, naming what is missing; the rest of the stack runs regardless.

9. **The first deploy.** Exactly the same as every later one: `task deploy` (below). It
   initialises Postgres on its first start and migrates it. Then `task backup:now` takes
   the first backup rather than waiting for 03:30.

The review app and its tailnet node come later, with M6: `deploy/tailscale.md` covers the
auth key and the first start.

## Deploying

From the laptop:

```sh
task deploy:remote          # ssh kumadrome 'cd ~/argelander && task deploy'
```

or on the mini, `cd ~/argelander && task deploy`. It refuses to run without `USB_ROOT` and
`POSTGRES_PASSWORD` in the environment or `.env`, or off `main`. Only the mini's `.env`
sets `USB_ROOT` (`.env.example` leaves it commented out), which is what keeps the task
from bringing the prod stack up on the laptop. Then it runs, against `compose.yaml` alone:

| Step | Why |
| --- | --- |
| `git pull --ff-only` | A checkout that has diverged from `origin/main` stops the deploy rather than merging |
| `docker compose pull --ignore-buildable` | Newer `postgres:16` and `tailscale/tailscale:stable` |
| `docker compose build --pull` | The prod image, on a freshly pulled `python:3.12-slim`, and the backup image on `debian:trixie-slim` |
| `docker compose run --rm fetcher argelander db-upgrade` | Migrates before the new workers start; starts Postgres first if it is down |
| `docker compose up -d --remove-orphans` | Recreates whatever changed; drops containers of services removed from `compose.yaml` |
| `docker compose ps` | Shows the result: the workers turn healthy within a minute |

The deploy reads the Taskfile before it pulls, so a change to the `deploy` task itself
takes effect on the deploy after the one that brings it.

The deploy starts the review app and its `tailscale` sidecar with the rest. The sidecar
needs `TS_AUTHKEY` in `.env` the first time, before it has a node key (`deploy/tailscale.md`).
With the T7 unplugged, the deploy's `up -d` fails on the api, whose `${USB_ROOT}/plots` bind
has no source: the workers still start and log the missing Cache marker, but the task exits
non-zero. Plug the SSD back in and deploy again.

To roll back, revert the change on `main` and deploy again; the deploy only ever moves the
checkout forward.

## Checking it after a reboot

With the T7 attached:

```sh
findmnt /mnt/music /mnt/argelander      # both mounted
cd ~/argelander && docker compose ps     # fetcher and analyzer up, healthy; backup up
docker compose logs --tail 20 fetcher    # claiming Jobs or idling, no mount-guard error
```

With the T7 detached, Docker and the rest of the mini still come up, about 30 seconds
late:

```sh
systemctl is-active docker               # active
findmnt /mnt/argelander                  # nothing: the empty directory on the boot disk
systemctl status mnt-argelander.mount    # inactive (dead); the journal has "Dependency failed for /mnt/argelander"
docker compose logs --tail 5 fetcher analyzer
# CACHE_DIR /usb/cache has no .argelander-cache marker, so the USB SSD is not mounted; ...
```

The workers keep exiting and restarting. Plug the T7 back in and mount both:

```sh
sudo mount /mnt/music && sudo mount /mnt/argelander
docker compose restart fetcher analyzer   # or wait for their next restart
```

A worker that was running when the T7 dropped out turns unhealthy instead
(`docs/runbook.md` §10); restart it the same way.
