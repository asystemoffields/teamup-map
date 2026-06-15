"""Minimal async Teamup REST client.

Endpoints confirmed against the Teamup API and the pyTeamUp wrapper:
  base : https://api.teamup.com/{calendarKey}
  auth : ?_teamup_token={key}
  GET  /subcalendars
  GET  /events?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
  GET  /events?modifiedSince={unix_ts}   -> {"events":[...], "timestamp": <int>}
"""
import httpx

from app import config

BASE = "https://api.teamup.com"


class TeamupClient:
    def __init__(self, calendar_id: str = None, token: str = None):
        self.cal = calendar_id or config.CALENDAR_ID
        self.token = token or config.API_KEY
        self._client = httpx.AsyncClient(timeout=30)

    def _url(self, path: str) -> str:
        return f"{BASE}/{self.cal}{path}"

    async def subcalendars(self):
        r = await self._client.get(
            self._url("/subcalendars"), params={"_teamup_token": self.token}
        )
        r.raise_for_status()
        return r.json().get("subcalendars", [])

    async def events(self, start_date: str, end_date: str):
        r = await self._client.get(
            self._url("/events"),
            params={"_teamup_token": self.token, "startDate": start_date, "endDate": end_date},
        )
        r.raise_for_status()
        return r.json().get("events", [])

    async def events_modified_since(self, ts):
        r = await self._client.get(
            self._url("/events"),
            params={"_teamup_token": self.token, "modifiedSince": int(ts)},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("events", []), data.get("timestamp")

    async def aclose(self):
        await self._client.aclose()
