#!/bin/bash
# =============================================================================
# OpenClaw Vault Instances: Denis, Alex, C
# Deploy Redis, Neo4j, Vault-API, Frontend per Tenant auf H200V
# Voraussetzung: Postgres + MCP-Server + Netzwerke bereits deployed
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
# TENANT-KONFIGURATION
# =============================================================================

declare -A TENANT_BASE_PORT
TENANT_BASE_PORT[denis]=9900
TENANT_BASE_PORT[alex]=10000
TENANT_BASE_PORT[c]=10100

declare -A TENANT_SUBNET
TENANT_SUBNET[denis]="10.200.11.0/24"
TENANT_SUBNET[alex]="10.200.12.0/24"
TENANT_SUBNET[c]="10.200.13.0/24"

declare -A TENANT_NEO4J_PW
TENANT_NEO4J_PW[denis]="denis0711"
TENANT_NEO4J_PW[alex]="alex0711"
TENANT_NEO4J_PW[c]="c0711"

declare -A TENANT_JWT
TENANT_JWT[denis]="denis-jwt-secret-0711-2026"
TENANT_JWT[alex]="alex-jwt-secret-0711-2026"
TENANT_JWT[c]="c-jwt-secret-0711-2026"

declare -A TENANT_DB_USER
TENANT_DB_USER[denis]="vault_denis"
TENANT_DB_USER[alex]="vault_alex"
TENANT_DB_USER[c]="vault_c"

declare -A TENANT_DB_PW
TENANT_DB_PW[denis]="vault_denis_2026"
TENANT_DB_PW[alex]="vault_alex_2026"
TENANT_DB_PW[c]="vault_c_2026"

declare -A TENANT_DB_NAME
TENANT_DB_NAME[denis]="vault_denis"
TENANT_DB_NAME[alex]="vault_alex"
TENANT_DB_NAME[c]="vault_c"

TENANTS=(denis alex c)

# =============================================================================
# PREFLIGHT CHECK
# =============================================================================
header "Preflight Check"

for TENANT in "${TENANTS[@]}"; do
    NETWORK="vault-${TENANT}-network"
    PG_CONTAINER="vault-${TENANT}-postgres"

    if ! docker network inspect "$NETWORK" &>/dev/null; then
        fail "Netzwerk $NETWORK existiert nicht!"
    fi
    log "Netzwerk $NETWORK vorhanden"

    if ! docker inspect "$PG_CONTAINER" &>/dev/null; then
        fail "Container $PG_CONTAINER existiert nicht!"
    fi
    log "Container $PG_CONTAINER vorhanden"
done

# =============================================================================
# STEP 1: Fix Postgres-Aliases
# =============================================================================
header "Step 1: Postgres DNS-Aliases fixen"

for TENANT in "${TENANTS[@]}"; do
    NETWORK="vault-${TENANT}-network"
    PG_CONTAINER="vault-${TENANT}-postgres"

    info "Fixe Aliases fuer $PG_CONTAINER auf $NETWORK..."

    # Disconnect und reconnect mit Aliases
    docker network disconnect "$NETWORK" "$PG_CONTAINER" 2>/dev/null || true
    docker network connect \
        --alias "$PG_CONTAINER" --alias postgres \
        "$NETWORK" "$PG_CONTAINER"

    log "$PG_CONTAINER hat jetzt Alias 'postgres' auf $NETWORK"
done

# =============================================================================
# STEP 2: Redis erstellen (3x)
# =============================================================================
header "Step 2: Redis Container erstellen"

for TENANT in "${TENANTS[@]}"; do
    BASE=${TENANT_BASE_PORT[$TENANT]}
    REDIS_PORT=$((BASE + 1))
    CONTAINER="vault-${TENANT}-redis"
    NETWORK="vault-${TENANT}-network"
    VOLUME="0711-vault-${TENANT}_vault-${TENANT}-redis"

    if docker inspect "$CONTAINER" &>/dev/null; then
        warn "$CONTAINER existiert bereits — ueberspringe"
        continue
    fi

    info "Erstelle $CONTAINER (Port $REDIS_PORT)..."

    docker run -d \
        --name "$CONTAINER" \
        --network "$NETWORK" \
        --network-alias "$CONTAINER" --network-alias redis \
        --restart unless-stopped \
        --health-cmd "redis-cli ping" --health-interval 5s --health-timeout 5s --health-retries 5 \
        -v "${VOLUME}:/data" \
        -p "${REDIS_PORT}:6379" \
        redis:7-alpine redis-server --appendonly yes

    log "$CONTAINER erstellt und laeuft auf Port $REDIS_PORT"
done

# =============================================================================
# STEP 3: Neo4j erstellen (3x)
# =============================================================================
header "Step 3: Neo4j Container erstellen"

for TENANT in "${TENANTS[@]}"; do
    BASE=${TENANT_BASE_PORT[$TENANT]}
    NEO4J_UI_PORT=$((BASE + 2))
    NEO4J_BOLT_PORT=$((BASE + 3))
    NEO4J_PW=${TENANT_NEO4J_PW[$TENANT]}
    CONTAINER="vault-${TENANT}-neo4j"
    NETWORK="vault-${TENANT}-network"
    VOLUME="0711-vault-${TENANT}_vault-${TENANT}-neo4j"

    if docker inspect "$CONTAINER" &>/dev/null; then
        warn "$CONTAINER existiert bereits — ueberspringe"
        continue
    fi

    info "Erstelle $CONTAINER (UI: $NEO4J_UI_PORT, Bolt: $NEO4J_BOLT_PORT)..."

    docker run -d \
        --name "$CONTAINER" \
        --network "$NETWORK" \
        --network-alias "$CONTAINER" --network-alias neo4j \
        --restart unless-stopped \
        -e "NEO4J_AUTH=neo4j/${NEO4J_PW}" \
        -e 'NEO4J_PLUGINS=["apoc"]' \
        -v "${VOLUME}:/data" \
        -p "${NEO4J_UI_PORT}:7474" -p "${NEO4J_BOLT_PORT}:7687" \
        neo4j:5

    log "$CONTAINER erstellt (UI: $NEO4J_UI_PORT, Bolt: $NEO4J_BOLT_PORT)"
done

# =============================================================================
# STEP 4: Vault-API erstellen (3x)
# =============================================================================
header "Step 4: Vault-API Container erstellen"

# Pruefe ob vault-api:v4 Image existiert
if ! docker image inspect vault-api:v4 &>/dev/null; then
    fail "Docker Image vault-api:v4 nicht gefunden! Bitte zuerst bauen."
fi

for TENANT in "${TENANTS[@]}"; do
    BASE=${TENANT_BASE_PORT[$TENANT]}
    API_PORT=$((BASE + 6))
    CONTAINER="vault-${TENANT}-api"
    NETWORK="vault-${TENANT}-network"

    DB_USER=${TENANT_DB_USER[$TENANT]}
    DB_PW=${TENANT_DB_PW[$TENANT]}
    DB_NAME=${TENANT_DB_NAME[$TENANT]}
    JWT=${TENANT_JWT[$TENANT]}

    if docker inspect "$CONTAINER" &>/dev/null; then
        warn "$CONTAINER existiert bereits — ueberspringe"
        continue
    fi

    info "Erstelle $CONTAINER (Port $API_PORT)..."

    docker run -d \
        --name "$CONTAINER" \
        --network "$NETWORK" \
        --network-alias "$CONTAINER" --network-alias vault-api \
        --restart unless-stopped \
        --add-host host.docker.internal:host-gateway \
        -e "DATABASE_URL=postgresql://${DB_USER}:${DB_PW}@postgres:5432/${DB_NAME}" \
        -e "REDIS_URL=redis://redis:6379" \
        -e "OLLAMA_HOST=http://host.docker.internal:11434" \
        -e "JWT_SECRET=${JWT}" \
        -e "TENANT_ID=${TENANT}" \
        -p "${API_PORT}:8000" \
        vault-api:v4

    log "$CONTAINER erstellt und laeuft auf Port $API_PORT"
done

# =============================================================================
# STEP 5: Frontend erstellen (3x)
# =============================================================================
header "Step 5: Frontend Container erstellen"

# Pruefe ob backend-frontend Image existiert
if ! docker image inspect backend-frontend &>/dev/null; then
    fail "Docker Image backend-frontend nicht gefunden! Bitte zuerst bauen."
fi

for TENANT in "${TENANTS[@]}"; do
    BASE=${TENANT_BASE_PORT[$TENANT]}
    FE_PORT=$((BASE + 8))
    CONTAINER="vault-${TENANT}-frontend"
    NETWORK="vault-${TENANT}-network"

    if docker inspect "$CONTAINER" &>/dev/null; then
        warn "$CONTAINER existiert bereits — ueberspringe"
        continue
    fi

    info "Erstelle $CONTAINER (Port $FE_PORT)..."

    docker run -d \
        --name "$CONTAINER" \
        --network "$NETWORK" \
        --restart unless-stopped \
        -p "${FE_PORT}:80" \
        backend-frontend

    # Patch API-URL im JS-Bundle fuer die Tenant-Subdomain
    info "Patche API-URL fuer ${TENANT}.vault.0711.io..."
    sleep 2  # Warte bis Container gestartet ist

    docker exec "$CONTAINER" sh -c \
        "sed -i 's|https://api-vault.0711.io|https://${TENANT}.vault.0711.io|g' /usr/share/nginx/html/assets/index-*.js" \
        2>/dev/null || warn "JS-Bundle Patch fehlgeschlagen fuer $CONTAINER (evtl. nicht noetig)"

    log "$CONTAINER erstellt auf Port $FE_PORT"
done

# =============================================================================
# ZUSAMMENFASSUNG
# =============================================================================
header "Deployment abgeschlossen!"

echo -e "${GREEN}=== Neue Container (12 total) ===================================${NC}"
echo ""

for TENANT in "${TENANTS[@]}"; do
    BASE=${TENANT_BASE_PORT[$TENANT]}
    echo -e "  ${BLUE}${TENANT}:${NC}"
    echo "    Redis:    vault-${TENANT}-redis     Port $((BASE + 1))"
    echo "    Neo4j:    vault-${TENANT}-neo4j     Port $((BASE + 2)) (UI), $((BASE + 3)) (Bolt)"
    echo "    API:      vault-${TENANT}-api       Port $((BASE + 6))"
    echo "    Frontend: vault-${TENANT}-frontend  Port $((BASE + 8))"
    echo ""
done

echo -e "${YELLOW}=== Naechste Schritte ===========================================${NC}"
echo ""
echo "  1. Warte ~30s bis alle Services bereit sind"
echo "  2. Verifiziere mit: bash verify-instances.sh"
echo "  3. Registriere User mit: python3 admin-register-users.py"
echo "  4. DNS-Eintraege erstellen:"
echo "       denis.vault.0711.io  -> H200V:9908"
echo "       alex.vault.0711.io   -> H200V:10008"
echo "       c.vault.0711.io      -> H200V:10108"
echo ""
