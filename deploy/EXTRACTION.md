# YouTube extraction investigation (2026-11-25)

The frontend, hosting architecture, DNS, origin policy, quotas, timeouts, URL validation,
resource limits and download verification are unchanged. No cookies, credentials,
proxies, third-party APIs, client overrides or access-control workarounds were added.

## Findings and confidence

- **Confirmed dependency drift:** Docker previously installed open-ended requirements,
  ignoring the existing workstation lock. Local yt-dlp was 2026.10.26 while Render
  reportedly installed 2026.11.23. The runtime now pins 2026.10.26/EJS 0.8.0 as a
  controlled reproduction baseline, not a proven fix for Render network access.
- Both releases declare EJS **0.8.0** in their official default dependencies:
  [October metadata](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/pyproject.toml)
  and [November metadata](https://github.com/yt-dlp/yt-dlp/blob/2026.11.23/pyproject.toml).
  Node 24 is supported; Node must be explicitly enabled, as our command already does.
  See the [official EJS guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
- **Command comparison on macOS:** 2026.10.26 extracted the same example link with
  development flags (~2.19 s) and production flags (~2.24 s). Production changes only
  the extractor allowlist, fragment concurrency (2 versus 4), and FFmpeg thread
  limits. None of those options change the YouTube player client or enable credentials.
  An isolated, temporary 2026.11.23 wheel also extracted that link with production
  flags (~2.35 s), using the existing EJS/Node installation. This was NOT a Linux test.
- **Confirmed solver warning on the local baseline:** the n-challenge step raised
  `TypeError: a.addOnDisposeCallback is not a function`, yet extraction returned
  playable formats. The installed solver scripts match yt-dlp's expected SHA3-512
  hashes, and restricted Node stdin execution succeeds. Installation and runtime
  availability are therefore verified locally; live player-script compatibility is
  still unresolved. The app preserves returned qualities and now logs partial solver
  failures instead of silently hiding warnings. No solver code was patched or
  integrity check disabled. A successful extraction does not prove every format was recovered.
- **Confirmed second production-only bug:** the extractor includes `mhtml` storyboard
  entries from `i.ytimg.com`. Our production download metadata guard correctly rejects
  that host. We now discard entries explicitly identified as audio-less/video-less
  mhtml storyboards before forming download metadata. TubeClip never offered these as
  downloadable videos. Real video/audio URLs still undergo the unchanged CDN, HTTPS,
  port, credential and public-address checks. The allowlist was NOT expanded.
- **Unresolved Render failure:** `Failed to extract any player response` occurs in
  yt-dlp's player-response collection; it is not by itself proof of an EJS mismatch
  or a blocked IP. The new logs distinguish HTTP refusals, missing player responses,
  unavailable media, JavaScript failures and timeouts. The preceding warnings are
  needed to identify the actual cloud failure. The healthy API endpoint checks
  installed dependencies, not live YouTube access.

## Reproducible installation

`backend/requirements.txt` pins the direct dependencies and constrains all transitive
runtime packages using `backend/requirements.lock.txt`. The lock no longer includes
pytest-only packages. Docker copies both files, installs binary wheels, and runs
`pip check`. Local setup uses the same requirements and no longer overwrites the
lock with `pip freeze`.

Node 24.18.0 and the Python 3.12 base image are pinned by Docker Hub manifest digest.
The digests were retrieved from the official registry. These pins do not make the
entire image byte-for-byte reproducible: Debian's FFmpeg/OS packages still come from
the configured Debian repositories at build time. Do not treat the lock as a promise
that an old extractor will continue to support future YouTube changes. Update direct
pins and the runtime lock deliberately, review upstream EJS requirements, then rerun
checks and source tests. Runtime dependency upgrades are not automatic.

## Diagnostics

Analyze uses one verbose, metadata-only subprocess, retaining the existing 90-second
boundary. There is no second extraction on failure. JSON metadata/stdout, raw commands,
environment variables, request/response bodies, traffic dumps and player/challenge
payloads are never logged. Only bounded, allowlisted stderr lines are retained;
URLs, paths, IP addresses, emails and identifiers are redacted, and sensitive
header/cookie/token/config/proxy lines are omitted. Exceptions passed to the API
contain a safe category rather than raw stderr. Do not add `--print-traffic`,
`--dump-pages`, challenge input dumps or browser credentials to production diagnostics.

Look for a JSON `tubeclip.extractor` event named `extraction_failed` or
`extraction_warning`. It includes a hashed source reference, mode, elapsed time,
return code, Python/OS/architecture, yt-dlp/EJS/Node versions and sanitized provider,
solver and failure lines. A warning with return code zero means some formats may be
missing, not that the entire extraction failed. API messages deliberately say cloud
access *may* be refused rather than asserting an IP block without evidence.

## Container verification before a manual Render deployment

Docker is not installed on the development Mac: `/usr/local/bin/docker` points at a
missing Docker.app. A real Linux image build/run and Render test could not be performed
here. Python dependency resolution for **CPython 3.12 / Linux x86-64 binary wheels**
passed; this checks package availability/compatibility, not executable behavior.

On a machine with Docker, from the repository root:

```bash
docker build --pull -t tubeclip-backend:extraction-check .
# This also runs during the image build, as the unprivileged runtime user:
docker run --rm tubeclip-backend:extraction-check python -m app.extraction_probe --self-check

# Replace VIDEO_ID with a public video you are authorized to test.
# This runs the actual app metadata path with production validation, no download.
docker run --rm --memory=512m --cpus=0.1 \
  -e ALLOWED_ORIGINS=https://app.example.com \
  tubeclip-backend:extraction-check python -m app.extraction_probe \
  'https://www.youtube.com/watch?v=VIDEO_ID'
```

The offline check validates the installed EJS hashes against the pinned yt-dlp and
executes Node in the same restricted stdin mode. It does not claim to solve a live
YouTube challenge. The optional URL probe prints only runtime versions, outcome,
qualities and audio availability; failures use the same sanitized logging.

After reviewing the patch, manually rebuild/deploy Render using the existing Docker
service and environment. No new environment variables or domain changes are needed.
Verify `/api/health` reports yt-dlp 2026.10.26 and EJS 0.8.0, then test Analyze once
with the same public source used locally. Inspect the sanitized event if it fails.
A Docker success from a home network cannot prove success from Render's outbound IP.
Render may still be refused or rate-limited by YouTube, and Node on 0.1 CPU may take
longer than it does locally. Dependency pins and healthy startup cannot remove those
restrictions; retain timeouts, quotas and the existing free-host resource limits.

## Verification results

- Backend: 124 tests passed, including existing real FFmpeg/media pipeline tests,
  production security checks and new diagnostics/storyboard regression tests.
- Frontend: four tests passed; TypeScript/Vite production build passed. No frontend
  source files changed.
- `pip check`: no broken requirements. The constrained runtime dependency resolution
  for CPython 3.12 / manylinux x86-64 succeeded with binary wheels only.
- Offline EJS hash and restricted Node self-check: passed on this Mac.
- Full production-mode metadata path on macOS: passed for a short public video;
  returned its available qualities and audio, while logging the n-challenge warning.
  The earlier long example was correctly rejected by the unchanged 600-second limit.
- Docker execution and Render outbound extraction: not tested here; Docker is missing
  and no cloud deployment was performed. The original Render fatal error is not
  claimed resolved, and the live n-challenge TypeError remains visible and unresolved.
