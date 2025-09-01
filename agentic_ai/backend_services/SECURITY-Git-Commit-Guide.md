# 🔒 Secure Git Commit Guide

This document explains how to safely commit your MCP server and DevTunnel configuration without exposing sensitive information.

## 🚨 Sensitive Information Identified

The following files contain sensitive data that should NOT be committed:

1. **`.env`** - Contains Azure OpenAI API keys and endpoints
2. **`crm-mcp-connector.yaml`** - Contains your specific DevTunnel URL
3. **Any files with actual secrets or connection strings**

## ✅ What's Safe to Commit

### Template Files Created:
- ✅ `.env.template` - Template with placeholder values
- ✅ `crm-mcp-connector.template.yaml` - Template with placeholder DevTunnel URL
- ✅ `README-MCP-DevTunnel-CopilotStudio.md` - Documentation (safe)
- ✅ `mcp_service.py` - Application code (safe)

### Updated Security:
- ✅ `.gitignore` updated to exclude sensitive files
- ✅ Template files use placeholder values

## 🔧 Before Committing - Setup Steps

### 1. Verify .gitignore Protection

The `.gitignore` now includes:
```gitignore
# DevTunnel and sensitive configuration files
**/crm-mcp-connector.yaml
**/*devtunnel*.yaml
**/*.local.yaml
**/*.personal.yaml
```

### 2. Check Git Status

```bash
git status
```

Should show:
- ✅ `crm-mcp-connector.template.yaml` (new file)
- ✅ `.env.template` (new file) 
- ✅ `README-MCP-DevTunnel-CopilotStudio.md` (new file)
- ❌ Should NOT show `.env` or `crm-mcp-connector.yaml`

### 3. Remove Sensitive Files from Git Tracking (if already tracked)

```bash
# Remove from git but keep local files
git rm --cached agentic_ai/applications/.env
git rm --cached agentic_ai/backend_services/crm-mcp-connector.yaml

# Verify they're ignored
git status
```

## 🚀 Safe Commit Process

### Step 1: Add Safe Files Only

```bash
# Add template files
git add agentic_ai/applications/.env.template
git add agentic_ai/backend_services/crm-mcp-connector.template.yaml
git add agentic_ai/backend_services/README-MCP-DevTunnel-CopilotStudio.md
git add .gitignore

# Add any other safe code changes
git add agentic_ai/backend_services/mcp_service.py
```

### Step 2: Verify What's Being Committed

```bash
# Review changes before commit
git diff --cached

# Check for any exposed secrets
git diff --cached | grep -i "api[_-]key\|secret\|password\|token\|devtunnel"
```

### Step 3: Commit Safely

```bash
git commit -m "feat: Add MCP server with DevTunnel support and Copilot Studio integration

- Add FastMCP server implementation for customer data
- Add DevTunnel configuration templates  
- Add comprehensive setup documentation
- Add OpenAPI specification template for Copilot Studio
- Update .gitignore to protect sensitive information
- Provide secure deployment instructions"
```

## 📋 Post-Commit Setup Instructions

After committing, anyone cloning the repo should:

### 1. Copy Template Files

```bash
# Copy and customize environment variables
cp agentic_ai/applications/.env.template agentic_ai/applications/.env

# Copy and customize MCP connector
cp agentic_ai/backend_services/crm-mcp-connector.template.yaml agentic_ai/backend_services/crm-mcp-connector.yaml
```

### 2. Update with Real Values

**`.env` file:**
```env
AZURE_OPENAI_ENDPOINT="https://YOUR-ACTUAL-ENDPOINT.openai.azure.com/"
AZURE_OPENAI_API_KEY="YOUR-ACTUAL-API-KEY"
```

**`crm-mcp-connector.yaml` file:**
```yaml
host: YOUR-ACTUAL-DEVTUNNEL-URL.use.devtunnels.ms
```

## 🔍 Verification Commands

### Check for Exposed Secrets

```bash
# Search for potential exposed secrets
git log --patch | grep -i "key\|secret\|password\|token" | head -20

# Check current repository for sensitive patterns
git grep -i "api[_-]key\|secret\|password\|token" -- '*.py' '*.yaml' '*.yml' '*.json' '*.md'
```

### Validate .gitignore

```bash
# Test if sensitive files are properly ignored
echo "test-secret-key" > test-sensitive.env
git status  # Should not show test-sensitive.env
rm test-sensitive.env
```

## 🛡️ Additional Security Best Practices

### 1. Use Environment Variables in Production

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Good: Get from environment
api_key = os.getenv('AZURE_OPENAI_API_KEY')

# Bad: Hardcoded in source
api_key = "your-actual-key-here"
```

### 2. Use Azure Key Vault for Production

```python
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://your-vault.vault.azure.net/", credential=credential)
api_key = client.get_secret("azure-openai-api-key").value
```

### 3. Rotate DevTunnel URLs Regularly

```bash
# Delete old tunnel
devtunnel delete crm-mcp

# Create new tunnel with different name
devtunnel create crm-mcp-new --allow-anonymous
```

## ✅ Final Checklist

Before pushing to remote repository:

- [ ] `.env` file is in `.gitignore` and not tracked
- [ ] `crm-mcp-connector.yaml` is in `.gitignore` and not tracked  
- [ ] Template files contain placeholder values only
- [ ] Documentation doesn't contain actual secrets
- [ ] Verified with `git status` and `git diff --cached`
- [ ] Searched commit for sensitive patterns
- [ ] Added setup instructions for other developers

## 🆘 If You Accidentally Committed Secrets

If you've already committed sensitive information:

### 1. Remove from History (Local Only)

```bash
# Reset to before the sensitive commit
git reset --hard HEAD~1

# Or remove specific files from last commit
git reset HEAD~1 -- agentic_ai/applications/.env
git commit --amend
```

### 2. If Already Pushed to Remote

```bash
# Force push (DANGEROUS - coordinate with team)
git push --force-with-lease origin main
```

### 3. Rotate All Exposed Secrets

- 🔄 Generate new Azure OpenAI API keys
- 🔄 Create new DevTunnel URLs  
- 🔄 Update all dependent services

---

**Remember**: It's always better to be safe than sorry with sensitive information! 🔒