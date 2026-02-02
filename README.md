# jira2unit4

Sync Tempo worklogs to Unit4 Zeiterfassung via Playwright browser automation.

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

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Create API tokens**

   - **Jira API Token**: [Create here](https://id.atlassian.com/manage-profile/security/api-tokens)
   - **Tempo API Token**: Go to Tempo > Settings > API Integration
     `https://<YOUR-ORG>.atlassian.net/plugins/servlet/ac/io.tempo.jira/tempo-app#!/configuration/api-integration`

3. **Create config file**
   ```bash
   cp config.example.json config.json
   ```
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

4. **First run (login)**

   On first run, Unit4 will prompt for login (2FA). The session is saved to `session.json` for subsequent runs.

## Usage

### Sync a specific week

```bash
# Dry-run (default) - shows what would happen
python sync_tempo_to_unit4.py 202605

# Execute - actually creates entries
python sync_tempo_to_unit4.py 202605 --execute
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
| `sync_tempo_to_unit4.py` | Main sync script |
| `build_mapping_from_history.py` | Build account→arbauft mapping from Unit4 history |
| `test_jira_connection.py` | Test Jira/Tempo API connectivity |
| `account_to_arbauft_mapping.json` | Account to ArbAuft mapping |
| `config.json` | Credentials (gitignored!) |
| `session.json` | Browser session (gitignored!) |

## Adding new account mappings

When the script encounters an unknown Tempo account, it will prompt for the Unit4 ArbAuft code. The mapping is saved to `account_to_arbauft_mapping.json`.

You can also manually edit this file:
```json
{
  "42": {
    "unit4_arbauft": "1234-56789-001",
    "tempo_name": "ACME - DevOps",
    "sample_ticket": "ACME-11578"
  }
}
```

## Troubleshooting

### "Page not loaded" / "Ergänzen not found"
- The script waits for the page to load, but Unit4 can be slow
- If it times out, try running again

### Duplicate entries
- The script deletes all `[WL:xxx]` entries before creating new ones
- If duplicates appear, run the script again to clean them up

### Session expired
- Delete `session.json` and run again to re-login

## Security

- **Never commit** `config.json` or `session.json`
- These files are in `.gitignore`
- Use `config.example.json` as template
