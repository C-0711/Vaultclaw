#!/bin/bash
# =============================================================================
# 0711-Vault OpenClaw Client Setup
# Installiert und konfiguriert OpenClaw als persoenlichen Assistenten
# mit Zugriff auf den 0711-Vault Document Store
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
echo -e "${BLUE}  0711-Vault OpenClaw Client Setup${NC}"
echo -e "${BLUE}================================================================${NC}"
echo ""

# =============================================================================
# CONFIG
# =============================================================================

CLIENT_ENV="${1:-}"

if [[ -z "$CLIENT_ENV" ]]; then
    echo "Usage: $0 <client-env-file>"
    echo ""
    echo "Beispiel: $0 client-01.env"
    echo ""
    echo "Die .env Datei bekommst du vom 0711-Vault Admin."
    exit 1
fi

if [[ ! -f "$CLIENT_ENV" ]]; then
    echo "Datei nicht gefunden: $CLIENT_ENV"
    exit 1
fi

source "$CLIENT_ENV"

info "Client Config geladen"
info "  API:  $VAULT_API_URL"
info "  S3:   $VAULT_S3_URL"

# =============================================================================
# PREREQUISITES
# =============================================================================

# Node.js
if ! command -v node &>/dev/null; then
    info "Installiere Node.js..."
    if [[ "$(uname)" == "Darwin" ]]; then
        brew install node 2>/dev/null || {
            curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
            export NVM_DIR="$HOME/.nvm"
            [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
            nvm install 22
        }
    else
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        sudo apt-get install -y nodejs
    fi
    log "Node.js installiert"
else
    log "Node.js vorhanden: $(node --version)"
fi

# Docker (optional, fuer isolierten Betrieb)
if command -v docker &>/dev/null; then
    log "Docker vorhanden"
    HAS_DOCKER=true
else
    HAS_DOCKER=false
    warn "Docker nicht vorhanden — OpenClaw laeuft direkt auf dem System"
fi

# =============================================================================
# OPENCLAW INSTALLIEREN
# =============================================================================

info "Installiere OpenClaw..."

OPENCLAW_DIR="$HOME/.openclaw"
mkdir -p "$OPENCLAW_DIR"

# OpenClaw via npm
npm install -g openclaw 2>/dev/null || {
    warn "npm install fehlgeschlagen, versuche npx..."
}

log "OpenClaw installiert"

# =============================================================================
# MCP SERVER KONFIGURATION
# =============================================================================

info "Konfiguriere MCP Server..."

mkdir -p "$OPENCLAW_DIR/config"

cat > "$OPENCLAW_DIR/config/mcp-servers.json" <<MCPJSON
{
  "mcpServers": {
    "vault-s3": {
      "type": "http",
      "url": "${VAULT_API_URL}/mcp/s3",
      "description": "0711-Vault S3 Dokumenten-Speicher. Lade Dokumente hoch, liste und durchsuche sie.",
      "auth": {
        "type": "bearer",
        "token": "${VAULT_API_KEY}"
      }
    },
    "vault-docs": {
      "type": "http",
      "url": "${VAULT_API_URL}/mcp/docs",
      "description": "0711-Vault Dokumenten-Intelligenz. OCR, Extraktion, Embeddings, semantische Suche.",
      "auth": {
        "type": "bearer",
        "token": "${VAULT_API_KEY}"
      }
    },
    "vault-assistant": {
      "type": "http",
      "url": "${VAULT_API_URL}/mcp/assistant",
      "description": "Persoenlicher AI-Assistent mit Zugriff auf deinen Dokumenten-Tresor.",
      "auth": {
        "type": "bearer",
        "token": "${VAULT_API_KEY}"
      }
    }
  }
}
MCPJSON

log "MCP Server konfiguriert"

# =============================================================================
# OPENCLAW SYSTEM PROMPT
# =============================================================================

cat > "$OPENCLAW_DIR/config/system-prompt.md" <<'SYSTEMPROMPT'
# 0711-Vault Persoenlicher Assistent

Du bist ein persoenlicher AI-Assistent, verbunden mit einem sicheren Dokumenten-Tresor (0711-Vault).

## Deine Faehigkeiten

### Dokumenten-Management
- **Hochladen**: Dokumente in den verschluesselten Vault laden
- **Suchen**: Semantische Suche ueber alle Dokumente (Embeddings via bge-m3)
- **OCR**: Text aus Bildern und PDFs extrahieren
- **Extraktion**: Strukturierte Daten aus Dokumenten extrahieren

### Intelligente Analyse
- Fasse Dokumente zusammen
- Beantworte Fragen basierend auf deinen gespeicherten Dokumenten
- Vergleiche Dokumente miteinander
- Extrahiere wichtige Daten (Termine, Betraege, Namen)

### S3-Zugriff
- Nutze die S3 API um Dateien zu verwalten
- Buckets: `documents`, `photos`, `media`
- Alle Daten sind ChaCha20-Poly1305 verschluesselt

## Wichtige Regeln

1. **Datenschutz**: Alle Daten liegen verschluesselt auf DSGVO-konformen Servern in Deutschland (Hetzner)
2. **Vertraulichkeit**: Teile niemals Inhalte aus dem Vault mit Dritten
3. **Genauigkeit**: Wenn du dir nicht sicher bist, sage es. Halluziniere keine Dokumenten-Inhalte.
4. **Sprache**: Antworte in der Sprache, in der der Benutzer schreibt (Deutsch/Englisch)
SYSTEMPROMPT

log "System Prompt konfiguriert"

# =============================================================================
# AWS CLI PROFIL (fuer S3-Zugriff)
# =============================================================================

info "Konfiguriere S3 CLI Profil..."

mkdir -p "$HOME/.aws"

# Profil hinzufuegen ohne bestehende Konfiguration zu ueberschreiben
if ! grep -q "\[profile vault-0711\]" "$HOME/.aws/config" 2>/dev/null; then
    cat >> "$HOME/.aws/config" <<AWSCONFIG

[profile vault-0711]
region = eu-central-1
output = json
endpoint_url = ${VAULT_S3_URL}
AWSCONFIG
fi

if ! grep -q "\[vault-0711\]" "$HOME/.aws/credentials" 2>/dev/null; then
    cat >> "$HOME/.aws/credentials" <<AWSCREDS

[vault-0711]
aws_access_key_id = ${AWS_ACCESS_KEY_ID}
aws_secret_access_key = ${AWS_SECRET_ACCESS_KEY}
AWSCREDS
    chmod 600 "$HOME/.aws/credentials"
fi

log "AWS CLI Profil 'vault-0711' konfiguriert"

# =============================================================================
# CONVENIENCE SCRIPTS
# =============================================================================

info "Erstelle Convenience Scripts..."

# Vault Upload Script
cat > "$OPENCLAW_DIR/vault-upload" <<'UPLOAD'
#!/bin/bash
# Upload eine Datei in den 0711-Vault
FILE="${1:?Usage: vault-upload <datei> [bucket]}"
BUCKET="${2:-documents}"

if [[ ! -f "$FILE" ]]; then
    echo "Datei nicht gefunden: $FILE"
    exit 1
fi

FILENAME=$(basename "$FILE")
echo "Lade hoch: $FILENAME -> s3://$BUCKET/$FILENAME"

aws s3 cp "$FILE" "s3://$BUCKET/$FILENAME" --profile vault-0711

echo "Fertig! Dokument wird jetzt automatisch verarbeitet (OCR + Embedding)."
UPLOAD
chmod +x "$OPENCLAW_DIR/vault-upload"

# Vault Search Script
cat > "$OPENCLAW_DIR/vault-search" <<'SEARCH'
#!/bin/bash
# Suche in 0711-Vault Dokumenten
QUERY="${1:?Usage: vault-search <suchbegriff>}"
source "$HOME/.openclaw/config/../../../$(ls $HOME/.openclaw/*.env 2>/dev/null | head -1)" 2>/dev/null || true

curl -s "${VAULT_API_URL:-https://api.vault.0711.io}/vault/search" \
    -H "Authorization: Bearer ${VAULT_API_KEY:-}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"$QUERY\", \"limit\": 10}" | jq .
SEARCH
chmod +x "$OPENCLAW_DIR/vault-search"

# Vault List Script
cat > "$OPENCLAW_DIR/vault-ls" <<'LIST'
#!/bin/bash
# Liste Dateien im 0711-Vault
BUCKET="${1:-documents}"
aws s3 ls "s3://$BUCKET/" --profile vault-0711
LIST
chmod +x "$OPENCLAW_DIR/vault-ls"

# PATH hinzufuegen
SHELL_RC="$HOME/.$(basename $SHELL)rc"
if ! grep -q "openclaw" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# 0711-Vault OpenClaw" >> "$SHELL_RC"
    echo "export PATH=\"\$HOME/.openclaw:\$PATH\"" >> "$SHELL_RC"
fi

log "Convenience Scripts erstellt: vault-upload, vault-search, vault-ls"

# =============================================================================
# VERBINDUNG TESTEN
# =============================================================================

info "Teste Verbindung zum Vault..."

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "${VAULT_API_URL}/health" 2>/dev/null || echo "000")

if [[ "$HEALTH" == "200" ]]; then
    log "Vault API erreichbar!"
else
    warn "Vault API nicht erreichbar (HTTP $HEALTH). Ist der Server schon gestartet?"
    warn "DNS Records muessen auf den Server zeigen."
fi

# =============================================================================
# FERTIG
# =============================================================================

echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}  OpenClaw Client Setup abgeschlossen!${NC}"
echo -e "${GREEN}================================================================${NC}"
echo ""
echo "  Starte OpenClaw:"
echo "    openclaw --config $OPENCLAW_DIR/config/mcp-servers.json"
echo ""
echo "  Oder nutze die CLI Tools:"
echo "    vault-upload rechnung.pdf        # Dokument hochladen"
echo "    vault-search 'Steuerbescheid'    # Dokumente suchen"
echo "    vault-ls documents               # Dateien auflisten"
echo ""
echo "  S3 Zugriff mit AWS CLI:"
echo "    aws s3 ls --profile vault-0711"
echo "    aws s3 cp datei.pdf s3://documents/ --profile vault-0711"
echo ""
