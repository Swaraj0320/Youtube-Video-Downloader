# Verification — 15 November 2026

## Passed

- `./setup.sh`: virtual environment/dependencies, project-local FFmpeg/FFprobe, extractor version check, and production frontend build.
- `./start.sh`: API and frontend start, HTTP readiness checks, loopback binding, and clean shutdown/restart.
- `npm run build` in `frontend/`: TypeScript and Vite production build.
- `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q`: **28 passed**. One non-failing upstream Starlette/httpx deprecation warning.
- Health endpoint: `status: ok`; FFmpeg, FFprobe, Node, and yt-dlp EJS detected.
- Frontend and proxied health/file endpoints return HTTP 200.

## Real media tests

Generated local eight-second media exercised complete and three-second clipped video/MP3 output through real yt-dlp and FFmpeg jobs. Clip duration tolerances were below 0.2 seconds. Tests covered exact format selection, video-only plus audio-only selection/merging, output stream verification, AAC compatibility conversion, safe filenames, API errors, timestamp validation, cancellation before processing, cleanup, and attachment serving.

Live YouTube metadata was retrieved for the Blender channel's Big Buck Bunny video (`aqz-KE-bpKQ`), returning 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, and 2160p.

Live download checks:

| Selection | Measured result | File endpoint |
| --- | --- | --- |
| 1080p, 00:00:10–00:00:15 | 5.002 s, H.264 video at 1080p with audio | HTTP 200, MP4 attachment |
| MP3, 00:00:10–00:00:15 | 5.040 s, MP3 audio only | HTTP 200, MP3 attachment |
| 2160p, 00:00:20–00:00:22 | 2.003 s, 3840×2160 H.264 with AAC audio | HTTP 200, MP4 attachment |

The 1080p check preceded the audio compatibility improvement; the final 2160p check confirms AAC output with the current implementation. Completed example files remain in `downloads/`.

## Verification limits

Browser automation was unavailable (the browser access host could not be reached). Visual layout, mobile rendering, clipboard permission behavior, drag interactions, and embedded YouTube playback were not verified in a browser. The frontend compiles and serves successfully. The player uses the official YouTube IFrame API for playback boundaries and error fallback; access and autoplay restrictions still depend on YouTube/the browser.

Full-length downloads were verified with generated local media, not a complete external YouTube video. External download tests used short intervals. Private/restricted/DRM content is deliberately unsupported. Larger files and longer 4K encodes were not stress-tested.

## Interface redesign

The interface was renamed TubeClip By Swaraj and redesigned with the supplied #054A91 / #3E7CB1 / #81A4CD / #DBE4EE / #F17300 palette. The layout is fluid across the screen, with responsive grids, larger text and controls, a compact analyzed-video header, and the existing automatic download flow retained. TypeScript/Vite build and live HTTP checks for the page, favicon, frontend modules, stylesheet, and proxied backend health pass. Interactive browser/visual verification remains unavailable in this session.

## Violet palette and theme update

The interface now uses the supplied #242038 / #9067C6 / #8D86C9 / #CAC4CE / #F7ECE1 palette with explicit light and dark surface, text, border, status, and control colors. Dark mode is the default; the header theme toggle persists the browser preference and restores it before the UI loads. Manrope Variable and Newsreader Variable Italic are locally bundled. Production build, local font/asset serving, and proxied health checks pass. Browser visual/theme-interaction verification remains unavailable.

## Original flower branding and pale canvas

The original flower was extracted from the second PDF page using its existing alpha mask, preserving the artwork without AI regeneration. Transparent square, header, favicon, and touch-icon assets were exported and their alpha channels checked. Header/footer and browser icons now use the flower. The default page canvas is linen; dark mode uses a pale-slate canvas around dark panels. Build and HTTP asset checks pass.

## Deployment preparation — 16 November 2026 (current)

This section supersedes the earlier deployment assumptions while retaining the historical local/visual records above.

- Backend: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q` — **105 passed**; one existing upstream Starlette/httpx deprecation warning. Includes the original real yt-dlp/FFmpeg local fixtures and new production CORS, ownership, signing, expiry, quota, health, cleanup, watchdog and configuration tests.
- Frontend: `npm test --prefix frontend` — **4 passed** using Node's built-in test runner.
- Frontend: `npm run build --prefix frontend` — TypeScript and Vite production build passed.
- Separate Vercel-style build with `VERCEL=1` and placeholder `VITE_API_BASE_URL=https://backend.example.com` passed; the configured API origin was present in the compiled JavaScript. A Vercel-style build without the variable failed with the intended clear configuration error. The ordinary local build was then restored.
- Python runtime dependencies resolved successfully in a pip dry run for **Python 3.12 / manylinux x86_64**, including matching yt-dlp and yt-dlp-ejs. This checks wheel availability/resolution, not a Linux runtime or cloud extraction.
- Backend is now API-only, with a Render Free blueprint and separate frontend Vercel configuration. No personal hostname is embedded in the deployment files. Example environment files are unignored by Git and contain placeholders only.
- UI CSS and markup layout were not changed by this preparation. The generated CSS asset hash remains `index-Cw-sm0MG.css`, matching the build before this deployment work. No new visual redesign was performed.
- No Git push, cloud deployment, DNS update, paid service, or account change was performed.

Verification limit: **Docker/Podman is not installed here**, so the image could not be built or started locally. A Docker build/start and actual Render extraction remain operator verification steps. Render Free's cold starts, ephemeral state, 0.1 CPU/512 MB limits, potential cloud-IP restrictions, usage quotas and possible overage billing are documented in README.md; large/long/4K jobs are not promised to work.
