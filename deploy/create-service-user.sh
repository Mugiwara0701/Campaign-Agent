#!/usr/bin/env bash
# Creates the dedicated OS account the Campaign Agent runs as, so it is not the administrator's own account.
#
#   ssh -t admin@office-host 'sudo bash -s' < deploy/create-service-user.sh     (from the laptop)
#   or, on the office machine as an administrator:  sudo bash deploy/create-service-user.sh
#
# The account: no password, no sudo, no other groups, its own home; the service starts without a login (linger).
# It gets the administrator's SSH public keys, so `deploy/remote.sh init campaign-agent@host` works from the laptop.
# Then install the system parts as the administrator (deploy/bootstrap-ubuntu.sh) and everything else as that account.
# Safe to re-run. Overrides: SERVICE_USER (default campaign-agent).
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-campaign-agent}"
case "$SERVICE_USER" in
  *[!a-z0-9_-]* | "") echo "not a valid account name: $SERVICE_USER" >&2; exit 1 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "run it with sudo (see the first lines of this file)" >&2
  exit 1
fi

if id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "account $SERVICE_USER exists, left as it is"
else
  adduser --disabled-password --gecos "Campaign Agent" "$SERVICE_USER"
  echo "created account $SERVICE_USER (no password, no sudo)"
fi

if id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx -E 'sudo|admin|wheel'; then
  echo "WARNING: $SERVICE_USER is in a sudo group; remove it: deluser $SERVICE_USER sudo" >&2
fi

home=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
admin="${SUDO_USER:-}"
if [ -n "$admin" ] && [ -f "$(getent passwd "$admin" | cut -d: -f6)/.ssh/authorized_keys" ]; then
  install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$home/.ssh"
  cat "$(getent passwd "$admin" | cut -d: -f6)/.ssh/authorized_keys" >> "$home/.ssh/authorized_keys"
  sort -u "$home/.ssh/authorized_keys" -o "$home/.ssh/authorized_keys"
  chown "$SERVICE_USER:$SERVICE_USER" "$home/.ssh/authorized_keys"
  chmod 600 "$home/.ssh/authorized_keys"
  echo "copied $admin's SSH public keys to $SERVICE_USER"
else
  echo "no administrator keys copied; add yours to $home/.ssh/authorized_keys, or use: ssh-copy-id $SERVICE_USER@<host>"
fi

loginctl enable-linger "$SERVICE_USER"   # the user service keeps running when nobody is logged in
echo "done. From the laptop: bash deploy/remote.sh init $SERVICE_USER@<office-host>"
echo "Then, as the administrator, the system parts: bash deploy/bootstrap-ubuntu.sh   (see docs/runbook.md)"
