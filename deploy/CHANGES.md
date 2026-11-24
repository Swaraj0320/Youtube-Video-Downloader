# File-by-file deployment preparation

This preparation is local only. It does not push, create hosting resources, update DNS, or deploy.
Existing uncommitted UI/social-platform work was retained. `frontend/src/style.css`,
`frontend/index.html`, `frontend/src/YouTubePreview.tsx`, the timeline markup, existing
animations, and the Instagram adapter were not changed in this deployment-preparation pass.

| File | Deployment change and purpose |
| --- | --- |
| `Dockerfile` | Backend-only Debian image with Python 3.12, Node 24, FFmpeg/FFprobe and runtime Python dependencies; non-root execution; Render `PORT`; one Uvicorn worker with bounded HTTP concurrency. |
| `.dockerignore` | Excludes frontend, local binaries, dependency folders, media, tests, Git data, and environment files from the runtime build context. |
| `render.yaml` | Render Free Web Service only, no disk/database/addon, no automatic deploys; explicit demo budgets and required configurable origins. |
| `backend/app/config.py` | Validates exact production origins/hosts and numeric limits; adds Render's assigned hostname; preserves localhost development defaults; rejects disabled production safety budgets. |
| `backend/app/security.py` | Bounded in-process rate counters, owner-token hashing, and expiring HMAC-signed file links; global counters cannot be displaced by rotating client IDs. |
| `backend/app/main.py` | Environment host/origin policy, explicit CORS, client ownership checks, small request bodies, per-client/peer/global budgets, isolated signed downloads, bounded worker/storage/time limits, TTL pruning and file cleanup. API-only service, dependency-sensitive health endpoint (503 when degraded). |
| `backend/app/media.py` | Shares validated settings; Linux binaries fall back to system PATH; production checks supported CDN hosts/private IPs and source duration; constrains extractors and FFmpeg work without changing the selected resolution or conversion/clip algorithms. |
| `backend/requirements.txt` | Runtime-only packages, explicit yt-dlp default/EJS dependencies; pytest is no longer installed in the server image. |
| `backend/requirements-dev.txt` | Adds pytest on top of runtime requirements for local verification. |
| `backend/.env.example` | Placeholder-only backend configuration with every supported operator setting. |
| `setup.sh` | Installs the development requirements so a fresh local setup still includes tests. |
| `frontend/src/api-url.ts` | Shared validation/composition of backend URLs; rejects unsafe schemes, credentials, paths, query strings, and accidental `/api` base suffixes. |
| `frontend/src/api.ts` | Uses `VITE_API_BASE_URL` for every API call; generates a client identifier; sends uncredentialed cross-origin requests; handles API and cold-start error responses. |
| `frontend/src/main.tsx` | Calls the shared API client and refreshes signed file URLs before saving; includes a bounded initial health retry for Render cold starts. No layout/style/animation/timeline changes. |
| `frontend/src/vite-env.d.ts` | Types the public build-time API URL variable. |
| `frontend/vite.config.ts` | Keeps the existing localhost Vite proxy and validates production configuration; Vercel builds fail early if the backend origin is missing. |
| `frontend/vercel.json` | Vite build/output settings for a single frontend project rooted at `frontend`. No Python services or media rewrites. |
| `frontend/.env.example` | Empty local-proxy default and placeholder production API origin; warns that VITE values are public. |
| `frontend/package.json` | Adds a Node built-in TypeScript test command without adding dependencies. |
| `frontend/tests/api.test.ts` | Tests local/production URL composition, every API operation, client headers, signed file addressing, unsafe URLs, and cold-start/JSON errors. |
| `backend/tests/test_hosting.py` | Retains hosting checks, replaces the obsolete combined-frontend assertion with the requested API-only architecture, and uses a generic example host. |
| `backend/tests/test_deployment.py` | Adds production/CORS import tests, host/origin/config validation, health failures, ownership/signatures, budgets, cleanup/expiry, watchdog, private-CDN rejection, and free/backend-only deployment assertions. |
| `README.md` | Complete Render/Vercel/Hostinger instructions, exact environment/settings reference, security model, default budgets, free-tier/billing limitations, and verification commands. |
| `deploy/RENDER.md` | Replaces the previous combined-service/personal-domain instructions with a short pointer to the new split architecture and generic placeholders. |
| `VERIFICATION.md` | Records current test/build/dependency checks and clearly distinguishes local success from unverified Docker/cloud execution. |
| `deploy/CHANGES.md` | This report, distinguishing this preparation from existing uncommitted project work. |

The existing `backend/tests/test_tubeclip.py` media/logic/API tests remain in place and are run with the new tests. The existing package lock files and local Python lock snapshot are retained; no frontend packages were added. Runtime dependency resolution was also checked for Python 3.12 / Linux x86_64 with pip's dry run. A local Docker engine is unavailable in this environment, so a real Linux image build/start and live cloud extraction are still verification steps for the operator. Nothing here claims that 4K re-encoding or cloud-blocked social links will work on Render Free.
