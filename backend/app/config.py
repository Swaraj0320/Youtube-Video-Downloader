"""Validated settings shared by the API and media subprocesses."""
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit


def number(name, default, minimum=0, maximum=2**31 - 1):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        raise ValueError(f'{name} must be an integer.') from None
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}.')
    return value


def hosts(value):
    result = set()
    for host in value.split(','):
        host = host.strip().lower()
        if not host:
            continue
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '..' in host:
            raise ValueError('ALLOWED_HOSTS must contain exact hostnames, without schemes, ports, paths, or wildcards.')
        result.add(host)
    return result


def origins(value, production):
    result = set()
    for origin in value.split(','):
        origin = origin.strip().rstrip('/')
        if not origin:
            continue
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('ALLOWED_ORIGINS contains an invalid port.') from None
        local = parsed.hostname in ('localhost', '127.0.0.1')
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or '*' in origin
                or (parsed.scheme != 'https' and (production or not local)) or port == 0):
            raise ValueError('ALLOWED_ORIGINS must contain exact HTTPS origins (HTTP localhost is allowed only in development).')
        result.add(origin)
    if production and not result:
        raise ValueError('Set ALLOWED_ORIGINS to the exact Vercel/custom frontend HTTPS origin before starting production.')
    return result


@dataclass(frozen=True)
class Settings:
    production: bool
    allowed_hosts: frozenset
    allowed_origins: frozenset
    jobs: int
    analyses: int
    analysis_ttl: int
    job_ttl: int
    retention: int
    timeout: int
    max_duration: int
    max_clip: int
    source_bytes: int
    output_bytes: int
    storage_bytes: int
    free_bytes: int
    requests_per_minute: int
    analyses_per_minute: int
    downloads_per_minute: int
    downloads_per_day: int

    @classmethod
    def from_env(cls):
        mode = os.environ.get('TUBECLIP_ENV', 'production' if os.environ.get('RENDER') else 'development')
        if mode not in ('development', 'production'):
            raise ValueError('TUBECLIP_ENV must be development or production.')
        prod = mode == 'production'
        public = hosts(os.environ.get('ALLOWED_HOSTS', ''))
        public |= hosts(os.environ.get('RENDER_EXTERNAL_HOSTNAME', ''))
        allowed = {'localhost', '127.0.0.1'} | public
        if not prod:
            allowed.add('testserver')
        origin_list = os.environ.get('ALLOWED_ORIGINS', '' if prod else 'http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:8000')
        mb = 1024**2
        result = cls(prod, frozenset(allowed), frozenset(origins(origin_list, prod)),
            number('MAX_CONCURRENT_JOBS', 1 if prod else 2, 1, 2),
            number('MAX_CONCURRENT_ANALYSES', 1 if prod else 2, 1, 2),
            number('ANALYSIS_TTL_SECONDS', 900 if prod else 3600, 60, 3600),
            number('JOB_TTL_SECONDS', 1800 if prod else 7200, 60, 86400),
            number('FILE_RETENTION_SECONDS', 900 if prod else 0, 0, 86400),
            number('JOB_TIMEOUT_SECONDS', 240 if prod else 1800, 5, 1800),
            number('MAX_MEDIA_DURATION_SECONDS', 600 if prod else 0, 0, 86400),
            number('MAX_CLIP_SECONDS', 120 if prod else 0, 0, 86400),
            number('MAX_SOURCE_MB', 128 if prod else 20480, 1, 20480) * mb,
            number('MAX_OUTPUT_MB', 128 if prod else 20480, 1, 20480) * mb,
            number('MAX_STORAGE_MB', 384 if prod else 0, 0, 102400) * mb,
            number('MIN_FREE_MB', 256 if prod else 512, 1, 102400) * mb,
            number('RATE_LIMIT_REQUESTS_PER_MINUTE', 300 if prod else 0, 0, 10000),
            number('RATE_LIMIT_ANALYSES_PER_MINUTE', 6 if prod else 0, 0, 100),
            number('RATE_LIMIT_DOWNLOADS_PER_MINUTE', 3 if prod else 0, 0, 100),
            number('MAX_DOWNLOADS_PER_DAY', 10 if prod else 0, 0, 10000))
        if prod and not all((result.retention, result.max_duration, result.max_clip, result.storage_bytes, result.requests_per_minute, result.analyses_per_minute, result.downloads_per_minute, result.downloads_per_day)):
            raise ValueError('Production cleanup, duration/storage budgets, and rate limits must be greater than zero.')
        return result


settings = Settings.from_env()
