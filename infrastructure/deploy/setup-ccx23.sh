#!/bin/bash
# =============================================================================
# 0711-Vault + OpenClaw — Hetzner CCX23 Single-Server Deployment
# Ziel: 10 Clients, Document Vault mit AI Pipeline via H200
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[>>]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "${RED}[XX]${NC} $1"; exit 1; }

header() {
    echo ""
    echo -e "${BLUE}================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}================================================================${NC}"
    echo ""
}

# =============================================================================
# PREFLIGHT
# =============================================================================
header "0711-Vault Hetzner CCX23 Setup"

[[ $EUID -ne 0 ]] && fail "Bitte als root ausfuehren: sudo bash $0"

# Detect if running on Hetzner
if curl -s -m 2 http://169.254.169.254/hetzner/v1/metadata/hostname &>/dev/null; then
    log "Hetzner Cloud erkannt"
else
    warn "Nicht auf Hetzner? Script laeuft trotzdem, aber WireGuard-IP muss manuell gesetzt werden."
fi

PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || echo "UNKNOWN")
info "Oeffentliche IP: $PUBLIC_IP"

# =============================================================================
# KONFIGURATION
# =============================================================================
header "Konfiguration"

DOMAIN="${VAULT_DOMAIN:-vault.0711.io}"
H200_IP="${H200_ENDPOINT:-}"
ACME_EMAIL="${ACME_EMAIL:-admin@0711.io}"
DATA_DIR="/opt/0711-vault"
COMPOSE_DIR="$DATA_DIR/compose"

read -p "Domain [$DOMAIN]: " input && DOMAIN="${input:-$DOMAIN}"
read -p "ACME Email [$ACME_EMAIL]: " input && ACME_EMAIL="${input:-$ACME_EMAIL}"
read -p "H200 GPU Server IP/Hostname (fuer WireGuard Tunnel) [$H200_IP]: " input && H200_IP="${input:-$H200_IP}"

if [[ -z "$H200_IP" ]]; then
    warn "Keine H200-IP angegeben. GPU-Pipeline wird spaeter konfiguriert."
fi

echo ""
info "Domain:     $DOMAIN"
info "H200:       ${H200_IP:-nicht konfiguriert}"
info "Daten:      $DATA_DIR"
echo ""
read -p "Weiter? (y/N) " confirm
[[ ! "$confirm" =~ ^[Yy]$ ]] && fail "Abgebrochen."

# =============================================================================
# SYSTEM PACKAGES
# =============================================================================
header "System-Pakete installieren"

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw \
    wireguard \
    fail2ban \
    unattended-upgrades \
    jq \
    htop \
    ncdu \
    > /dev/null 2>&1

log "System-Pakete installiert"

# Docker
if ! command -v docker &>/dev/null; then
    info "Installiere Docker..."
    curl -fsSL https://get.docker.com | sh -s -- --quiet
    systemctl enable --now docker
    log "Docker installiert"
else
    log "Docker bereits vorhanden"
fi

# Docker Compose Plugin
if ! docker compose version &>/dev/null; then
    info "Installiere Docker Compose Plugin..."
    apt-get install -y -qq docker-compose-plugin > /dev/null 2>&1
    log "Docker Compose Plugin installiert"
else
    log "Docker Compose bereits vorhanden"
fi

# =============================================================================
# FIREWALL
# =============================================================================
header "Firewall konfigurieren"

ufw --force reset > /dev/null 2>&1
ufw default deny incoming
ufw default allow outgoing

# SSH
ufw allow 22/tcp comment "SSH"

# HTTP/HTTPS (Traefik)
ufw allow 80/tcp comment "HTTP"
ufw allow 443/tcp comment "HTTPS"

# WireGuard (H200 Tunnel)
ufw allow 51820/udp comment "WireGuard"

# Deny everything else
ufw --force enable
log "Firewall aktiv: SSH, HTTP, HTTPS, WireGuard"

# =============================================================================
# FAIL2BAN
# =============================================================================
header "Fail2Ban konfigurieren"

cat > /etc/fail2ban/jail.local <<'JAIL'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = ssh
logpath = %(sshd_log)s

[traefik-auth]
enabled  = true
port     = http,https
filter   = traefik-auth
logpath  = /opt/0711-vault/logs/traefik-access.log
maxretry = 10
JAIL

cat > /etc/fail2ban/filter.d/traefik-auth.conf <<'FILTER'
[Definition]
failregex = ^<HOST> .* (401|403) .*$
FILTER

systemctl enable --now fail2ban
log "Fail2Ban aktiv"

# =============================================================================
# WIREGUARD — TUNNEL ZUR H200
# =============================================================================
header "WireGuard Tunnel (Hetzner <-> H200)"

WG_DIR="$DATA_DIR/wireguard"
mkdir -p "$WG_DIR"

if [[ ! -f "$WG_DIR/server_private.key" ]]; then
    wg genkey | tee "$WG_DIR/server_private.key" | wg pubkey > "$WG_DIR/server_public.key"
    chmod 600 "$WG_DIR/server_private.key"
    log "WireGuard Keys generiert"
else
    log "WireGuard Keys existieren bereits"
fi

SERVER_PRIVKEY=$(cat "$WG_DIR/server_private.key")
SERVER_PUBKEY=$(cat "$WG_DIR/server_public.key")

cat > /etc/wireguard/wg-h200.conf <<WGCONF
# 0711-Vault <-> H200 GPU Tunnel
[Interface]
Address    = 10.71.1.1/24
ListenPort = 51820
PrivateKey = $SERVER_PRIVKEY
PostUp     = iptables -A FORWARD -i wg-h200 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown   = iptables -D FORWARD -i wg-h200 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# H200 GPU Server — Peer-Key muss nach Setup eingetragen werden
# [Peer]
# PublicKey  = <H200_PUBLIC_KEY>
# AllowedIPs = 10.71.1.2/32
# Endpoint   = ${H200_IP:-H200_IP_HIER_EINTRAGEN}:51820
# PersistentKeepalive = 25
WGCONF

chmod 600 /etc/wireguard/wg-h200.conf

# Aktiviere IP Forwarding
sysctl -w net.ipv4.ip_forward=1 > /dev/null
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-wireguard.conf

if [[ -n "$H200_IP" ]]; then
    warn "WireGuard Config vorbereitet. H200-Peer muss noch konfiguriert werden."
    warn "H200 braucht diesen Public Key: $SERVER_PUBKEY"
fi

log "WireGuard vorbereitet"

# =============================================================================
# VERZEICHNISSTRUKTUR
# =============================================================================
header "Verzeichnisse erstellen"

mkdir -p "$DATA_DIR"/{compose,data/{postgres,redis,neo4j},logs,backups,certs}
mkdir -p "$COMPOSE_DIR"

log "Verzeichnisse unter $DATA_DIR erstellt"

# =============================================================================
# SECRETS GENERIEREN
# =============================================================================
header "Secrets generieren"

SECRETS_FILE="$DATA_DIR/.env"

if [[ -f "$SECRETS_FILE" ]]; then
    warn ".env existiert bereits — wird NICHT ueberschrieben"
    source "$SECRETS_FILE"
else
    POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '=/+')
    NEO4J_PASSWORD=$(openssl rand -base64 24 | tr -d '=/+')
    REDIS_PASSWORD=$(openssl rand -base64 24 | tr -d '=/+')
    JWT_SECRET=$(openssl rand -base64 48 | tr -d '=/+')
    VAULT_ENCRYPTION_KEY=$(openssl rand -base64 32)
    VAULT_KEY_SALT=$(openssl rand -base64 32)

    cat > "$SECRETS_FILE" <<ENVFILE
# =============================================================================
# 0711-Vault Secrets — GEHEIM HALTEN!
# Generiert: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# =============================================================================

# Domain
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL

# PostgreSQL
POSTGRES_USER=vault
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=vault

# Neo4j
NEO4J_PASSWORD=$NEO4J_PASSWORD

# Redis
REDIS_PASSWORD=$REDIS_PASSWORD

# Auth
JWT_SECRET=$JWT_SECRET

# Albert Encryption (ChaCha20-Poly1305)
VAULT_ENCRYPTION_KEY=$VAULT_ENCRYPTION_KEY
VAULT_KEY_SALT=$VAULT_KEY_SALT

# H200 GPU (ueber WireGuard Tunnel)
OLLAMA_HOST=http://10.71.1.2:11434
AI_SERVICE_URL=http://10.71.1.2:8001

# Embedding & Vision Models (auf H200)
EMBEDDING_MODEL=bge-m3:latest
VISION_MODEL=llama4:latest
ENVFILE

    chmod 600 "$SECRETS_FILE"
    log "Secrets generiert und gespeichert in $SECRETS_FILE"
fi

# =============================================================================
# DOCKER COMPOSE
# =============================================================================
header "Docker Compose erstellen"

cat > "$COMPOSE_DIR/docker-compose.yml" <<'COMPOSE'
# =============================================================================
# 0711-Vault — Hetzner CCX23 Single-Server Deployment
# 4 vCPU / 16 GB RAM / 160 GB SSD
# GPU-Verarbeitung laeuft remote auf H200 via WireGuard
# =============================================================================

services:

  # =========================================
  # REVERSE PROXY + TLS
  # =========================================
  traefik:
    image: traefik:v3.0
    container_name: vault-traefik
    restart: unless-stopped
    command:
      - "--api.dashboard=true"
      - "--api.insecure=false"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.web.http.redirections.entryPoint.to=websecure"
      - "--entrypoints.web.http.redirections.entryPoint.scheme=https"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
      - "--certificatesresolvers.letsencrypt.acme.email=${ACME_EMAIL}"
      - "--certificatesresolvers.letsencrypt.acme.storage=/certs/acme.json"
      - "--accesslog=true"
      - "--accesslog.filepath=/logs/traefik-access.log"
      - "--accesslog.bufferingsize=100"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ${DATA_DIR:-/opt/0711-vault}/certs:/certs
      - ${DATA_DIR:-/opt/0711-vault}/logs:/logs
    networks:
      - vault-net

  # =========================================
  # DATABASES
  # =========================================
  postgres:
    image: pgvector/pgvector:pg16
    container_name: vault-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 2G
    networks:
      - vault-net

  redis:
    image: redis:7-alpine
    container_name: vault-redis
    restart: unless-stopped
    command: >
      redis-server
      --appendonly yes
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 768M
    networks:
      - vault-net

  neo4j:
    image: neo4j:5-community
    container_name: vault-neo4j
    restart: unless-stopped
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_server_memory_heap_initial__size: "512m"
      NEO4J_server_memory_heap_max__size: "1G"
      NEO4J_server_memory_pagecache_size: "512m"
    volumes:
      - neo4j-data:/data
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
    networks:
      - vault-net

  # =========================================
  # 0711-VAULT API
  # =========================================
  vault-api:
    image: ghcr.io/c-0711/0711-vault/vault-api:latest
    build:
      context: ./services/vault-api
      dockerfile: Dockerfile
    container_name: vault-api
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      REDIS_URL: redis://redis:6379
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD}
      JWT_SECRET: ${JWT_SECRET}
      VAULT_ENCRYPTION_KEY: ${VAULT_ENCRYPTION_KEY}
      VAULT_KEY_SALT: ${VAULT_KEY_SALT}
      # GPU auf H200 via WireGuard
      OLLAMA_HOST: ${OLLAMA_HOST:-http://10.71.1.2:11434}
      AI_SERVICE_URL: ${AI_SERVICE_URL:-http://10.71.1.2:8001}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-bge-m3:latest}
      VISION_MODEL: ${VISION_MODEL:-llama4:latest}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    labels:
      - "traefik.enable=true"
      # API
      - "traefik.http.routers.vault-api.rule=Host(`api.${DOMAIN}`)"
      - "traefik.http.routers.vault-api.entrypoints=websecure"
      - "traefik.http.routers.vault-api.tls.certresolver=letsencrypt"
      - "traefik.http.services.vault-api.loadbalancer.server.port=8000"
      # S3-kompatible API
      - "traefik.http.routers.vault-s3.rule=Host(`s3.${DOMAIN}`)"
      - "traefik.http.routers.vault-s3.entrypoints=websecure"
      - "traefik.http.routers.vault-s3.tls.certresolver=letsencrypt"
      - "traefik.http.services.vault-s3.loadbalancer.server.port=8000"
      # Rate Limiting
      - "traefik.http.middlewares.vault-ratelimit.ratelimit.average=100"
      - "traefik.http.middlewares.vault-ratelimit.ratelimit.burst=200"
      - "traefik.http.routers.vault-api.middlewares=vault-ratelimit"
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
    networks:
      - vault-net

  # =========================================
  # BACKGROUND WORKER (Document Processing)
  # =========================================
  worker:
    image: ghcr.io/c-0711/0711-vault/vault-api:latest
    build:
      context: ./services/vault-api
      dockerfile: Dockerfile
    container_name: vault-worker
    restart: unless-stopped
    command: python worker.py
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      REDIS_URL: redis://redis:6379
      VAULT_ENCRYPTION_KEY: ${VAULT_ENCRYPTION_KEY}
      VAULT_KEY_SALT: ${VAULT_KEY_SALT}
      # GPU Jobs gehen an H200
      OLLAMA_HOST: ${OLLAMA_HOST:-http://10.71.1.2:11434}
      AI_SERVICE_URL: ${AI_SERVICE_URL:-http://10.71.1.2:8001}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-bge-m3:latest}
      VISION_MODEL: ${VISION_MODEL:-llama4:latest}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 2G
    networks:
      - vault-net

  # =========================================
  # FRONTEND
  # =========================================
  frontend:
    image: ghcr.io/c-0711/0711-vault/frontend:latest
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: vault-frontend
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.vault-frontend.rule=Host(`${DOMAIN}`)"
      - "traefik.http.routers.vault-frontend.entrypoints=websecure"
      - "traefik.http.routers.vault-frontend.tls.certresolver=letsencrypt"
      - "traefik.http.services.vault-frontend.loadbalancer.server.port=80"
    networks:
      - vault-net

volumes:
  postgres-data:
    driver: local
  redis-data:
    driver: local
  neo4j-data:
    driver: local

networks:
  vault-net:
    driver: bridge
COMPOSE

log "docker-compose.yml erstellt"

# =============================================================================
# REPO KLONEN
# =============================================================================
header "0711-Vault Repository klonen"

REPO_DIR="$COMPOSE_DIR"

if [[ -d "$COMPOSE_DIR/.git" ]]; then
    info "Repo existiert bereits, aktualisiere..."
    cd "$COMPOSE_DIR" && git pull
else
    info "Klone Repository..."
    # Clone backend structure needed for building images
    git clone --depth 1 https://github.com/C-0711/0711-Vault.git "$COMPOSE_DIR/repo" 2>/dev/null || true

    # Falls privat, mit Token:
    # git clone --depth 1 https://${GITHUB_TOKEN}@github.com/C-0711/0711-Vault.git "$COMPOSE_DIR/repo"

    if [[ -d "$COMPOSE_DIR/repo" ]]; then
        # Link die benoetigten Verzeichnisse
        ln -sf "$COMPOSE_DIR/repo/backend/services" "$COMPOSE_DIR/services" 2>/dev/null || true
        ln -sf "$COMPOSE_DIR/repo/frontend" "$COMPOSE_DIR/frontend" 2>/dev/null || true
        log "Repository geklont"
    else
        warn "Repository konnte nicht geklont werden. Bitte manuell klonen:"
        warn "  git clone https://github.com/C-0711/0711-Vault.git $COMPOSE_DIR/repo"
    fi
fi

# =============================================================================
# SYMLINK .env
# =============================================================================
ln -sf "$SECRETS_FILE" "$COMPOSE_DIR/.env"
log ".env verlinkt"

# =============================================================================
# BACKUP CRON
# =============================================================================
header "Automatische Backups konfigurieren"

cat > "$DATA_DIR/backup.sh" <<'BACKUP'
#!/bin/bash
# 0711-Vault Taegliches Backup
set -euo pipefail

BACKUP_DIR="/opt/0711-vault/backups"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

# PostgreSQL Dump
docker exec vault-postgres pg_dump -U vault vault | gzip > "$BACKUP_DIR/postgres_${DATE}.sql.gz"

# Neo4j Dump (cold)
docker exec vault-neo4j neo4j-admin database dump neo4j --to-stdout 2>/dev/null | gzip > "$BACKUP_DIR/neo4j_${DATE}.dump.gz" || true

# Aufraumen: Backups aelter als $KEEP_DAYS Tage loeschen
find "$BACKUP_DIR" -name "*.gz" -mtime +$KEEP_DAYS -delete

echo "[$(date)] Backup fertig: postgres_${DATE}.sql.gz"
BACKUP

chmod +x "$DATA_DIR/backup.sh"

# Cron: Taeglich um 03:00
(crontab -l 2>/dev/null | grep -v "0711-vault/backup"; echo "0 3 * * * /opt/0711-vault/backup.sh >> /opt/0711-vault/logs/backup.log 2>&1") | crontab -

log "Taegliches Backup um 03:00 eingerichtet (14 Tage Retention)"

# =============================================================================
# SYSTEM TUNING fuer CCX23
# =============================================================================
header "System Tuning (CCX23: 4 vCPU, 16 GB RAM)"

cat > /etc/sysctl.d/99-vault-tuning.conf <<'SYSCTL'
# 0711-Vault System Tuning

# Netzwerk
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15

# Memory
vm.overcommit_memory = 1
vm.swappiness = 10

# File Descriptors
fs.file-max = 2097152
fs.inotify.max_user_watches = 524288
SYSCTL

sysctl --system > /dev/null 2>&1
log "System Tuning angewendet"

# =============================================================================
# STARTEN
# =============================================================================
header "0711-Vault starten"

cd "$COMPOSE_DIR"

info "Starte Docker Compose..."
docker compose --env-file "$SECRETS_FILE" up -d

# Warte auf Services
info "Warte auf Services..."
sleep 10

for svc in vault-postgres vault-redis vault-api; do
    if docker inspect --format='{{.State.Running}}' "$svc" 2>/dev/null | grep -q true; then
        log "$svc laeuft"
    else
        warn "$svc ist noch nicht bereit"
    fi
done

# =============================================================================
# OPENCLAW CLIENT CONFIGS GENERIEREN
# =============================================================================
header "OpenClaw Client-Konfigurationen generieren"

OPENCLAW_DIR="$DATA_DIR/openclaw-clients"
mkdir -p "$OPENCLAW_DIR"

for i in $(seq 1 10); do
    CLIENT_NAME="client-$(printf '%02d' $i)"
    CLIENT_API_KEY=$(openssl rand -hex 24)

    cat > "$OPENCLAW_DIR/$CLIENT_NAME.env" <<CLIENTENV
# =============================================================================
# OpenClaw Client Config: $CLIENT_NAME
# 0711-Vault Persoenlicher Assistent
# =============================================================================

# Vault API
VAULT_API_URL=https://api.${DOMAIN}
VAULT_S3_URL=https://s3.${DOMAIN}
VAULT_API_KEY=${CLIENT_API_KEY}

# OpenClaw Settings
OPENCLAW_ASSISTANT_NAME=0711-Vault Assistent
OPENCLAW_MODEL=claude-sonnet-4-6
OPENCLAW_MCP_SERVERS=vault-s3,vault-docs

# S3 Credentials (fuer diesen Client)
AWS_ACCESS_KEY_ID=${CLIENT_NAME}
AWS_SECRET_ACCESS_KEY=${CLIENT_API_KEY}
AWS_ENDPOINT_URL=https://s3.${DOMAIN}
AWS_DEFAULT_REGION=eu-central-1

# Vault Features
VAULT_FEATURES_OCR=true
VAULT_FEATURES_EMBED=true
VAULT_FEATURES_EXTRACT=true
VAULT_FEATURES_CHAT=true
CLIENTENV

    chmod 600 "$OPENCLAW_DIR/$CLIENT_NAME.env"
done

log "10 OpenClaw Client-Konfigurationen erstellt in $OPENCLAW_DIR/"

# =============================================================================
# OPENCLAW MCP SERVER CONFIG
# =============================================================================

cat > "$OPENCLAW_DIR/mcp-config.json" <<MCPCONF
{
  "mcpServers": {
    "vault-s3": {
      "type": "http",
      "url": "https://api.${DOMAIN}/mcp/s3",
      "description": "0711-Vault S3 Document Storage",
      "auth": {
        "type": "bearer",
        "token_env": "VAULT_API_KEY"
      },
      "tools": [
        "s3_list_buckets",
        "s3_list_objects",
        "s3_get_object",
        "s3_put_object",
        "s3_delete_object"
      ]
    },
    "vault-docs": {
      "type": "http",
      "url": "https://api.${DOMAIN}/mcp/docs",
      "description": "0711-Vault Document Intelligence",
      "auth": {
        "type": "bearer",
        "token_env": "VAULT_API_KEY"
      },
      "tools": [
        "vault_upload_document",
        "vault_search_documents",
        "vault_get_document",
        "vault_ask_document",
        "vault_list_documents",
        "vault_process_document"
      ]
    },
    "vault-assistant": {
      "type": "http",
      "url": "https://api.${DOMAIN}/mcp/assistant",
      "description": "0711-Vault Personal AI Assistant",
      "auth": {
        "type": "bearer",
        "token_env": "VAULT_API_KEY"
      },
      "tools": [
        "assistant_chat",
        "assistant_summarize",
        "assistant_extract_data",
        "assistant_translate"
      ]
    }
  }
}
MCPCONF

log "OpenClaw MCP Server Config erstellt"

# =============================================================================
# ZUSAMMENFASSUNG
# =============================================================================
header "Setup abgeschlossen!"

cat <<SUMMARY

${GREEN}=== 0711-Vault Deployment ======================================${NC}

  Server:       Hetzner CCX23 (4 vCPU, 16 GB RAM, 160 GB SSD)
  IP:           $PUBLIC_IP
  Domain:       $DOMAIN

${GREEN}=== Services ===================================================${NC}

  Frontend:     https://$DOMAIN
  API:          https://api.$DOMAIN
  S3:           https://s3.$DOMAIN

${GREEN}=== Infrastruktur ==============================================${NC}

  PostgreSQL:   pgvector/pg16     (4 GB RAM Limit)
  Redis:        7-alpine          (512 MB)
  Neo4j:        5-community       (2 GB RAM Limit)
  Traefik:      v3.0              (Let's Encrypt TLS)

${GREEN}=== GPU Pipeline (H200) ========================================${NC}

  WireGuard:    10.71.1.1 (Hetzner) <-> 10.71.1.2 (H200)
  Ollama:       $OLLAMA_HOST
  Models:       bge-m3, llama4

${GREEN}=== OpenClaw ===================================================${NC}

  Clients:      10 Konfigurationen in $OPENCLAW_DIR/
  MCP Config:   $OPENCLAW_DIR/mcp-config.json

${YELLOW}=== DNS Records erstellen! =====================================${NC}

  A    $DOMAIN          ->  $PUBLIC_IP
  A    api.$DOMAIN      ->  $PUBLIC_IP
  A    s3.$DOMAIN       ->  $PUBLIC_IP

${YELLOW}=== WireGuard H200 Setup ======================================${NC}

  Auf der H200 ausfuehren:

  1. WireGuard installieren:
     apt install wireguard

  2. Keys generieren:
     wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key

  3. /etc/wireguard/wg-vault.conf erstellen:

     [Interface]
     Address    = 10.71.1.2/24
     PrivateKey = <H200_PRIVATE_KEY>

     [Peer]
     PublicKey  = $SERVER_PUBKEY
     Endpoint   = $PUBLIC_IP:51820
     AllowedIPs = 10.71.1.0/24
     PersistentKeepalive = 25

  4. Aktivieren:
     systemctl enable --now wg-quick@wg-vault

  5. Auf Hetzner den H200-Peer in /etc/wireguard/wg-h200.conf eintragen
     und: systemctl enable --now wg-quick@wg-h200

${GREEN}=== Nuetzliche Befehle =========================================${NC}

  docker compose -f $COMPOSE_DIR/docker-compose.yml logs -f
  docker compose -f $COMPOSE_DIR/docker-compose.yml ps
  docker compose -f $COMPOSE_DIR/docker-compose.yml restart vault-api
  $DATA_DIR/backup.sh                    # Manuelles Backup

${RED}=== WICHTIG ===================================================${NC}

  Secrets:      $SECRETS_FILE
  WireGuard:    $WG_DIR/
  Backups:      $DATA_DIR/backups/

  DIESE DATEIEN SICHER AUFBEWAHREN!

${NC}
SUMMARY

log "Fertig! DNS Records erstellen, dann ist alles live."
