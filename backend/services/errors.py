"""Data-provider errors.

Each error carries an Azerbaijani ``user_message`` for Telegram/CLI/API and an
English ``detail`` for logs. Raw provider texts (often English) never reach the
user.
"""

from __future__ import annotations

from backend.i18n import t


class ProviderError(Exception):
    message_key = "error.provider_unavailable"

    def __init__(self, detail: str = "", **params: object) -> None:
        super().__init__(detail or self.message_key)
        self.detail = detail
        self.params = params

    @property
    def user_message(self) -> str:
        return t(self.message_key, **self.params)


class ProviderUnavailableError(ProviderError):
    """Timeouts, connection failures and 5xx after all retries."""

    message_key = "error.provider_unavailable"


class ProviderResponseError(ProviderError):
    """The provider answered, but not with something we can use."""

    message_key = "error.bad_response"


class RateLimitedError(ProviderError):
    message_key = "error.rate_limited"


class QuotaExhaustedError(ProviderError):
    message_key = "error.quota_exhausted"


class PlanRestrictedError(ProviderError):
    message_key = "error.plan_restricted"


class ProviderAuthError(ProviderError):
    message_key = "error.provider_auth"

    def __init__(self, detail: str = "", *, key_name: str) -> None:
        super().__init__(detail, key_name=key_name)


class MissingApiKeyError(ProviderError):
    message_key = "error.missing_api_key"

    def __init__(self, *, key_name: str) -> None:
        super().__init__(f"{key_name} is not set", key_name=key_name)
