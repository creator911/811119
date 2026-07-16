# CandyCast

CandyCast public site, member services, and administrator application.

## Local run

Create `runtime/candycast_secrets.json` with the administrator PBKDF2 salt and
hash, then run:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python standalone_pulseutv_server.py \
  --source site \
  --site-dir site \
  --db-path runtime/candycast.sqlite3 \
  --workdir runtime \
  --host 127.0.0.1 \
  --port 8770 \
  --no-prepare
```

The public site is at `http://127.0.0.1:8770/` and the administrator site is at
`http://127.0.0.1:8770/admin/`.

## Ubuntu first deployment

1. Install `nginx`, `python3-venv`, `sqlite3`, `certbot`,
   `python3-certbot-nginx`, and `ufw`.
2. Create the non-root `candycast` account and directories:

```bash
sudo adduser --disabled-password --gecos "" candycast
sudo install -d -o candycast -g www-data -m 0750 /opt/candycast/app
sudo install -d -o candycast -g www-data -m 0750 /var/lib/candycast
sudo install -d -o candycast -g www-data -m 0750 /var/backups/candycast
sudo install -d -o root -g candycast -m 0750 /etc/candycast
sudo -u candycast python3 -m venv /opt/candycast/venv
sudo -u candycast git clone https://github.com/creator911/811119.git /opt/candycast/app
sudo -u candycast /opt/candycast/venv/bin/pip install -r /opt/candycast/app/requirements.txt
```

3. Put the real administrator salt and hash in
   `/etc/candycast/candycast.env` with mode `0640`:

```text
CANDYCAST_ADMIN_PASSWORD_SALT=replace_with_private_hex
CANDYCAST_ADMIN_PASSWORD_HASH=replace_with_private_hex
```

4. Copy `deploy/candycast.service` to `/etc/systemd/system/candycast.service`.
   Allow the deploy account to restart only this service:

```text
candycast ALL=(root) NOPASSWD: /bin/systemctl restart candycast
```

5. Start with `deploy/nginx/candycast.bootstrap.conf`, point both DNS records to
   the server, and issue the certificate:

```bash
sudo certbot --nginx -d mycandycast.com -d www.mycandycast.com
```

Then install `deploy/nginx/candycast.conf`, run `sudo nginx -t`, and reload
Nginx. Set Cloudflare SSL/TLS mode to `Full (strict)`.

6. Open only SSH, HTTP, and HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

The Python service remains bound to `127.0.0.1:8770`.

## Database transfer

The database is never committed. Before the first transfer:

```bash
deploy/backup_database.sh
sha256sum pulseutv_standalone_runtime_1909/candycast.sqlite3
scp pulseutv_standalone_runtime_1909/candycast.sqlite3 \
  candycast@SERVER:/var/lib/candycast/candycast.sqlite3.upload
ssh candycast@SERVER \
  'sha256sum /var/lib/candycast/candycast.sqlite3.upload'
```

Compare both hashes before renaming the upload to
`/var/lib/candycast/candycast.sqlite3`.

## GitHub Actions secrets

- `DEPLOY_HOST`: Ubuntu server address
- `DEPLOY_USER`: `candycast`
- `DEPLOY_SSH_KEY`: private half of the dedicated deploy key

The matching public key belongs in `/home/candycast/.ssh/authorized_keys`.
