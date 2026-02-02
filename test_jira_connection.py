"""Test Jira API connection and worklog visibility."""

import json
import requests


def main():
    with open("config.json") as f:
        config = json.load(f)

    base_url = config["jira"]["base_url"]
    email = config["jira"]["user_email"]
    token = config["jira"]["api_token"]

    # Test 1: Get myself
    print("[*] Testing /myself endpoint...")
    r = requests.get(
        f"{base_url}/rest/api/3/myself",
        auth=(email, token),
        headers={"Accept": "application/json"},
    )
    print(f"    Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"    User: {data.get('displayName', '?')}")
        print(f"    Email: {data.get('emailAddress', '?')}")
    else:
        print(f"    Error: {r.text[:200]}")
        return

    # Test 2: Search for recent worklogs
    print()
    print("[*] Testing worklog search (last 7 days)...")
    jql = "worklogAuthor = currentUser() AND worklogDate >= -7d"
    payload = {"jql": jql, "maxResults": 10, "fields": ["key", "summary"]}
    r = requests.post(
        f"{base_url}/rest/api/3/search/jql",
        auth=(email, token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload,
    )
    print(f"    Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        issues = data.get("issues", [])
        print(f"    Found {len(issues)} issues with worklogs")
        for issue in issues[:5]:
            print(f"      - {issue['key']}: {issue['fields']['summary'][:50]}")
    else:
        print(f"    Error: {r.text[:300]}")

    # Test 3: Get actual worklogs for found issues
    if r.status_code == 200 and issues:
        print()
        print("[*] Fetching actual worklogs for each issue...")

        # Get my account ID
        myself = requests.get(
            f"{base_url}/rest/api/3/myself",
            auth=(email, token),
            headers={"Accept": "application/json"},
        ).json()
        my_account_id = myself.get("accountId", "")

        total_my_worklogs = 0
        for issue in issues:
            issue_key = issue["key"]
            wl_resp = requests.get(
                f"{base_url}/rest/api/3/issue/{issue_key}/worklog",
                auth=(email, token),
                headers={"Accept": "application/json"},
            )
            if wl_resp.status_code == 200:
                worklogs = wl_resp.json().get("worklogs", [])
                # Filter: only my worklogs in last 7 days
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

                my_worklogs = []
                for wl in worklogs:
                    author_id = wl.get("author", {}).get("accountId", "")
                    started = wl.get("started", "")[:10]
                    if author_id == my_account_id and started >= cutoff:
                        hours = wl.get("timeSpentSeconds", 0) / 3600
                        comment = ""
                        if wl.get("comment"):
                            try:
                                comment = wl["comment"]["content"][0]["content"][0]["text"][:30]
                            except (KeyError, IndexError):
                                pass
                        my_worklogs.append({"date": started, "hours": hours, "comment": comment})

                if my_worklogs:
                    print(f"    {issue_key}:")
                    for wl in my_worklogs:
                        print(f"      {wl['date']}: {wl['hours']:.2f}h - {wl['comment']}")
                    total_my_worklogs += len(my_worklogs)

        print()
        print(f"[*] Total: {total_my_worklogs} worklogs (yours, last 7 days) via JIRA API")

    # Test 4: Try Tempo API
    print()
    print("[*] Testing TEMPO API...")

    # Get my account ID
    myself_resp = requests.get(
        f"{base_url}/rest/api/3/myself",
        auth=(email, token),
        headers={"Accept": "application/json"},
    )
    my_account_id = myself_resp.json().get("accountId", "")

    from datetime import datetime, timedelta
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")

    # Tempo Cloud API (different from Server)
    tempo_endpoints = [
        # Tempo Cloud (api.tempo.io)
        f"https://api.tempo.io/4/worklogs/user/{my_account_id}?from={date_from}&to={date_to}",
        # Tempo Server/DC style
        f"{base_url}/rest/tempo-timesheets/4/worklogs?dateFrom={date_from}&dateTo={date_to}",
        f"{base_url}/rest/tempo-core/1/user/schedule/{my_account_id}?from={date_from}&to={date_to}",
    ]

    # Check if there's a tempo token in config
    tempo_token = config.get("tempo", {}).get("api_token", "")

    for endpoint in tempo_endpoints:
        print(f"    Trying: {endpoint[:60]}...")
        try:
            if "api.tempo.io" in endpoint and tempo_token:
                # Tempo Cloud uses Bearer token
                r = requests.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {tempo_token}", "Accept": "application/json"},
                    timeout=5,
                )
            else:
                # Tempo Server uses Jira auth
                r = requests.get(
                    endpoint,
                    auth=(email, token),
                    headers={"Accept": "application/json"},
                    timeout=5,
                )
            print(f"      Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    print(f"      Found {len(data)} worklogs")
                elif isinstance(data, dict) and "results" in data:
                    print(f"      Found {len(data['results'])} worklogs")
                    print()

                    # Show first worklog structure for debugging
                    print("      [DEBUG] First worklog structure:")
                    first_wl = data["results"][0]
                    for key in first_wl.keys():
                        val = first_wl[key]
                        if isinstance(val, dict):
                            print(f"        {key}: {list(val.keys())}")
                        elif isinstance(val, list):
                            print(f"        {key}: [{len(val)} items]")
                        else:
                            print(f"        {key}: {val}")
                    print()

                    print("      Resolving issue keys...")

                    # Cache for issue ID -> key mapping
                    issue_cache = {}

                    for wl in data["results"]:
                        date = wl.get("startDate", "?")
                        hours = wl.get("timeSpentSeconds", 0) / 3600
                        issue_id = wl.get("issue", {}).get("id")
                        desc = wl.get("description", "")[:30]

                        # Tempo Account field (for ArbAuft mapping)
                        account = wl.get("billableSeconds")  # Sometimes here
                        tempo_account = None
                        if "attributes" in wl:
                            # Tempo attributes (custom fields in worklog)
                            for attr in wl.get("attributes", {}).get("values", []):
                                print(f"          Tempo attr: {attr}")

                        # Check for account in worklog directly
                        if "account" in wl:
                            tempo_account = wl["account"]
                            print(f"          Tempo Account: {tempo_account}")

                        # Resolve issue ID to key and fields via Jira API
                        if issue_id and issue_id not in issue_cache:
                            r_issue = requests.get(
                                f"{base_url}/rest/api/3/issue/{issue_id}",
                                auth=(email, token),
                                headers={"Accept": "application/json"},
                            )
                            if r_issue.status_code == 200:
                                issue_data = r_issue.json()
                                fields = issue_data.get("fields", {})
                                issue_cache[issue_id] = {
                                    "key": issue_data.get("key"),
                                    "summary": fields.get("summary", "")[:40],
                                    "project": fields.get("project", {}).get("key"),
                                    "components": [c.get("name") for c in fields.get("components", [])],
                                    "labels": fields.get("labels", []),
                                    "issuetype": fields.get("issuetype", {}).get("name"),
                                    "fields": fields,  # Keep all fields for inspection
                                }
                            else:
                                issue_cache[issue_id] = {"key": f"ID:{issue_id}", "summary": "?"}

                        cached = issue_cache.get(issue_id, {})
                        issue_key = cached.get("key", "?")
                        fields = cached.get("fields", {})

                        # Extract Account field (customfield_10048)
                        account_field = fields.get("customfield_10048", {})
                        if isinstance(account_field, dict):
                            account_name = account_field.get("name") or account_field.get("value") or "?"
                            account_key = account_field.get("key") or account_field.get("id") or "?"
                        else:
                            account_name = str(account_field) if account_field else "?"
                            account_key = "?"

                        print(f"        {date}: {hours:.2f}h | {issue_key} | Account: {account_name} ({account_key})")

                    # Show all custom fields from first issue for analysis
                    if issue_cache:
                        print()
                        print("      [DEBUG] All fields from first issue (looking for contract/arbauft):")
                        first_issue = list(issue_cache.values())[0]
                        fields = first_issue.get("fields", {})
                        for field_key, field_value in fields.items():
                            if field_key.startswith("customfield_") and field_value:
                                # Try to extract meaningful value
                                if isinstance(field_value, dict):
                                    val = field_value.get("value") or field_value.get("name") or str(field_value)[:50]
                                elif isinstance(field_value, list) and field_value:
                                    val = [v.get("value") or v.get("name") or str(v)[:30] for v in field_value[:3]]
                                else:
                                    val = str(field_value)[:50]
                                print(f"          {field_key}: {val}")
                else:
                    print(f"      Response: {str(data)[:100]}")
                break
        except Exception as e:
            print(f"      Error: {e}")


if __name__ == "__main__":
    main()
