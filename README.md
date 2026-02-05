# j2u4

Sync Tempo worklogs to Unit4 Zeiterfassung via Playwright browser automation.

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| **OS** | Linux / macOS | Windows: use WSL (see below) |
| **Python** | 3.11+ | |
| **Node.js** | 18+ | Required for Playwright |
| **Network** | VPN | If required for Unit4 access |

### Windows Users

The shell scripts (`setup.sh`, `sync`, `build-mapping`) require a Unix shell.
On Windows, use **WSL** (Windows Subsystem for Linux):

```powershell
# 1. Install WSL (run as Administrator)
wsl --install

# 2. Open Ubuntu terminal, then follow Quick Start below
```

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd j2u4
./setup.sh

# 2. Edit config.json with your API tokens

# 3. Test connectivity
./sync --check

# 4. Sync a week (dry-run first, then execute)
./sync 202606
./sync 202606 --execute
```

## How it works

```
Tempo API ──→ Worklogs (date, hours, issue_id)
    │
    ▼
Jira API ──→ Issue Details (key, summary, Account field)
    │
    ▼
Mapping ──→ Tempo Account → Unit4 ArbAuft
    │
    ▼
Playwright ──→ Unit4 Zeiterfassung (browser automation)
```

## Setup

### Automatic Setup (recommended)

```bash
./setup.sh
```

This will:
- Create a Python virtual environment
- Install all dependencies
- Install Chromium for browser automation
- Create `config.json` from template

### Manual Setup

1. **Install dependencies**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Create config file**
   ```bash
   cp config.example.json config.json
   ```

### API Tokens

You need two API tokens:

- **Jira API Token**: [Create here](https://id.atlassian.com/manage-profile/security/api-tokens)
- **Tempo API Token**: Go to Tempo > Settings > API Integration
  `https://<YOUR-ORG>.atlassian.net/plugins/servlet/ac/io.tempo.jira/tempo-app#!/configuration/api-integration`

Edit `config.json` with your credentials:
```json
{
  "jira": {
    "base_url": "https://<YOUR-ORG>.atlassian.net",
    "user_email": "your-email@example.com",
    "api_token": "your-jira-api-token"
  },
  "tempo": {
    "api_token": "your-tempo-api-token"
  },
  "unit4": {
    "url": "https://ubw.unit4cloud.com/<YOUR-TENANT>/Default.aspx"
  }
}
```

### First run (login)

On first run, Unit4 will prompt for login (2FA). The session is saved to `session.json` for subsequent runs.

## Usage

### Check connectivity first

```bash
./sync --check
```

This tests Jira, Tempo, and Unit4 connectivity before syncing.

### Sync a specific week

```bash
# Dry-run (default) - shows what would happen
./sync 202605

# Execute - actually creates entries
./sync 202605 --execute
```

The week format is `YYYYWW` (ISO week number), e.g., `202605` = Week 5 of 2026.

### What the script does

1. Fetches worklogs from Tempo for the specified week
2. Looks up Jira issues to get the Account field
3. Maps Account → Unit4 ArbAuft code
4. Opens Unit4 in a browser (you can watch!)
5. **Deletes** all existing `[WL:xxx]` entries for that week
6. **Creates** fresh entries from Tempo

### Entry marker format

Entries are marked with `[WL:xxx]` at the beginning of the text field:
```
[WL:1764] working on concept
```
This allows tracking which Unit4 entries were synced from which Tempo worklog.

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | One-time setup (creates venv, installs dependencies) |
| `sync` | Wrapper script for syncing (use this!) |
| `build-mapping` | Wrapper script for building mappings |
| `sync_tempo_to_unit4.py` | Main sync script (Python) |
| `build_mapping_from_history.py` | Build account→arbauft mapping from Unit4 history |
| `config.json` | Credentials (gitignored!) |
| `config.example.json` | Template for config.json |
| `account_to_arbauft_mapping.json` | Account to ArbAuft mapping (gitignored!) |
| `session.json` | Browser session (gitignored!) |

## Account Mappings

The script needs to know which Tempo Account maps to which Unit4 ArbAuft code.
This mapping is stored in `account_to_arbauft_mapping.json`.

### Option 1: Auto-build from Unit4 history (recommended)

If you already have time entries in Unit4, the script can learn the mappings:

```bash
# Scan last 8 weeks (default)
./build-mapping

# Scan last 12 weeks
./build-mapping --weeks 12

# Scan specific range
./build-mapping --from 202601 --to 202610
```

This opens Unit4, scans the specified weeks, and builds the mapping automatically.

### Option 2: Enter mappings during sync

When the script encounters an unknown Tempo account, it will prompt you:

```
Unknown Account: 42 (ACME - Development)
  Ticket: ACME-1234
  Summary: Fix deployment pipeline

Enter ArbAuft (e.g., 1234-56789-001) or SKIP to skip:
```

Enter the ArbAuft code and it will be saved for future use.

### Option 3: Manual editing

Edit `account_to_arbauft_mapping.json` directly:

```json
{
  "42": {
    "unit4_arbauft": "1234-56789-001",
    "tempo_name": "ACME - Development",
    "sample_ticket": "ACME-1234"
  }
}
```

### Finding the right ArbAuft code

The ArbAuft code (e.g., `1234-56789-001`) is visible in Unit4 when you create a time entry.
It's the "ArbAuft" field in the entry form.

## Command Reference

| Command | Description |
|---------|-------------|
| `./setup.sh` | Initial setup (run once after cloning) |
| `./sync --check` | Test connectivity to Jira, Tempo, Unit4 |
| `./sync YYYYWW` | Dry-run sync for week (e.g., `./sync 202606`) |
| `./sync YYYYWW --execute` | Actually sync the week |
| `./sync YYYYWW --cutover YYYY-MM-DD --execute` | Sync from cutover date onwards |
| `./build-mapping` | Build mappings from last 8 weeks |
| `./build-mapping --weeks N` | Build mappings from last N weeks |
| `./build-mapping --from YYYYWW --to YYYYWW` | Build mappings from specific range |

## Troubleshooting

### "config.json not found"
- Run `./setup.sh` to create from template, or
- Copy manually: `cp config.example.json config.json`

### "Authentication failed" / API errors
- Run `./sync --check` to diagnose connectivity issues
- Verify your API tokens are correct in `config.json`
- Jira token: Check it's not expired at [Atlassian Account](https://id.atlassian.com/manage-profile/security/api-tokens)
- Tempo token: Regenerate in Tempo Settings > API Integration

### "Cannot connect to Unit4"
- Make sure you're connected to VPN (if required)
- Check the URL in `config.json` is correct

### "Page not loaded" / "Ergänzen not found"
- The script waits for the page to load, but Unit4 can be slow
- If it times out, try running again

### Duplicate entries
- The script deletes all `[WL:xxx]` entries before creating new ones
- If duplicates appear, run the script again to clean them up

### Session expired
- The script will detect this and prompt for re-login
- If issues persist, delete `session.json` and run again

## Security

- **Never commit** `config.json` or `session.json`
- These files are in `.gitignore`
- Use `config.example.json` as template
