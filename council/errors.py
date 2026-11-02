class ConsiliumError(Exception):
    """Base class for all council errors."""


class PrivacyRefusal(ConsiliumError):
    """Prompt contains a secret, or no member satisfies the required tier."""


class NoEligibleMember(ConsiliumError):
    """No member has the requested capability."""


class AllMembersFailed(ConsiliumError):
    """Every fan-out member abstained."""


class ProxyError(ConsiliumError):
    """The proxy itself rejected/failed the request (auth, unreachable); not a member failure."""


class MemberCallError(ConsiliumError):
    """A single member call failed (non-200 / timeout).

    ``permanent`` marks failures that will not heal on retry (provider auth/billing,
    model gone) so callers can mark the member unhealthy instead of retrying.
    """

    def __init__(self, alias: str, detail: str, *, permanent: bool = False) -> None:
        super().__init__(f"{alias}: {detail}")
        self.alias = alias
        self.detail = detail
        self.permanent = permanent
