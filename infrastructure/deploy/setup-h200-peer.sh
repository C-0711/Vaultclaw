#!/bin/bash
# =============================================================================
# 0711-Vault H200 GPU Server Setup
# WireGuard Peer + Ollama + AI Service fuer den Hetzner Vault
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[>>]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }

echo ""
echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}  0711-Vault H200 GPU Peer Setup${NC}"
echo -e "${BLUE}================================================================${NC}"
echo ""

# =============================================================================
# CONFIG
# =============================================================================

HETZNER_PUBLIC_IP="${1:?Usage: $0 <hetzner-server-ip> <hetzner-wireguard-pubkey>}"
HETZNER_WG_PUBKEY="${2:?Usage: $0 <hetzner-server-ip> <hetzner-wireguard-pubkey>}"

WG_TUNNEL_IP="10.71.1.2"
HETZNER_TUNNEL_IP="10.71.1.1"

info "Hetzner Server:   $HETZNER_PUBLIC_IP"
info "Hetzner WG Key:   $HETZNER_WG_PUBKEY"
info "H200 Tunnel IP:   $WG_TUNNEL_IP"

# =============================================================================
# WIREGUARD
# =============================================================================
info "Konfiguriere WireGuard..."

apt-get update -qq && apt-get install -y -qq wireguard > /dev/null 2>&1

# Keys generieren
if [[ ! -f /etc/wireguard/private.key ]]; then
    wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
    chmod 600 /etc/wireguard/private.key
    log "WireGuard Keys generiert"
fi

H200_PRIVKEY=$(cat /etc/wireguard/private.key)
H200_PUBKEY=$(cat /etc/wireguard/public.key)

cat > /etc/wireguard/wg-vault.conf <<WGCONF
# 0711-Vault Tunnel: H200 -> Hetzner
[Interface]
Address    = ${WG_TUNNEL_IP}/24
PrivateKey = ${H200_PRIVKEY}

[Peer]
PublicKey  = ${HETZNER_WG_PUBKEY}
Endpoint   = ${HETZNER_PUBLIC_IP}:51820
AllowedIPs = ${HETZNER_TUNNEL_IP}/32, 10.71.1.0/24
PersistentKeepalive = 25
WGCONF

chmod 600 /etc/wireguard/wg-vault.conf

# IP Forwarding
sysctl -w net.ipv4.ip_forward=1 > /dev/null
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-wireguard.conf

# Starten
systemctl enable --now wg-quick@wg-vault
log "WireGuard Tunnel aktiv"

# Teste Verbindung
sleep 2
if ping -c 1 -W 3 "$HETZNER_TUNNEL_IP" &>/dev/null; then
    log "Tunnel zu Hetzner steht! (ping $HETZNER_TUNNEL_IP OK)"
else
    warn "Tunnel noch nicht aktiv. Hetzner-Seite muss den H200 Peer eintragen."
    warn "H200 Public Key: $H200_PUBKEY"
fi

# =============================================================================
# OLLAMA (falls noch nicht installiert)
# =============================================================================
if ! command -v ollama &>/dev/null; then
    info "Installiere Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    log "Ollama installiert"
else
    log "Ollama bereits vorhanden"
fi

# Ollama auf WireGuard Interface binden
mkdir -p /etc/systemd/system/ollama.service.d/

cat > /etc/systemd/system/ollama.service.d/override.conf <<OVERRIDE
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/data/ollama/models"
OVERRIDE

systemctl daemon-reload
systemctl restart ollama
log "Ollama lauscht auf 0.0.0.0:11434 (erreichbar via $WG_TUNNEL_IP)"

# Models laden
info "Lade Models (kann dauern)..."
ollama pull bge-m3:latest &
ollama pull llama4:latest &
wait
log "Models geladen: bge-m3, llama4"

# =============================================================================
# FIREWALL — Nur WireGuard Tunnel erlauben
# =============================================================================
info "Konfiguriere Firewall..."

# Ollama nur ueber WireGuard erreichbar machen
if command -v ufw &>/dev/null; then
    ufw allow from 10.71.1.0/24 to any port 11434 proto tcp comment "Ollama via WireGuard"
    ufw allow from 10.71.1.0/24 to any port 8001 proto tcp comment "AI-Service via WireGuard"
    log "Firewall: Ollama + AI-Service nur via WireGuard erreichbar"
else
    # iptables Fallback
    iptables -A INPUT -s 10.71.1.0/24 -p tcp --dport 11434 -j ACCEPT
    iptables -A INPUT -s 10.71.1.0/24 -p tcp --dport 8001 -j ACCEPT
    iptables -A INPUT -p tcp --dport 11434 -j DROP
    iptables -A INPUT -p tcp --dport 8001 -j DROP
    log "iptables: Ollama + AI-Service nur via WireGuard"
fi

# =============================================================================
# FERTIG
# =============================================================================

echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}  H200 GPU Peer Setup abgeschlossen!${NC}"
echo -e "${GREEN}================================================================${NC}"
echo ""
echo "  WireGuard Tunnel:  $WG_TUNNEL_IP <-> $HETZNER_TUNNEL_IP"
echo "  Ollama:            http://$WG_TUNNEL_IP:11434"
echo ""
echo -e "${YELLOW}  WICHTIG: Auf dem Hetzner-Server den H200 Peer eintragen:${NC}"
echo ""
echo "  In /etc/wireguard/wg-h200.conf den Peer-Block einkommentieren:"
echo ""
echo "    [Peer]"
echo "    PublicKey  = $H200_PUBKEY"
echo "    AllowedIPs = $WG_TUNNEL_IP/32"
echo "    PersistentKeepalive = 25"
echo ""
echo "  Dann: systemctl restart wg-quick@wg-h200"
echo ""
echo "  Teste: ping $WG_TUNNEL_IP  (von Hetzner)"
echo "         ping $HETZNER_TUNNEL_IP  (von H200)"
echo ""
