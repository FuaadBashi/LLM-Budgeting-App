"""Login throttling. Phase 10.

The existing half-second delay on a failed password is honest about what it is
-- its own comment says it is not a substitute for rate limiting. At 0.5s an
attempt that is still about 170,000 guesses a day against a single password,
which is fine on localhost and not fine on a network.

**Exponential backoff, not lockout.** A single-user app has one account, so
locking "the account" locks the only person who can use it -- and anyone who can
reach the login form could then deny the owner access by failing on purpose.
Growing delays make brute force infeasible (the tenth attempt already waits
minutes) while leaving the owner able to get in by waiting. That trade is the
right way round: an attacker needs thousands of attempts, the owner needs one.

The counter is in-process, so a restart clears it. For a single-instance
personal app that is acceptable and worth saying out loud rather than implying:
an attacker who can restart the process has already won by other means.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.clock import now as utc_now

#: Failures allowed at full speed before backoff starts. Fat-fingering a
#: password twice is normal and should not be punished.
FREE_ATTEMPTS = 3
#: First penalty, doubling per failure after that.
BASE_DELAY_SECONDS = 2.0
#: Ceiling, so the owner is never locked out permanently. Ten minutes makes
#: sustained guessing pointless without making recovery impossible.
MAX_DELAY_SECONDS = 600.0


@dataclass
class Throttle:
    failures: int = 0
    #: When the next attempt is allowed. Compared against the clock, not slept
    #: through -- holding a worker thread open is how a slow-login turns into a
    #: denial of service against the whole API.
    blocked_until: object | None = field(default=None)

    def penalty(self) -> float:
        if self.failures <= FREE_ATTEMPTS:
            return 0.0
        steps = self.failures - FREE_ATTEMPTS - 1
        return min(BASE_DELAY_SECONDS * (2**steps), MAX_DELAY_SECONDS)

    def retry_after(self) -> int:
        """Seconds until another attempt is accepted. Zero means now."""
        if self.blocked_until is None:
            return 0
        remaining = (self.blocked_until - utc_now()).total_seconds()
        return max(0, int(remaining + 0.999))

    def record_failure(self) -> None:
        from datetime import timedelta

        self.failures += 1
        delay = self.penalty()
        self.blocked_until = utc_now() + timedelta(seconds=delay) if delay else None

    def record_success(self) -> None:
        """One correct password clears the history. It was the owner."""
        self.failures = 0
        self.blocked_until = None


#: Global, because there is exactly one password. Keying by IP would just invite
#: an attacker to rotate addresses, which is cheap, while doing nothing about
#: the single thing being guessed.
_state = Throttle()


def check() -> int:
    """Seconds the caller must wait, or 0 if an attempt is allowed now."""
    return _state.retry_after()


def record_failure() -> None:
    _state.record_failure()


def record_success() -> None:
    _state.record_success()


def reset() -> None:
    """For tests, and for a deliberate restart of the count."""
    _state.failures = 0
    _state.blocked_until = None


def state() -> Throttle:
    return _state
