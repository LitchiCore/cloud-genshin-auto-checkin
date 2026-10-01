"""Bounded, thread-safe login throttling for the single-process web server."""
from dataclasses import dataclass
import ipaddress
import math
import threading
import time


def source_ip(peer, headers):
    """Only the loopback reverse proxy may supply one validated X-Real-IP."""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return 'unknown'
    mapped = getattr(address, 'ipv4_mapped', None)
    address = mapped or address
    values = headers.get_all('X-Real-IP', [])
    if address.is_loopback and len(values) == 1:
        value = values[0].strip()
        # ipaddress accepts IPv6 zone identifiers; proxy public-source headers must not.
        if len(value) > 45 or '%' in value:
            return str(address)
        try:
            forwarded = ipaddress.ip_address(value)
            return str(getattr(forwarded, 'ipv4_mapped', None) or forwarded)
        except ValueError:
            pass
    return str(address)


@dataclass
class _State:
    window_start: float
    last_seen: float
    failures: int = 0
    active: int = 0
    blocked_until: float = 0


class LoginGuard:
    def __init__(self, *, user_limit=8, ip_limit=30, user_cooldown=300,
                 ip_cooldown=600, window=900, ttl=1800, max_entries=4096,
                 max_active=16, clock=time.monotonic):
        self.user_limit = user_limit
        self.ip_limit = ip_limit
        self.user_cooldown = user_cooldown
        self.ip_cooldown = ip_cooldown
        self.window = window
        self.ttl = ttl
        self.max_entries = max_entries
        self.max_active = max_active
        self.clock = clock
        self._states = {}
        self._active = 0
        self._lock = threading.Lock()

    def _policy(self, key):
        if key[0] == 'user':
            return self.user_limit, self.user_cooldown
        return self.ip_limit, self.ip_cooldown

    def admit(self, username, ip):
        # Invalid names share one key; callers never retain arbitrarily long input.
        username = username if 0 < len(username) <= 64 else '<invalid>'
        keys = (('user', username), ('ip', ip))
        with self._lock:
            now = self.clock()
            expired = [key for key, state in self._states.items()
                       if not state.active and state.blocked_until <= now
                       and now - state.last_seen >= self.ttl]
            for key in expired:
                del self._states[key]
            retry = 0
            for key in keys:
                state = self._states.get(key)
                if state is None:
                    continue
                if not state.active and (
                    (state.blocked_until and state.blocked_until <= now)
                    or (not state.blocked_until and now - state.window_start >= self.window)
                ):
                    state.failures = 0
                    state.blocked_until = 0
                    state.window_start = now
                state.last_seen = now
                if state.blocked_until > now:
                    retry = max(retry, math.ceil(state.blocked_until - now))
                elif state.failures + state.active >= self._policy(key)[0]:
                    retry = max(retry, 1)
            if retry:
                return None, retry
            missing = sum(key not in self._states for key in keys)
            if len(self._states) + missing > self.max_entries:
                # Never evict live limits: flooding identities cannot reset a ban.
                return None, 60
            if self._active >= self.max_active:
                return None, 1
            for key in keys:
                state = self._states.setdefault(key, _State(now, now))
                state.active += 1
                state.last_seen = now
            self._active += 1
            return keys, 0

    def finish(self, token, success):
        with self._lock:
            now = self.clock()
            retry = 0
            for key in token:
                state = self._states[key]
                state.active -= 1
                state.last_seen = now
                if success:
                    if key[0] == 'user':
                        state.failures = 0
                        state.blocked_until = 0
                        state.window_start = now
                else:
                    state.failures += 1
                    limit, cooldown = self._policy(key)
                    if state.failures >= limit:
                        state.blocked_until = max(state.blocked_until, now + cooldown)
                    if state.blocked_until > now:
                        retry = max(retry, math.ceil(state.blocked_until - now))
            self._active -= 1
            return 0 if success else retry
