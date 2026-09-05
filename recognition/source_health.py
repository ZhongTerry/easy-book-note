import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class _SourceState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_outcome: str = 'unknown'


class SourceHealthTracker:
    """In-memory, domain-level circuit breaker for crawler requests."""

    def __init__(
        self,
        clock=time.monotonic,
        failure_threshold: int = 3,
        base_cooldown_seconds: int = 15,
        max_cooldown_seconds: int = 15 * 60,
        challenge_cooldown_seconds: int = 30 * 60,
    ):
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._base_cooldown_seconds = base_cooldown_seconds
        self._max_cooldown_seconds = max_cooldown_seconds
        self._challenge_cooldown_seconds = challenge_cooldown_seconds
        self._states: dict[str, _SourceState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def source_key(url: str) -> str:
        return (urlparse(url).hostname or '').lower()

    def cooldown_remaining(self, url: str) -> int:
        source = self.source_key(url)
        if not source:
            return 0
        with self._lock:
            state = self._states.get(source)
            if not state:
                return 0
            return max(0, int(state.cooldown_until - self._clock()))

    def record_success(self, url: str) -> None:
        source = self.source_key(url)
        if not source:
            return
        with self._lock:
            state = self._states.setdefault(source, _SourceState())
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            state.last_outcome = 'success'

    def record_failure(self, url: str, reason: str) -> None:
        source = self.source_key(url)
        if not source:
            return
        with self._lock:
            state = self._states.setdefault(source, _SourceState())
            state.consecutive_failures += 1
            state.last_outcome = reason
            if reason == 'challenge':
                cooldown = self._challenge_cooldown_seconds
            elif state.consecutive_failures >= self._failure_threshold:
                exponent = state.consecutive_failures - self._failure_threshold
                cooldown = min(self._max_cooldown_seconds, self._base_cooldown_seconds * (2 ** exponent))
            else:
                return
            state.cooldown_until = max(state.cooldown_until, self._clock() + cooldown)

    def snapshot(self, url: str) -> dict[str, int | str]:
        source = self.source_key(url)
        with self._lock:
            state = self._states.get(source, _SourceState())
            return {
                'source': source,
                'consecutive_failures': state.consecutive_failures,
                'cooldown_seconds': max(0, int(state.cooldown_until - self._clock())),
                'last_outcome': state.last_outcome,
            }
