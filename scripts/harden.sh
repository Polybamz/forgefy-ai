#!/usr/bin/env bash
# Baseline hardening for a fresh Ubuntu 24.04 VPS (plan phase 1). Run as root:
#   sudo ./scripts/harden.sh
#
#   - installs security updates and turns on automatic security updates
#   - firewall: only SSH, HTTP and HTTPS in
#   - fail2ban against SSH password guessing
#   - SSH: key login only, no root password login — but ONLY when an SSH key
#     is already installed, so this can never lock you out
#
# Safe to re-run.
set -euo pipefail

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

say "Installing updates and security tools"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ufw fail2ban unattended-upgrades curl openssl

say "Automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

say "Firewall: allow SSH, 80, 443 — deny everything else inbound"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/    /'
# Docker publishes container ports past ufw. Only caddy publishes any (80/443),
# which are meant to be public; never add a ports: entry to ollama or postgres.

say "fail2ban (bans IPs that keep failing SSH logins)"
systemctl enable --now fail2ban >/dev/null

# SSH: only switch off passwords when someone can already log in with a key.
has_key=false
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
  [ -s "$f" ] && grep -qE '^(ssh-|ecdsa-|sk-)' "$f" && has_key=true
done
if $has_key; then
  say "SSH: key login only"
  cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd)
  say "Done. Keep this session open and test a NEW ssh login before closing it."
else
  warn "No SSH key found in any authorized_keys — leaving password login ON so you are not locked out."
  warn "From your PC:  ssh-copy-id root@THIS_SERVER   (or add your public key to ~/.ssh/authorized_keys)"
  warn "then re-run this script to switch passwords off."
fi
