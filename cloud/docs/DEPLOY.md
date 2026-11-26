# Deploying Kvist Cloud

*(Русская версия: [DEPLOY.ru.md](DEPLOY.ru.md))*

One Debian 12 server: nginx in front, the control server (one process) behind it, PostgreSQL 15. Backups go to a
second machine, see [BACKUP.md](BACKUP.md).

Customers' machines are KVM virtual machines on the same server (see "Customer machines" below), so one server is
the whole product: sign-in, payments (or hand-granted access while billing is off), machines, backups.

## Layout on the server

```
/opt/kvist-cloud/releases/<id>/   one folder per deploy (the code, its own .venv, vendor/)
/opt/kvist-cloud/current          a link to the live release; switching is one atomic link change
/etc/kvist-cloud/cloud.env        all settings and secrets (0640 root:kvist-cloud)
/var/lib/kvist-cloud              data dir (exports), written by the service only
```

`vendor/` holds the five tiny core files the server imports (`deploy/kernel-files.txt`); the repository does not
need to be on the server.

## First time

```bash
# PostgreSQL and a database
sudo apt install postgresql nginx python3-venv rsync curl
sudo -u postgres createuser kvist_cloud --pwprompt
sudo -u postgres createdb -O kvist_cloud kvist_cloud

# the service user and folders
sudo adduser --system --group --home /var/lib/kvist-cloud kvist-cloud
sudo install -d -o kvist-cloud -g kvist-cloud /var/lib/kvist-cloud
sudo install -d /opt/kvist-cloud/releases /etc/kvist-cloud

# settings: fresh secrets, then fill in the rest (it lists what)
sudo scripts/gen-secrets.sh /etc/kvist-cloud/cloud.env
sudoedit /etc/kvist-cloud/cloud.env
```

Save `KVIST_CLOUD_MASTER_KEY` from that file in a password manager and on paper, **not** on the server's backups.
Fill in `PUBLIC_URL`, `DATABASE_URL` (`postgresql+psycopg://…`), `KVIST_CLOUD_DATA_DIR`, `GITHUB_CLIENT_ID/SECRET`
(callback `<PUBLIC_URL>/auth/github/callback`), `ADMIN_GITHUB_LOGINS`, the Platega values or `BILLING_ENABLED=false`.
`CHATGPT_SIGNIN` stays off (the default): "Sign in with ChatGPT" would send many customers' plans from this server's
one address, which OpenAI's abuse protection looks for, and it is the customers' accounts that get banned. Turn it on
only after OpenAI has agreed; while off, the provider is not offered and its routes answer 503.

Debian 13 works too (PostgreSQL 17 instead of 15; see the version note in [BACKUP.md](BACKUP.md)). If Cloudflare is
in front and the host's nginx already has an Origin certificate, use `deploy/nginx/kvist-cloud-cloudflare.conf` in place
of `kvist-cloud.conf` (no certbot; read its header for the 100 MB upload limit of the proxy).

Install the service and nginx (replace `cloud.example.org`, issue the certificate):

```bash
sudo cp deploy/systemd/kvist-cloud.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo sed 's/cloud.example.org/<your-name>/g' deploy/nginx/kvist-cloud.conf | sudo tee /etc/nginx/conf.d/kvist-cloud.conf
sudo certbot --nginx -d <your-name>      # or your own certificate
sudo systemctl enable kvist-cloud
```

Set up backups before the first deploy ([BACKUP.md](BACKUP.md)): a deploy refuses to migrate without a fresh dump.

## Every deploy

On your computer:

```bash
scripts/deploy.sh user@server
```

**When only the frontends changed** since the revision the server runs (everything in `cloud/frontend`, `cloud/mobile`,
`cloud/workspace`, or outside what the server and the guests run, such as the phone apps' native code), it uploads
just the built frontend into the live release and runs `activate_frontend.sh`: the old frontend is swapped for the
new one, the service restarts (the phone page is read at start), and if `/healthz` does not answer the old frontend
goes back. No venv, no backup, no migration, no guest image: seconds. Any other change, or `deploy.sh --full`, makes
a full release:

It builds the frontend, copies the tree into a new release (nothing is pushed to any git remote), and runs
`activate_release.sh` on the server, which, in this order:

1. installs the release into its own venv and **checks the configuration** (`python -m kvist_cloud.prodcheck`):
   https, PostgreSQL, a real provider, sign-in configured, an admin, no development flags, a valid master key;
2. takes a database backup (refuses without backup settings; `KVIST_SKIP_BACKUP=1` overrides, knowingly);
3. migrates the database;
4. switches the `current` link and restarts the service, then waits up to 60 s for `/healthz`;
5. **if it does not come up, switches back to the previous release** and exits non-zero;
6. runs `python -m kvist_cloud.integrity` (schema at head, the ledger's append-only guard, signs and references in
   the ledger, paid checkouts that granted nothing, duplicate machines) and keeps the last 5 releases.

Because the old release keeps running against the migrated database during a rollback, **a migration must stay
compatible with the release before it**: add columns first, remove them a release later.

Afterwards, from anywhere: `scripts/smoke_prod.sh https://<your-name>`. It looks from outside: HTTPS and
certificate, `/healthz`, security headers, no `/docs`, anonymous `/api/me` and `/admin` refused, the sign-in going
to GitHub (not to a development login), and the runners' WebSocket path getting through the proxy.

## Customer machines (`MACHINE_PROVIDER=libvirt`)

Each customer's machine is a KVM virtual machine on the server itself (Debian 12 guest, one per account). The server
needs `/dev/kvm`: bare metal, or a VPS with nested virtualization. Set `MACHINE_PROVIDER=libvirt` in `cloud.env`.

- **Your computer** needs podman or docker: `deploy.sh` builds the guest's wheels (kvist, kvist-runner and all
  dependencies) in a Debian 12 container, because the guest's Python is 3.11, whatever the host's is. They are cached
  while the sources are unchanged and land in `/opt/kvist-cloud/wheels`, owned by root.
- **On every deploy** `activate_release.sh` runs `scripts/install_vm_host.sh`: installs KVM/libvirt/nftables, copies
  the two root-side scripts into `/usr/local/lib/kvist-vm/`, loads the network rules and starts the helper socket.
- **The server stays unprivileged.** It asks a small root helper (`vm_ctl.py`) over `/run/kvist-vm.sock`, which only
  the server's user can open; the helper validates every field and can only touch domains named `kvist-<number>`.
  `setup_vm.sh` makes the VM; the runner inside dials out to the gateway with its own token, nothing connects in
  (no ssh, no open port).
- **Isolation** (`deploy/vm-firewall.nft`): a VM cannot reach another VM, the host's own services, private ranges or
  the provider's metadata address, only the internet. The network (`deploy/vm-network.xml`) has libvirt's DNS off:
  a host that already runs a DNS server cannot share port 53 with it, and guests get public resolvers by DHCP.
- **The first boot takes minutes**: the guest installs its packages (a browser, a virtual screen, build tools). The
  server waits up to 20 minutes for a new machine to dial in, 3 for a later wake.
- **Ceilings** are the largest plan (4 vCPU, 8 GB, 150 GB), checked again by `setup_vm.sh`, which also refuses when
  the host would be left with less than 2 GB of memory or 20 GB of disk.

## Rotating the master key

```bash
sudo /opt/kvist-cloud/current/scripts/rotate_master_key.sh start    # backup, new key in front, re-encrypt, restart
# check the site, save the NEW key off the server
sudo /opt/kvist-cloud/current/scripts/rotate_master_key.sh finish   # drop the old key
```

**Keep the old key offline for as long as backups older than the rotation exist** (6 months by default): they hold
secrets encrypted with it.

## Things to know

- **One worker only.** The runners' gateway, the event hub and the screen frames live in the process's memory; the
  unit runs `--workers 1`. More than one server process would split runners from browsers.
- **A restart drops open sockets**; runners and browsers reconnect by themselves, but a deploy during a task is
  visible. Deploy when it is quiet.
- **`pip install` reaches PyPI** on the server during a deploy and nothing pins the dependency versions yet.
- **Tested** in a Debian 12 container against PostgreSQL 15 with `systemctl` replaced by a stand-in (first deploy,
  backup before migration, smoke, key rotation, rollback of a release that cannot start, release pruning) and the
  nginx file through `nginx -t` plus a real WebSocket handshake; then on a real Debian 13 server with real systemd:
  `deploy.sh` over ssh and rsync, the backup timers and a restore test on the second host, and the VM provider with
  two live guests (first boot and runner dial-in, sleep, wake, delete; the isolation rules, probed from inside the
  guests). **Not tested:** certbot, a task run on a customer machine with a real model key, many VMs at once.
