# FOSSA Team Management and Analysis

This repository contains tools for automatically managing FOSSA teams and running analysis with team assignment.

## Overview

These tools help you:
1. Query existing FOSSA teams via API
2. Create a new team if it doesn't exist
3. Run FOSSA analysis and assign the project to a team
4. Handle new repositories being added to FOSSA

## Available Tools

### 1. GitHub Action Workflow

**File:** `fossa-team-analysis.yml`

A comprehensive GitHub Actions workflow that manages teams and runs FOSSA analysis in your CI/CD pipeline.

#### Features

- Queries existing teams via FOSSA API
- Creates team automatically if it doesn't exist
- Runs FOSSA analysis with team assignment
- Runs FOSSA test for license and vulnerability checks
- Supports custom FOSSA endpoints
- Configurable via workflow inputs or defaults

#### Setup

1. **Add the workflow to your repository:**

```bash
mkdir -p .github/workflows
cp fossa-team-analysis.yml .github/workflows/
```

2. **Configure secrets:**

Add your FOSSA API key to GitHub repository secrets:
- Go to Settings → Secrets and variables → Actions
- Add secret named `FOSSA_API_KEY` with your FOSSA API key

3. **Configure the workflow:**

The workflow can be triggered in three ways:

**Option A: Manual trigger with custom team name**
```yaml
on:
  workflow_dispatch:
    inputs:
      team_name:
        description: 'Team name to assign the project to'
        required: true
```

**Option B: Automatic on push (uses repository owner as team)**
```yaml
on:
  push:
    branches:
      - main
```

**Option C: On pull requests**
```yaml
on:
  pull_request:
```

#### Usage Examples

**Manual trigger:**
1. Go to Actions tab in GitHub
2. Select "FOSSA Team Analysis" workflow
3. Click "Run workflow"
4. Enter team name (e.g., "Engineering Team")
5. Optionally specify custom FOSSA endpoint

**Automatic on push:**
```bash
git push origin main
# Workflow runs automatically, uses repository owner as team name
```

**Custom configuration:**
```yaml
env:
  FOSSA_API_KEY: ${{ secrets.FOSSA_API_KEY }}
  TEAM_NAME: ${{ inputs.team_name || 'Platform Team' }}
  FOSSA_ENDPOINT: ${{ inputs.fossa_endpoint || 'https://app.fossa.com' }}
```

---

### 2. Standalone Bash Script

**File:** `manage-team-and-analyze.sh`

A flexible bash script that can be run locally or in any CI/CD system.

#### Features

- Works in any environment with bash, curl, and jq
- Can create teams only, analyze only, or both
- Debug mode for troubleshooting
- Colored output for readability
- Comprehensive error handling

#### Requirements

- bash 4.0+
- curl
- jq
- FOSSA CLI (auto-installs if missing)

#### Usage

**Basic usage:**
```bash
export FOSSA_API_KEY="your-api-key-here"
./manage-team-and-analyze.sh "Engineering Team"
```

**With custom endpoint:**
```bash
export FOSSA_ENDPOINT="https://fossa.company.com"
export FOSSA_API_KEY="your-api-key-here"
./manage-team-and-analyze.sh "Platform Team"
```

**Create team only (don't run analysis):**
```bash
export FOSSA_API_KEY="your-api-key-here"
./manage-team-and-analyze.sh "New Team" --create-only
```

**Analyze with existing team (skip team creation check):**
```bash
export FOSSA_API_KEY="your-api-key-here"
./manage-team-and-analyze.sh "Existing Team" --analyze-only
```

**Debug mode:**
```bash
export FOSSA_API_KEY="your-api-key-here"
./manage-team-and-analyze.sh "Team Name" --debug
```

**Help:**
```bash
./manage-team-and-analyze.sh --help
```

---

## FOSSA API Reference

### Authentication

All API calls use Bearer token authentication:

```bash
curl -H "Authorization: Bearer $FOSSA_API_KEY" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json" \
     "$FOSSA_ENDPOINT/api/teams"
```

### Key Endpoints

#### List All Teams
```bash
GET /api/teams
```

Response:
```json
[
  {
    "id": 1,
    "name": "Engineering Team",
    "autoAddUsers": false,
    "teamUsers": [],
    "teamProjectsCount": 5,
    "teamReleaseGroupsCount": 2
  }
]
```

#### Create Team
```bash
POST /api/teams
Content-Type: application/json

{
  "name": "New Team",
  "autoAddUsers": false
}
```

Response:
```json
{
  "id": 123,
  "name": "New Team",
  "autoAddUsers": false,
  "createdAt": "2024-01-15T10:00:00Z"
}
```

#### Get Team Details
```bash
GET /api/teams/:id
GET /api/v2/teams/:id  # Extended info
```

#### Add Projects to Team
```bash
PUT /api/teams/:id/projects
Content-Type: application/json

{
  "projects": ["custom+org/repo$revision"],
  "action": "add"
}
```

---

## FOSSA CLI Team Flag

The FOSSA CLI supports the `--team` flag to assign projects to teams during analysis:

```bash
fossa analyze --team "Team Name"
```

This automatically:
1. Assigns the analyzed project to the specified team
2. Creates the team if you have the right permissions (some FOSSA instances)
3. Makes the project visible to all team members

---

## Common Use Cases

### Use Case 1: New Repository Setup

**Scenario:** You're adding a new repository to FOSSA and want it assigned to a specific team.

**Solution:**
```bash
# Option 1: Using the script
export FOSSA_API_KEY="your-key"
./manage-team-and-analyze.sh "Platform Engineering"

# Option 2: Using GitHub Actions
# Add the workflow file and push - it runs automatically
```

### Use Case 2: Multiple Repositories, One Team

**Scenario:** You have multiple repositories that should all belong to the same team.

**Solution:**
```yaml
# In each repository's .github/workflows/fossa-team-analysis.yml
env:
  TEAM_NAME: "Platform Engineering"
```

### Use Case 3: Team Per Repository Owner

**Scenario:** You want each repository owner (org/user) to have their own team.

**Solution:**
```yaml
# Uses GitHub context to get repo owner
env:
  TEAM_NAME: ${{ github.repository_owner }}
```

### Use Case 4: Multi-Environment Setup

**Scenario:** Different FOSSA instances for dev/staging/prod.

**Solution:**
```bash
# Development
export FOSSA_ENDPOINT="https://fossa-dev.company.com"
export FOSSA_API_KEY="dev-key"
./manage-team-and-analyze.sh "Dev Team"

# Production
export FOSSA_ENDPOINT="https://fossa.company.com"
export FOSSA_API_KEY="prod-key"
./manage-team-and-analyze.sh "Production Team"
```

---

## Troubleshooting

### Issue: "Failed to fetch teams (HTTP 401)"

**Cause:** Invalid or expired API key

**Solution:**
1. Verify your API key is correct
2. Check if the API key has the necessary permissions
3. Generate a new API key if needed

### Issue: "Failed to create team (HTTP 403)"

**Cause:** Insufficient permissions to create teams

**Solution:**
1. Contact your FOSSA administrator to grant team creation permissions
2. Have an admin create the team manually
3. Use `--analyze-only` flag to skip team creation

### Issue: "jq: command not found"

**Cause:** jq is not installed

**Solution:**
```bash
# macOS
brew install jq

# Ubuntu/Debian
sudo apt-get install jq

# CentOS/RHEL
sudo yum install jq
```

### Issue: "fossa: command not found"

**Cause:** FOSSA CLI is not installed

**Solution:**
```bash
# The script auto-installs, or manually install:
curl -H 'Cache-Control: no-cache' \
  https://raw.githubusercontent.com/fossas/fossa-cli/master/install-latest.sh | bash
```

### Issue: Team creation succeeds but project not assigned

**Cause:** The team was created but the analyze command didn't use the team flag

**Solution:**
Ensure you're using the `--team` flag:
```bash
fossa analyze --team "Team Name"
```

---

## Advanced Configuration

### Custom Team Properties

You can extend the script to set additional team properties:

```bash
# In manage-team-and-analyze.sh, modify the create_team function:
response=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Authorization: Bearer $FOSSA_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "'"$team_name"'",
      "autoAddUsers": true,
      "defaultRoleId": 2,
      "uniqueIdentifier": "team-slug"
    }' \
    "$FOSSA_ENDPOINT/api/teams")
```

### Adding Users to Team

```bash
# After team creation, add users:
curl -X PUT \
  -H "Authorization: Bearer $FOSSA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "users": [
      {"id": 123, "roleId": 2},
      {"id": 456, "roleId": 3}
    ],
    "action": "add"
  }' \
  "$FOSSA_ENDPOINT/api/teams/$TEAM_ID/users"
```

### Integration with Other CI/CD Systems

**GitLab CI:**
```yaml
fossa-analysis:
  stage: test
  script:
    - export FOSSA_API_KEY=$FOSSA_API_KEY
    - ./manage-team-and-analyze.sh "Engineering Team"
  only:
    - main
```

**Jenkins:**
```groovy
pipeline {
    agent any
    environment {
        FOSSA_API_KEY = credentials('fossa-api-key')
    }
    stages {
        stage('FOSSA Analysis') {
            steps {
                sh './manage-team-and-analyze.sh "Engineering Team"'
            }
        }
    }
}
```

**CircleCI:**
```yaml
version: 2.1
jobs:
  fossa-analysis:
    docker:
      - image: cimg/base:stable
    steps:
      - checkout
      - run:
          name: FOSSA Team Analysis
          command: |
            export FOSSA_API_KEY=$FOSSA_API_KEY
            ./manage-team-and-analyze.sh "Engineering Team"
```

---

## Security Best Practices

1. **Never commit API keys to git:**
   - Use GitHub Secrets for GitHub Actions
   - Use environment variables for local/CI runs
   - Add `.env` files to `.gitignore`

2. **Use least-privilege API keys:**
   - Create dedicated API keys for CI/CD
   - Limit permissions to only what's needed
   - Rotate keys regularly

3. **Validate team names:**
   - Sanitize user input if accepting team names from external sources
   - Use predefined team names when possible

4. **Audit team access:**
   - Regularly review team memberships
   - Remove unused teams
   - Monitor team creation logs

---

## Contributing

To improve these tools:

1. Test changes locally before committing
2. Update documentation when adding features
3. Follow existing code style
4. Add error handling for edge cases

---

## Support

For issues with:
- **FOSSA API:** Contact FOSSA support or check https://docs.fossa.com
- **These tools:** Open an issue in this repository
- **FOSSA CLI:** Check https://github.com/fossas/fossa-cli

---

## License

These tools are provided as-is for use with FOSSA. Check your FOSSA license agreement for API usage terms.
