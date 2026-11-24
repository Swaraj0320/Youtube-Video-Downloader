# TubeClip By Swaraj

A YouTube, Pinterest, and Instagram downloader, MP3 extractor, and clip editor for macOS. Paste any supported platform into the same link field; Pinterest and Instagram support photos and videos, while Instagram reels support video and audio extraction. Built with React, TypeScript, Vite, Tailwind CSS, Lucide, FastAPI, yt-dlp, and FFmpeg. Local use requires no paid API or account. The optional personal demo runs a Vercel Hobby frontend and Render Free Docker backend, with DNS managed at Hostinger.

## Start

Requirements: Python 3.11+ and Node.js 22.12+ (Node 24 recommended), npm, an internet connection, and enough free disk space. FFmpeg and FFprobe are installed **inside this project** using npm packages; no Homebrew or system-wide installation is needed. The setup is verified on this Apple Silicon Mac with Python 3.14 and Node 24.

From Terminal:

```bash
cd "$HOME/Desktop/YOUTUBE VIDEO DOWNLOADER"
./setup.sh
./start.sh
```

Setup installs the Python virtual environment, JavaScript packages, and local media binaries, then builds the frontend. You only need setup initially or after changing dependencies. `start.sh` launches the API and frontend, checks both, and prints the address:

**http://127.0.0.1:5173**

Keep that terminal open. Press **Ctrl+C** to stop both servers. If TubeClip is already running, `start.sh` prints its address and reuses it. If another application is using either port, stop that conflicting server before launching TubeClip. Both servers bind to this computer's loopback interface. This command starts the local development application. See the separate deployment instructions below for the public personal demo.

## Use

1. Paste a single YouTube video, Pinterest pin (including `pin.it` share links), or public Instagram post/reel URL and click **Analyze Link**, or press Enter. The Paste button uses the browser clipboard permission; use Command+V if permission is unavailable.
2. Check the title, channel, duration, and thumbnail. Select **Best Available** or a resolution actually returned by yt-dlp. Quality is never upscaled or silently reduced.
3. For videos, choose **Video** or **MP3 Audio**. Pinterest and Instagram photos automatically show **Photo** with available pixel dimensions and no clip controls. Audio-only is available for videos with an audio track; silent videos can still be saved. MP3 uses the best accessible audio and high-quality VBR conversion; conversion cannot improve the source recording.
4. For the full video, leave both timestamp inputs empty. For a clip, enter both as `HH:MM:SS` (for example `00:04:00` to `00:04:50`), or drag the two timeline handles. The handles also support keyboard arrows when focused. The end must be after the start and within the video duration.
5. **Preview Clip** loads the selected interval in the embedded player. YouTube must allow embedding and playback; your browser may require pressing Play. Downloading and timestamp selection are independent of preview availability. **Reset to Full Video** clears both timestamps.
6. Click **Download**. Transfer percentages come from yt-dlp and apply to each source stream; a percentage can reset when the audio stream begins. Processing phases have an indeterminate indicator. Only verified finished output is marked ready.
7. The browser download starts automatically as soon as processing finishes—no second click is needed. If you need another copy or the browser blocks the automatic download, use **Download again** on the same primary button; this reuses the finished file without processing again. Changing the quality, format, or clip starts a new job on the next Download click. In local development, the processed original is retained in the project's `downloads/` folder. Hosted demo files expire after 15 minutes by default. Your browser may save an additional copy to its configured Downloads folder. Clips include timestamps in their names; every file has a collision-resistant suffix.
8. Paste another link and analyze it when the current job finishes. For Instagram carousel posts, use the item selector to choose a photo or video within the same post. Failed/cancelled downloads can be retried. Cancellation terminates the job's subprocess group and removes its working files.

## Appearance

TubeClip uses a fixed dark theme with charcoal surfaces, off-white text, and red actions and highlights. The existing layout, animations, spacing, original flower logo, and locally served Manrope and Newsreader fonts are retained. The app always opens in dark mode and has no theme switch or saved theme preference.

## Processing and limits

- Instagram accepts public `/p/`, `/reel/`, `/reels/`, and `/tv/` links. Available photo and video resolutions are listed without upscaling, with MP3 enabled only when audio is present. Carousel items share the same editor and can be selected from the post item control. Profiles, stories, private posts, and content blocked from anonymous access are not supported. Instagram sometimes limits public access; the app reports the restriction rather than requesting credentials or bypassing it.
- Pinterest uses the same source field and download controls as YouTube. Photos retain their source file format and pixels, with only resolutions returned by Pinterest offered. Video qualities include available direct and HLS formats. MP3 uses the accessible source audio; photos and silent videos cannot provide audio. Photo files are verified against the selected width and height. Pinterest video preview uses the cover image; timestamp clipping remains available. Pinterest boards and multi-item story pins are not supported.
- Single videos or photo pins only. Video links containing playlist/radio parameters such as `list=`, `index=`, or `start_radio=1` are accepted; those parameters are stripped and only the selected video is processed. Playlist-only links are rejected. Ongoing live streams are rejected. Completed `/live/` and `/shorts/` links are supported when accessible.
- To reduce download startup time, analyzed video formats and source headers are cached on the server and passed directly to the downloader. Cached metadata is refreshed after ten minutes or once after an expired-link error. Four media fragments can transfer concurrently locally, or two in production when supported. Photos stream directly without a downloader subprocess. Compatible MP4s skip remuxing, and completed selections are reused while the app is running. Verification still checks the actual file, resolution, duration, and audio requirements before success. The browser starts saving once a complete verified file is ready; network transfer, large files, and audio/clip encoding still take time.
- High-resolution video-only streams are downloaded together with audio and merged. Full videos preserve the video stream without re-encoding; incompatible MP4 audio is converted to AAC. MP4 is preferred; if the source codecs cannot be remuxed to MP4, the original source container is retained and its actual extension is shown.
- Clips use yt-dlp's `--download-sections` and `--force-keyframes-at-cuts`, invoking FFmpeg to re-encode for accurate boundaries. Source/network seek support may affect how much media must be transferred. This takes more CPU than a keyframe-only cut, especially at 4K. MP3 conversion processes only the selected clip, not the entire original.
- Before success, FFprobe checks duration, the requested video height, audio presence, and output type. A duration discrepancy over 1.5 seconds is rejected. Tests use tighter tolerances on generated samples.
- Local defaults: two concurrent jobs and two metadata analyses are allowed. The overall local job deadline is 30 minutes with a 20 GB per-source-stream size limit; the separate production demo budgets below are much smaller. At least 512 MB free space is required before starting. Keep substantially more space free for large sources, intermediate streams, and output.
- Analysis expires after one hour. Job status and file links are held in memory; after restarting, existing files remain in `downloads/` and can be opened from Finder. Old job links will no longer work. At most 200 local jobs are retained in memory, with terminal job records expiring after two hours. Local completed files are retained unless file cleanup is explicitly configured.
- Working folders are isolated under `temp/job-<id>/` and removed on completion, failure, or cancellation. Startup removes abandoned working folders older than 24 hours. After a forced termination, newer abandoned folders can remain until a later startup.
- Browser codec support varies; a downloaded AV1/VP9 MP4 may require a compatible media player. Full video downloads preserve source codecs. Restricted, private, DRM-protected, and inaccessible content is not bypassed. Network and YouTube changes can affect extraction.

## Troubleshooting

**Engine unavailable / missing FFmpeg:** Run `./setup.sh` again, then restart. Check `http://127.0.0.1:8000/api/health` for dependency status.

**YouTube extraction fails:** Check that the video is publicly accessible, try a different single video, and update the extractor and its matching EJS scripts:

```bash
.venv/bin/python -m pip install -U "yt-dlp[default]"
```

Restart TubeClip afterward. Node is explicitly enabled as yt-dlp's JavaScript runtime. EJS scripts are installed via the `default` dependency group as described in the [official yt-dlp EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). We do not import browser cookies or attempt to bypass restrictions. YouTube may temporarily block access even to public videos.

**Download fails:** Check the backend terminal for technical logs, your network connection, and available disk space. Analyze again if a format or analysis has expired. A failed job never becomes a successful downloadable file.

**Player unavailable:** Some videos disallow embedding or playback in particular browsers. Select the clip using the timeline and time inputs; an embedded player is not required for processing.

**Long clips/4K are slow:** Precise cutting requires encoding. Leave the timestamps empty for a full download without the clip re-encoding cost.

## Verify / develop

```bash
# Run backend logic, API, and real media pipeline tests.
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q

# Type-check and compile the frontend.
cd frontend
npm test
npm run build
```

The tests generate an eight-second video and sine-wave audio locally, run real FFmpeg conversions and separate-stream merging, then exercise yt-dlp jobs through a local fixture server for full/clip video and MP3. They also cover exact resolution selection, invalid URLs/timestamps, unavailable dependencies, output filenames/serving, and cancellation. The fixture server is test-only; the public API accepts only validated YouTube video, Pinterest pin/share, and Instagram post/reel URLs. Pinterest tests also cover photo resolution selection, a real image download and MIME type, silent-video processing, missing codec metadata, and unsafe share redirects.

`frontend/package-lock.json` and `tools/package-lock.json` record JavaScript dependencies. `backend/requirements.lock.txt` records the verified Python environment; install it with `.venv/bin/python -m pip install -r backend/requirements.lock.txt` when exact reproducibility is needed. Normal setup uses updateable version ranges so yt-dlp can receive extractor fixes.

## Responsible use

Only download content you own or are authorized to save, and follow applicable terms and permissions. No DRM circumvention, authentication bypass, or access-control evasion is implemented.


## Deployment: Vercel Hobby + Render Free + Hostinger DNS

This architecture is a **production-style personal demo, not an unlimited production downloader**. The React layout, animations, theme, timeline controls, image/video quality selection, clipping, and MP3 conversion are unchanged. Explicit hosted limits can reject a large source or long clip; the app does not silently downgrade quality, remove audio, or report truncated output as success.

### 1. Prepare the repository locally

Run the checks below. Review the changes, then commit/push them yourself when ready; none of the supplied scripts automatically push or deploy. Do not include `.env` files, private tokens, browser cookies, virtual environments, local binaries, downloaded media, or `node_modules`.

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
npm test --prefix frontend
npm run build --prefix frontend
```

`backend/requirements.txt` contains runtime packages. `backend/requirements-dev.txt` adds pytest for development; `setup.sh` installs that file. The existing lock file records the earlier verified local environment, not a guarantee that an old extractor will keep working on social platforms.

### 2. Deploy only the backend on Render Free

1. In a **free/Hobby workspace**, choose **New → Blueprint**, connect your repository, and select the root `render.yaml`. Alternatively create a **Web Service** with the settings below. Do not choose a paid instance or add a disk/database.
2. Fill `ALLOWED_ORIGINS` with the exact Vercel frontend origin you intend to use, such as `https://your-frontend.vercel.app`. You can reserve/create the Vercel project first, or use the intended custom frontend origin while provisioning Render. This setting is required: an empty/wildcard/invalid production origin fails startup intentionally.
3. Leave `ALLOWED_HOSTS` empty to use only Render's assigned hostname plus loopback health checks. If using a custom backend domain, add only that API hostname, such as `api.example.com`. Do not put the frontend hostname here.
4. Review that the instance is **Free**, **one instance**, and there is **no disk**. Manually deploy.
5. Open the assigned backend URL's `/api/health`; it should return HTTP 200, `status: "ok"`, and report FFmpeg, FFprobe, Node, yt-dlp, and EJS as available. `/` intentionally returns 404: this service does not host the frontend.

| Render setting | Exact value |
| --- | --- |
| Service type | Web Service |
| Runtime | Docker |
| Plan | Free |
| Root Directory | Empty: repository root |
| Dockerfile Path | `./Dockerfile` |
| Docker build context | Repository root |
| Docker Command | Empty: use the image CMD |
| Health Check Path | `/api/health` |
| Instances / Uvicorn workers | `1` / `1` |
| Automatic deploys | Off |
| Persistent disks / paid addons | None |

The image installs Python 3.12, Node 24, Linux FFmpeg (including FFprobe), yt-dlp with its default dependencies, and yt-dlp-ejs. It runs as a non-root user, writes only to temporary data storage, listens on `0.0.0.0:$PORT` (10000 fallback), and does not copy or build the frontend. Python/Node use the same Debian Bookworm base family. Render supplies `PORT`, `RENDER`, and `RENDER_EXTERNAL_HOSTNAME`; do not replace them with localhost or a frontend URL.

### 3. Deploy only the frontend on Vercel Hobby

1. Import the repository into a **Hobby** project. If Vercel detects multiple applications, select **Import single project beside frontend**, not the backend and not a multi-service import.
2. Use **Root Directory `frontend`**, **Vite**, **Build Command `npm run build`**, **Output Directory `dist`**. The frontend's `vercel.json` repeats those build settings.
3. Set `VITE_API_BASE_URL=https://your-backend.onrender.com` to the actual backend **origin**, without `/api`, a path, credentials, query string, or fragment. Use the API custom domain instead if it is already verified. All health/analyze/download/status/cancel calls and signed file links use this origin.
4. Deploy manually. `VITE_` values are embedded in public JavaScript; they are not secrets. Changing this variable requires a new frontend build/deployment.
5. On Render, set `ALLOWED_ORIGINS` to the exact assigned frontend HTTPS origin and your verified custom frontend origin, comma-separated. Restart/redeploy Render after changing it. Arbitrary Vercel preview domains are intentionally not allowed; add a particular trusted preview origin explicitly if needed.
6. Verify the UI, Analyze, one small video, MP3, photo, clip boundaries, cancellation, and Download again. The browser requests files **directly from Render**, not through Vercel Functions or a Vercel rewrite. This avoids copying large files into frontend memory or routing media through Vercel's function payload limits.

A Vercel build fails clearly if `VITE_API_BASE_URL` is missing. A local build may leave it empty for the existing local `/api` proxy workflow. `frontend/.env.example` documents both cases; Vite loads a local `frontend/.env.local` if you create it, but never commit it.

### 4. Connect your Hostinger-managed domain

All names here are placeholders. Use your own domain and the exact DNS targets shown in your hosting dashboards.

1. In **Vercel → Project → Settings → Domains**, add a frontend subdomain such as `app.example.com`.
2. In **Hostinger → Domains → DNS**, add the **CNAME** record for `app` pointing to the exact target Vercel displays. Add any verification TXT record Vercel requests. Do not assume a fixed Vercel IP/CNAME target.
3. Keep Hostinger's nameservers, existing root website, and email records. Review any conflicting record for the chosen **subdomain only** before changing it.
4. Optional: in **Render → Service → Settings → Custom Domains**, add `api.example.com`. Add Hostinger **CNAME `api` → the assigned Render hostname**, without `https://` or a path. Verify it in Render and wait for managed HTTPS. Remove/replace conflicting records only for that API subdomain as instructed by Render.
5. Add the frontend custom HTTPS origin to Render's `ALLOWED_ORIGINS`. For an API custom domain, add its hostname to `ALLOWED_HOSTS` and update Vercel's `VITE_API_BASE_URL`, then rebuild/redeploy the frontend.
6. Verify both HTTPS addresses after DNS propagation. Frontend DNS points to Vercel; API DNS, if used, points to Render. Registering a domain does not itself host either service.

### Environment variables

Copy examples from [backend/.env.example](backend/.env.example) and [frontend/.env.example](frontend/.env.example). The backend does **not** automatically load a `.env` file. Use Render's environment UI, Docker's `--env-file`, or actual process environment variables. No deployment credentials or shared API secret are needed in the frontend.

| Variable | Where | Production value/default | Purpose |
| --- | --- | --- | --- |
| `VITE_API_BASE_URL` | Vercel build | **Required:** actual backend HTTPS origin | Direct API and file URLs; no `/api` suffix |
| `TUBECLIP_ENV` | Render | `production` | Enables public-demo safeguards |
| `ALLOWED_ORIGINS` | Render | **Required:** exact trusted frontend HTTPS origins, comma-separated | CORS and browser origin policy; no wildcards, credentials, or paths |
| `ALLOWED_HOSTS` | Render | Optional comma-separated extra **API hostnames** | Render's assigned hostname is automatically added; no scheme/port/path/wildcard |
| `TUBECLIP_DATA_DIR` | Render/Docker | `/tmp/tubeclip-data` in Docker | Writable ephemeral download/temp directories |
| `MAX_CONCURRENT_JOBS` | Render | `1` | Maximum active download/conversion jobs (maximum configurable 2) |
| `MAX_CONCURRENT_ANALYSES` | Render | `1` | Maximum simultaneous metadata analyses (maximum configurable 2) |
| `ANALYSIS_TTL_SECONDS` | Render | `900` | Analysis tokens expire after 15 minutes |
| `JOB_TTL_SECONDS` | Render | `1800` | Terminal job records expire after 30 minutes |
| `FILE_RETENTION_SECONDS` | Render | `900` | Completed files expire after 15 minutes; cleanup runs every minute |
| `JOB_TIMEOUT_SECONDS` | Render | `240` | Download/merge/conversion wall-time target; subprocesses are terminated and working files cleaned |
| `MAX_MEDIA_DURATION_SECONDS` | Render | `600` | Entire source must be at most 10 minutes, even for a short clip |
| `MAX_CLIP_SECONDS` | Render | `120` | Selected clip may be at most 2 minutes |
| `MAX_SOURCE_MB` | Render | `128` | yt-dlp per-stream limit plus actual downloaded-source check; watchdog caps combined working files at twice this size |
| `MAX_OUTPUT_MB` | Render | `128` | Final file limit; FFmpeg output-size cap plus post-verification rejection |
| `MAX_STORAGE_MB` | Render | `384` | Combined temporary/completed application storage budget |
| `MIN_FREE_MB` | Render | `256` | Stop/reject jobs when free space falls below this reserve |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Render | `300` | Per socket-peer and per client API request budgets |
| `RATE_LIMIT_ANALYSES_PER_MINUTE` | Render | `6` | Per peer/client and **global** metadata request budget |
| `RATE_LIMIT_DOWNLOADS_PER_MINUTE` | Render | `3` | Per peer/client and **global** job submission budget |
| `MAX_DOWNLOADS_PER_DAY` | Render | `10` | Global new-job budget over a sliding 24 hours; file-transfer requests have twice this budget |
| `PORT` | Render automatic | Render-assigned, Docker fallback `10000` | Public listening port; no need to set manually |
| `RENDER_EXTERNAL_HOSTNAME` | Render automatic | Assigned backend hostname | Automatically accepted Host header |
| `RENDER` | Render automatic | Provider value | Selects production defaults if `TUBECLIP_ENV` is unset |

All `MB` settings use MiB (1024² bytes). Limits are application safeguards, not extra Render capacity. Production rejects zero/disabled cleanup, duration/storage budgets, or rate limits. Defaults in development retain localhost origins, two concurrent jobs/analyses, larger file limits, and no request quotas or automatic completed-file cleanup. Do not run development mode on the public Render service.

### Public-demo safeguards and what they do not guarantee

- Initial links still pass the existing YouTube/Pinterest/Instagram single-media URL validators. Public extraction additionally limits yt-dlp extractors to those platforms and permits downloaded media only on the supported HTTPS CDN families, rejecting private/reserved IP resolutions. Public Pinterest pins that embed a different provider are rejected instead of following arbitrary embedded URLs; local extraction behavior remains available.
- The browser creates a random client identifier, stored locally, and sends it as `X-TubeClip-Client`. Analysis/job lookup, reuse, polling, and cancellation are scoped to its hash. Missing identifiers are rejected in production. This is capability-based job isolation, **not user login** or proof that an HTTP client is human. Do not publish this as an abuse-proof commercial downloader.
- Completed downloads use short-lived (up to five-minute) signed URLs. Anyone holding such a link can save the file until it expires; the frontend refreshes the link before Download again. API responses are non-cacheable, file links use no-referrer, and the Docker command disables access logs so query tokens are not logged by Uvicorn. Provider logs/settings remain your responsibility.
- CORS accepts exact origins only, with no cookie credentials. **CORS is not server authentication**: command-line clients can request public media if they comply with the client ID and quota rules. Quotas apply globally as well as by client, so rotating IDs does not increase the expensive-operation budget.
- Incoming API JSON is capped at 8 KB, including requests without Content-Length. Worker count, job history, analysis cache, file size, working storage, duration, and request counts are bounded. Cleanup never traverses symlinked output files. Startup discards orphaned production working directories; local stale-directory cleanup remains conservative.
- Quotas, job ownership records, and signing keys are in one process's memory. They reset on a service restart and are not a distributed abuse-prevention or billing system. Forwarded client-IP headers are not trusted; Render/shared proxies may make per-peer limits stricter than an individual-user IP limit. Keep exactly one instance/worker.

### Render Free limitations: no guarantees for large/long media

Render explicitly describes free compute as suitable for demos, not production. The documented free instance has **0.1 CPU and 512 MB RAM**. Installing FFmpeg does not mean a large 4K/8K download or precise re-encode will finish within its CPU, memory, disk, or time budgets. No 4K capability is silently removed from the interface, but those jobs can be rejected, time out, or run out of memory. Start with small public photos and short 360p/720p clips. Full downloads can be cheaper to process than clips because compatible streams are retained without re-encoding; this does not guarantee speed or success.

Free services sleep after 15 idle minutes and may take about a minute to wake up. They may restart, and ephemeral files disappear on restart/redeploy/spin-down. In-memory analyses/jobs and signing keys always disappear on process restart. There is no resume-after-restart queue, persistent paid disk, separate paid worker, or keep-alive trick. Save ready files promptly.

The watchdog samples working storage and job time, while network metadata requests and FFprobe have their own bounded subprocess timeouts. Cleanup/cancellation can therefore take a little longer than the nominal job deadline, and sampled disk checks are not a filesystem-level quota. The OS/provider can terminate the process before an application error is returned when memory is exhausted.

YouTube, Instagram, and Pinterest can block cloud/data-center IP addresses, throttle extraction, require login, or change their formats. A successful local extraction does not establish that it will work on Render. This project does not supply cookies, paid proxy services, authentication bypasses, or access-control evasion to work around those restrictions.

**Free-only billing:** `plan: free` and Vercel Hobby are the only configured services. No paid disk, instance, database, or addon is defined. However, Render's documentation says bandwidth/build usage may incur extra charges when a payment method is present, or services can be suspended when it is absent/exhausted. Review the workspace's included usage and spend limits; do not enable paid resources or add a payment method solely for this demo. Application quotas are not a guarantee against provider charges, particularly after restarts. Stop the service before exceeding your free allowance. This setup does not promise unlimited bandwidth or perpetual free uptime.

Official references (checked for this preparation): [Render Free](https://render.com/docs/free), [Render web services](https://render.com/docs/web-services), [Render blueprint compute limits](https://render.com/docs/blueprint-spec), [Vite on Vercel](https://vercel.com/docs/frameworks/frontend/vite), [Vercel custom domains](https://vercel.com/docs/domains/working-with-domains/add-a-domain), [Render DNS](https://render.com/docs/configure-other-dns).

### Local container smoke test (requires Docker)

```bash
docker build -t tubeclip-api .
docker run --rm -p 10000:10000 \
  -e ALLOWED_ORIGINS=https://app.example.com \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  tubeclip-api
# In another terminal:
curl -i http://127.0.0.1:10000/api/health
```

This is a local test, not a deploy command. `app.example.com` is only an example allowed frontend origin; replace it when testing your real Vercel frontend. Do not expose the development Vite server as your production backend.
