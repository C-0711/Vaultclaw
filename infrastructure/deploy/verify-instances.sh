#!/bin/bash
# =============================================================================
# Verify OpenClaw Vault Instances: Denis, Alex, C
# Prueft ob alle Container laufen und Services erreichbar sind
# =============================================================================
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0

check() {
    local desc="$1"
    local result="$2"
    if [[ "$result" == "true" ]] || [[ "$result" == "ok" ]]; then
        echo -e "  ${GREEN}[PASS]${NC} $desc"
        ((PASS++))
    else
        echo -e "  ${RED}[FAIL]${NC} $desc"
        ((FAIL++))
    fi
}

header() {
    echo ""
    echo -e "${BLUE}--- $1 ---${NC}"
}

echo ""
echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}  OpenClaw Vault Instances — Verification${NC}"
echo -e "${BLUE}================================================================${NC}"

# =============================================================================
# 1. Container Running Check
# =============================================================================
header "Container Status"

CONTAINERS=(
    "vault-denis-postgres"
    "vault-denis-redis"
    "vault-denis-neo4j"
    "vault-denis-api"
    "vault-denis-frontend"
    "denis-mcp-server"
    "vault-alex-postgres"
    "vault-alex-redis"
    "vault-alex-neo4j"
    "vault-alex-api"
    "vault-alex-frontend"
    "alex-mcp-server"
    "vault-c-postgres"
    "vault-c-redis"
    "vault-c-neo4j"
    "vault-c-api"
    "vault-c-frontend"
    "c-mcp-server"
)

for CONTAINER in "${CONTAINERS[@]}"; do
    RUNNING=$(docker inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo "false")
    check "$CONTAINER running" "$RUNNING"
done

# =============================================================================
# 2. Port Connectivity
# =============================================================================
header "Port Erreichbarkeit"

declare -A PORT_DESC
PORT_DESC[9900]="Denis Postgres"
PORT_DESC[9901]="Denis Redis"
PORT_DESC[9902]="Denis Neo4j UI"
PORT_DESC[9906]="Denis vault-api"
PORT_DESC[9908]="Denis Frontend"
PORT_DESC[9940]="Denis MCP"
PORT_DESC[10000]="Alex Postgres"
PORT_DESC[10001]="Alex Redis"
PORT_DESC[10002]="Alex Neo4j UI"
PORT_DESC[10006]="Alex vault-api"
PORT_DESC[10008]="Alex Frontend"
PORT_DESC[10040]="Alex MCP"
PORT_DESC[10100]="C Postgres"
PORT_DESC[10101]="C Redis"
PORT_DESC[10102]="C Neo4j UI"
PORT_DESC[10106]="C vault-api"
PORT_DESC[10108]="C Frontend"
PORT_DESC[10140]="C MCP"

for PORT in 9900 9901 9902 9906 9908 9940 10000 10001 10002 10006 10008 10040 10100 10101 10102 10106 10108 10140; do
    DESC="${PORT_DESC[$PORT]}"
    if timeout 2 bash -c "echo >/dev/tcp/localhost/$PORT" 2>/dev/null; then
        check "Port $PORT ($DESC)" "ok"
    else
        check "Port $PORT ($DESC)" "fail"
    fi
done

# =============================================================================
# 3. Vault-API Health Checks
# =============================================================================
header "Vault-API Health"

for PORT in 9906 10006 10106; do
    TENANT=""
    case $PORT in
        9906) TENANT="Denis" ;;
        10006) TENANT="Alex" ;;
        10106) TENANT="C" ;;
    esac

    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "http://localhost:${PORT}/health" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        check "$TENANT vault-api health (:$PORT)" "ok"
    else
        check "$TENANT vault-api health (:$PORT) — HTTP $HTTP_CODE" "fail"
    fi
done

# =============================================================================
# 4. Frontend HTTP Check
# =============================================================================
header "Frontend HTTP"

for PORT in 9908 10008 10108; do
    TENANT=""
    case $PORT in
        9908) TENANT="Denis" ;;
        10008) TENANT="Alex" ;;
        10108) TENANT="C" ;;
    esac

    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "http://localhost:${PORT}/" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]] || [[ "$HTTP_CODE" == "304" ]]; then
        check "$TENANT Frontend (:$PORT) — HTTP $HTTP_CODE" "ok"
    else
        check "$TENANT Frontend (:$PORT) — HTTP $HTTP_CODE" "fail"
    fi
done

# =============================================================================
# 5. Redis Ping
# =============================================================================
header "Redis Ping"

for TENANT in denis alex c; do
    CONTAINER="vault-${TENANT}-redis"
    PONG=$(docker exec "$CONTAINER" redis-cli ping 2>/dev/null || echo "")
    if [[ "$PONG" == "PONG" ]]; then
        check "$CONTAINER ping" "ok"
    else
        check "$CONTAINER ping" "fail"
    fi
done

# =============================================================================
# 6. Neo4j Status
# =============================================================================
header "Neo4j Browser"

for PORT in 9902 10002 10102; do
    TENANT=""
    case $PORT in
        9902) TENANT="Denis" ;;
        10002) TENANT="Alex" ;;
        10102) TENANT="C" ;;
    esac

    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "http://localhost:${PORT}/" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]] || [[ "$HTTP_CODE" == "302" ]] || [[ "$HTTP_CODE" == "303" ]]; then
        check "$TENANT Neo4j UI (:$PORT) — HTTP $HTTP_CODE" "ok"
    else
        check "$TENANT Neo4j UI (:$PORT) — HTTP $HTTP_CODE" "fail"
    fi
done

# =============================================================================
# 7. Netzwerk-Alias Check (postgres erreichbar im Container-Netzwerk)
# =============================================================================
header "Postgres DNS-Alias (intern)"

for TENANT in denis alex c; do
    API_CONTAINER="vault-${TENANT}-api"
    # Pruefe ob der API-Container postgres im Netzwerk aufloesen kann
    if docker inspect "$API_CONTAINER" &>/dev/null; then
        RESOLVED=$(docker exec "$API_CONTAINER" sh -c "getent hosts postgres 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "")
        if [[ -n "$RESOLVED" ]]; then
            check "$TENANT: 'postgres' resolves to $RESOLVED" "ok"
        else
            check "$TENANT: 'postgres' DNS alias nicht aufloesbar" "fail"
        fi
    else
        check "$TENANT: API Container nicht vorhanden (kann Alias nicht pruefen)" "fail"
    fi
done

# =============================================================================
# ZUSAMMENFASSUNG
# =============================================================================
echo ""
echo -e "${BLUE}================================================================${NC}"
TOTAL=$((PASS + FAIL))
if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}  ALLE CHECKS BESTANDEN: $PASS/$TOTAL${NC}"
else
    echo -e "${YELLOW}  Ergebnis: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC} (von $TOTAL)${NC}"
fi
echo -e "${BLUE}================================================================${NC}"
echo ""

exit $FAIL
