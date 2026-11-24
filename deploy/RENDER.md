# TubeClip deployment: Render backend + Vercel frontend

The canonical instructions and complete environment-variable reference are in
[the README](../README.md#deployment-vercel-hobby--render-free--hostinger-dns).

- Render: **Free Web Service**, repository root, Docker runtime, root `Dockerfile`, `/api/health`, one instance.
- Vercel: **Hobby**, single frontend project, root `frontend`, preset Vite, `npm run build`, output `dist`.
- Hostinger: DNS only. Point the frontend subdomain to the exact target Vercel provides; optionally point a separate API subdomain to Render. Keep existing root/email records and nameservers.
- `ALLOWED_ORIGINS` on Render lists exact frontend HTTPS origins. `VITE_API_BASE_URL` on Vercel is the backend HTTPS origin, without `/api`. They serve different purposes and are not interchangeable.
- `ALLOWED_HOSTS` contains optional extra API hostnames. Render's assigned `RENDER_EXTERNAL_HOSTNAME` is added automatically.

The supplied blueprint has **no paid instance, disk, database, or addon**, and automatic deployment is disabled. Nothing has been pushed or deployed automatically. Free compute is for a small personal demo; cold starts, ephemeral state, quotas, platform access restrictions, CPU/RAM limitations, and provider usage charges still apply. Follow the README's free-only billing notes before manually deploying.
