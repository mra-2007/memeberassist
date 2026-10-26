# MemberAssist frontend

Vite + React 19 + TypeScript (strict) + Tailwind CSS v4.
Run it from the repository root so the backend and frontend share one task runner:

```bash
uv run python manage.py web     # http://localhost:5173
```

`/api` is proxied to the FastAPI backend on `127.0.0.1:8000`, which keeps the browser on a single
origin so the httpOnly authentication cookie stays first-party in development.

| Script | Does |
| --- | --- |
| `npm run dev` | Dev server |
| `npm run build` | Typecheck then production build into `dist/` |
| `npm run lint` | ESLint |
| `npm run format` | Prettier (write) |
| `npm run typecheck` | `tsc` only |

Pages are added in Phase 6: login, member chat (with the Security view), agent console and the
admin AI-stack status page.
