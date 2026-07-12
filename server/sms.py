"""SMS provider adapters.

MockSMSProvider is the only provider dev/test ever use — it records sends
in memory and never touches a network. TwilioSMSProvider is the production
adapter shape; it is UNVERIFIED (no account, no BAA yet — docs/05) and is
constructed only when explicitly configured.
"""

import os
from dataclasses import dataclass, field


class SMSError(Exception):
    pass


@dataclass
class SentSMS:
    provider_message_id: str
    from_e164: str
    to_e164: str
    body: str


@dataclass
class MockSMSProvider:
    """MOCK — records outbound messages; optionally fails specific numbers."""

    name: str = "mock"
    fail_numbers: set[str] = field(default_factory=set)
    outbox: list[SentSMS] = field(default_factory=list)
    _counter: int = 0

    def send(self, *, from_e164: str, to_e164: str, body: str) -> str:
        if to_e164 in self.fail_numbers:
            raise SMSError(f"MOCK delivery failure to {to_e164}")
        self._counter += 1
        pmid = f"MOCK-{self._counter:06d}"
        self.outbox.append(
            SentSMS(provider_message_id=pmid, from_e164=from_e164, to_e164=to_e164, body=body)
        )
        return pmid


class TwilioSMSProvider:
    """Twilio REST adapter — UNVERIFIED: requires account, BAA, 10DLC (docs/06).

    Kept minimal and dependency-free (raw HTTP); do not enable in any
    environment that handles real patients until the docs/05 checklist items
    for Twilio are ☑.
    """

    name = "twilio"

    def __init__(self, account_sid: str | None = None, auth_token: str | None = None):
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN", "")
        if not self.account_sid or not self.auth_token:
            raise SMSError("Twilio credentials not configured")

    def send(self, *, from_e164: str, to_e164: str, body: str) -> str:
        import httpx

        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            auth=(self.account_sid, self.auth_token),
            data={"From": from_e164, "To": to_e164, "Body": body},
            timeout=15,
        )
        if resp.status_code >= 400:
            raise SMSError(f"twilio error {resp.status_code}: {resp.text[:200]}")
        return resp.json()["sid"]
