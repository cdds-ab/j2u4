"""API clients for Jira and Tempo."""

import requests

ACCOUNT_FIELD = "customfield_10048"


class JiraClient:
    """Client for Jira REST API."""

    def __init__(self, config: dict):
        self.base_url = config["jira"]["base_url"]
        self.email = config["jira"]["user_email"]
        self.token = config["jira"]["api_token"]

    def get_my_account_id(self) -> str:
        """Get the current user's Jira account ID."""
        r = requests.get(
            f"{self.base_url}/rest/api/3/myself",
            auth=(self.email, self.token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["accountId"]

    def get_issue_details(self, issue_id: int) -> dict | None:
        """Fetch issue details (key, summary, account field)."""
        r = requests.get(
            f"{self.base_url}/rest/api/3/issue/{issue_id}",
            auth=(self.email, self.token),
            headers={"Accept": "application/json"},
            params={"fields": f"key,summary,{ACCOUNT_FIELD}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        return None


class TempoClient:
    """Client for Tempo REST API."""

    def __init__(self, config: dict):
        self.token = config["tempo"]["api_token"]

    def fetch_worklogs(
        self, account_id: str, date_from: str, date_to: str
    ) -> list[dict]:
        """Fetch worklogs for a user within a date range."""
        worklogs = []
        url = f"https://api.tempo.io/4/worklogs/user/{account_id}"
        params = {"from": date_from, "to": date_to, "limit": 1000}

        while url:
            r = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                params=params,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()

            worklogs.extend(data.get("results", []))

            # Handle pagination
            url = data.get("metadata", {}).get("next")
            params = {}  # Clear params for pagination URLs

        return worklogs
