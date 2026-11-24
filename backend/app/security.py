"""Small in-process controls for a single-instance personal demo."""
from collections import OrderedDict, deque
import hashlib
import hmac
import secrets
import threading
import time


class RateLimiter:
    def __init__(self, max_keys=4096):
        self.buckets = OrderedDict()
        self.lock = threading.Lock()
        self.max_keys = max_keys

    def check(self, keys, limit, window=60, now=None):
        if not limit:
            return 0
        now = time.monotonic() if now is None else now
        with self.lock:
            buckets = []
            for key in dict.fromkeys(keys):
                bucket = self.buckets.setdefault((key, window), deque())
                self.buckets.move_to_end((key, window))
                while bucket and bucket[0] <= now - window:
                    bucket.popleft()
                buckets.append(bucket)
            retry = max((max(1, int(window - (now - b[0])) + 1) for b in buckets if len(b) >= limit), default=0)
            if not retry:
                for bucket in buckets:
                    bucket.append(now)
            while len(self.buckets) > self.max_keys:
                removable = next((key for key in self.buckets if not key[0].endswith(':global')), None)
                if removable is None:
                    break
                del self.buckets[removable]
            return retry


def owner_id(token):
    return hashlib.sha256(token.encode()).hexdigest()


_SIGNING_KEY = secrets.token_bytes(32)


def file_signature(job_id, owner, expires):
    return hmac.new(_SIGNING_KEY, f'{job_id}:{owner}:{expires}'.encode(), hashlib.sha256).hexdigest()


def valid_file_signature(job_id, owner, expires, token):
    if not isinstance(token, str) or len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        return False
    if not expires or expires < time.time() or expires > time.time() + 310:
        return False
    return hmac.compare_digest(file_signature(job_id, owner, expires), token or '')
