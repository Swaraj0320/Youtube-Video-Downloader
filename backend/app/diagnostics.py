"""Bounded extractor diagnostics. Never log metadata JSON or raw commands/headers."""
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import logging
import platform
import re
import shutil
import subprocess

log = logging.getLogger('tubeclip.extractor')


class ExtractionError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        # Safe even if an API handler logs the exception traceback.
        super().__init__(f'Extraction failed ({reason}); see sanitized extractor diagnostics.')


def failure_reason(stderr):
    # Verbose command/config lines contain '--socket-timeout' even on unrelated
    # failures. Classify actual errors/warnings rather than those debug settings.
    text = '\n'.join(line for line in (stderr or '').lower().splitlines()
                     if not line.startswith('[debug]') or 'warning:' in line or 'error:' in line)
    if re.search(r'\b(?:403|429)\b', text) or any(s in text for s in ('sign in', 'login required', 'ip is likely being blocked', 'not a bot')):
        return 'access_refused'
    if 'failed to extract any player response' in text or 'all player responses are invalid' in text:
        return 'player_response'
    if any(s in text for s in ('private video', 'video unavailable', 'video is unavailable', 'has been removed', 'age-restricted', 'not available in your country')):
        return 'unavailable'
    if any(s in text for s in ('challenge solving failed', 'error solving', 'error running node', 'no supported javascript runtime')):
        return 'javascript'
    if 'timed out' in text or re.search(r'\btimeout(?:error)?\b', text):
        return 'timeout'
    return 'extractor'


def diagnostic_lines(stderr):
    """Allowlist relevant lines, redact sensitive values, omit JS/input dumps/stacks."""
    result = []
    for line in (stderr or '')[-65536:].splitlines():
        if not line.lstrip().startswith(('[debug]', 'WARNING:', 'ERROR:', '[youtube]',
                                        'TypeError:', 'ReferenceError:', 'SyntaxError:')):
            continue
        if not any(marker in line for marker in (
            'yt-dlp version ', 'Python ', 'JS runtimes:', 'JS Challenge Providers:',
            'Using challenge solver ', 'Solving JS challenges ', 'WARNING:', 'ERROR:',
            'TypeError:', 'ReferenceError:', 'SyntaxError:', 'Error running node process',
        )):
            continue
        # Headers/configuration/payloads may include arbitrary credentials: omit the
        # entire line rather than trying to guess every possible secret format.
        if re.search(r'cookie|authorization|bearer|password|secret|token|proxy|headers|command.line|https?_proxy|challenge[s]?\s*[=:]|player_response\s*[=:]', line, re.I):
            continue
        line = re.sub(r'https?://[^\s<>"\']+', '[url]', line)
        line = re.sub(r'\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b', '[email]', line)
        line = re.sub(r'(?:[A-Za-z]:\\|/)(?:[^\s:<>"\']+/)*[^\s:<>"\']+', '[path]', line)
        line = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[ip]', line)
        line = re.sub(r'(?<!\w)(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]+(?:%[\w.-]+)?', '[ip]', line)
        line = re.sub(r'\b[A-Za-z0-9_-]{11}\b(?=:)', '[video]', line)
        line = re.sub(r'\b[A-Za-z0-9_=-]{40,}\b', '[value]', line)
        line = ''.join(c for c in line if c.isprintable())
        result.append(line[:600])
    return result[-24:]


@lru_cache(maxsize=1)
def runtime_details():
    versions = {}
    for name in ('yt-dlp', 'yt-dlp-ejs'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'missing'
    node = shutil.which('node')
    versions['node'] = 'missing'
    if node:
        try:
            output = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=3)
            versions['node'] = output.stdout.strip() if re.fullmatch(r'v\d+\.\d+\.\d+\s*', output.stdout or '') else 'unrecognized'
        except (OSError, subprocess.SubprocessError):
            versions['node'] = 'unavailable'
    return {'python': platform.python_version(), 'os': platform.system(), 'arch': platform.machine(), **versions}


def log_extraction(url, stderr, *, production, returncode, elapsed, reason=None):
    event = {'event': 'extraction_failed' if reason else 'extraction_warning',
             'source_ref': hashlib.sha256(url.encode()).hexdigest()[:12],
             'production': production, 'returncode': returncode, 'seconds': round(elapsed, 3),
             'reason': reason or 'javascript', 'runtime': runtime_details(),
             'diagnostics': diagnostic_lines(stderr)}
    # JSON escaping prevents multiline log injection; no stdout, command, env or URL.
    log.log(logging.ERROR if reason else logging.WARNING, '%s', json.dumps(event))
