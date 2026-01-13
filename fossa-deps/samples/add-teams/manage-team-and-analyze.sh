#!/usr/bin/env bash
set -euo pipefail

# FOSSA Team Management and Analysis Script
# This script queries existing teams, creates a team if needed, and runs FOSSA analysis

# Configuration
FOSSA_ENDPOINT="${FOSSA_ENDPOINT:-https://app.fossa.com}"
FOSSA_API_KEY="${FOSSA_API_KEY:-}"
TEAM_NAME="${1:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

usage() {
    cat <<EOF
Usage: $0 <team_name> [options]

Manages FOSSA teams and runs analysis with team assignment.

Arguments:
  team_name           Name of the team to assign the project to

Environment Variables:
  FOSSA_API_KEY      FOSSA API key (required)
  FOSSA_ENDPOINT     FOSSA endpoint URL (default: https://app.fossa.com)

Options:
  -h, --help         Show this help message
  --create-only      Only create team, don't run analysis
  --analyze-only     Only run analysis (assumes team exists)
  --debug            Enable debug output

Examples:
  # Basic usage
  FOSSA_API_KEY=xxx $0 "Engineering Team"

  # With custom endpoint
  FOSSA_ENDPOINT=https://fossa.company.com FOSSA_API_KEY=xxx $0 "Platform Team"

  # Only create team
  FOSSA_API_KEY=xxx $0 "New Team" --create-only

  # Only run analysis with existing team
  FOSSA_API_KEY=xxx $0 "Existing Team" --analyze-only
EOF
    exit 0
}

# Parse arguments
CREATE_ONLY=false
ANALYZE_ONLY=false
DEBUG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            ;;
        --create-only)
            CREATE_ONLY=true
            shift
            ;;
        --analyze-only)
            ANALYZE_ONLY=true
            shift
            ;;
        --debug)
            DEBUG=true
            set -x
            shift
            ;;
        *)
            if [ -z "$TEAM_NAME" ]; then
                TEAM_NAME="$1"
            fi
            shift
            ;;
    esac
done

# Validate inputs
if [ -z "$TEAM_NAME" ]; then
    log_error "Team name is required"
    usage
fi

if [ -z "$FOSSA_API_KEY" ]; then
    log_error "FOSSA_API_KEY environment variable is not set"
    exit 1
fi

# Check dependencies
if ! command -v curl &> /dev/null; then
    log_error "curl is required but not installed"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log_error "jq is required but not installed"
    exit 1
fi

if [ "$ANALYZE_ONLY" = false ] && ! command -v fossa &> /dev/null; then
    log_warn "fossa CLI is not installed. Will attempt to install..."
    curl -H 'Cache-Control: no-cache' https://raw.githubusercontent.com/fossas/fossa-cli/master/install-latest.sh | bash
    if ! command -v fossa &> /dev/null; then
        log_error "Failed to install fossa CLI"
        exit 1
    fi
    log_info "fossa CLI installed successfully"
fi

# Function to check if team exists
check_team_exists() {
    local team_name="$1"

    log_info "Checking if team '$team_name' exists..."

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -H "Authorization: Bearer $FOSSA_API_KEY" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json" \
        "$FOSSA_ENDPOINT/api/teams")

    local http_code
    http_code=$(echo "$response" | tail -n 1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" != "200" ]; then
        log_error "Failed to fetch teams (HTTP $http_code)"
        if [ "$DEBUG" = true ]; then
            echo "Response: $body"
        fi
        exit 1
    fi

    local team_exists
    team_exists=$(echo "$body" | jq -r --arg name "$team_name" '.[] | select(.name == $name) | .name' | head -n 1)

    if [ -n "$team_exists" ]; then
        log_info "Team '$team_name' already exists"
        return 0
    else
        log_warn "Team '$team_name' does not exist"
        return 1
    fi
}

# Function to create team
create_team() {
    local team_name="$1"

    log_info "Creating team '$team_name'..."

    local response
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer $FOSSA_API_KEY" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json" \
        -d "{\"name\":\"$team_name\",\"autoAddUsers\":false}" \
        "$FOSSA_ENDPOINT/api/teams")

    local http_code
    http_code=$(echo "$response" | tail -n 1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" != "200" ] && [ "$http_code" != "201" ]; then
        log_error "Failed to create team (HTTP $http_code)"
        if [ "$DEBUG" = true ]; then
            echo "Response: $body"
        fi
        exit 1
    fi

    local team_id
    team_id=$(echo "$body" | jq -r '.id')
    log_info "Team '$team_name' created successfully (ID: $team_id)"
}

# Function to run FOSSA analysis
run_fossa_analysis() {
    local team_name="$1"

    log_info "Running FOSSA analysis and assigning to team '$team_name'..."

    if fossa analyze --team "$team_name"; then
        log_info "FOSSA analysis completed successfully"
    else
        log_error "FOSSA analysis failed"
        exit 1
    fi

    log_info "Running FOSSA test for license and vulnerability checks..."

    if fossa test; then
        log_info "FOSSA test completed successfully"
    else
        log_warn "FOSSA test failed (license or vulnerability issues found)"
        # Don't exit with error - let user decide how to handle test failures
    fi
}

# Main execution
main() {
    log_info "FOSSA Team Management and Analysis"
    log_info "Team: $TEAM_NAME"
    log_info "Endpoint: $FOSSA_ENDPOINT"
    echo ""

    # Check/create team unless analyze-only mode
    if [ "$ANALYZE_ONLY" = false ]; then
        if ! check_team_exists "$TEAM_NAME"; then
            create_team "$TEAM_NAME"
        fi
    fi

    # Run analysis unless create-only mode
    if [ "$CREATE_ONLY" = false ]; then
        run_fossa_analysis "$TEAM_NAME"
        echo ""
        log_info "Project analyzed and assigned to team '$TEAM_NAME'"
        log_info "View results at: $FOSSA_ENDPOINT"
    fi
}

main "$@"
