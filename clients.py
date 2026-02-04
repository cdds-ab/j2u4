"""API clients for Jira and Tempo."""

import requests

ACCOUNT_FIELD = "customfield_10048"


class ApiError(Exception):
    """User-friendly API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _handle_api_error(response: requests.Response, service: str) -> str:
    """Convert HTTP errors to user-friendly messages."""
    status = response.status_code

    messages = {
        401: f"{service}: Authentication failed. Check your API token!",
        403: f"{service}: Access denied. Check your permissions or API token!",
        404: f"{service}: Resource not found. Check the URL in config.json!",
        429: f"{service}: Too many requests. Wait a moment and try again.",
        500: f"{service}: Server error. The service may be temporarily unavailable.",
        502: f"{service}: Bad gateway. The service may be temporarily unavailable.",
        503: f"{service}: Service unavailable. Try again later.",
    }

    return messages.get(status, f"{service}: HTTP {status} - {response.reason}")


class JiraClient:
    """Client for Jira REST API."""

    def __init__(self, config: dict):
        self.base_url = config["jira"]["base_url"]
        self.email = config["jira"]["user_email"]
        self.token = config["jira"]["api_token"]

    def get_my_account_id(self) -> str:
        """Get the current user's Jira account ID."""
        try:
            r = requests.get(
                f"{self.base_url}/rest/api/3/myself",
                auth=(self.email, self.token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
        except requests.exceptions.ConnectionError:
            raise ApiError(f"Jira: Cannot connect to {self.base_url}. Check your network!")
        except requests.exceptions.Timeout:
            raise ApiError("Jira: Connection timed out. The server may be slow.")

        if not r.ok:
            raise ApiError(_handle_api_error(r, "Jira"), r.status_code)
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
            try:
                r = requests.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/json",
                    },
                    params=params,
                    timeout=30,
                )
            except requests.exceptions.ConnectionError:
                raise ApiError("Tempo: Cannot connect to api.tempo.io. Check your network!")
            except requests.exceptions.Timeout:
                raise ApiError("Tempo: Connection timed out. The server may be slow.")

            if not r.ok:
                raise ApiError(_handle_api_error(r, "Tempo"), r.status_code)
            data = r.json()

            worklogs.extend(data.get("results", []))

            # Handle pagination
            url = data.get("metadata", {}).get("next")
            params = {}  # Clear params for pagination URLs

        return worklogs
