/**
 * Two proxies, for two backends that both exist in this repository.
 *
 * `/api/*` is the **legacy v1** application in `backend/`, which the
 * `(dashboard)` route group still talks to. It is untouched here: `18-ROADMAP.md`'s
 * cutover and legacy removal is a separate operational step, not part of Phase 12,
 * and breaking a working UI to tidy the tree is not a Phase 12 change.
 *
 * `/api/v2/*` is the v2 API in `oipulse/`, which the `(terminal)` route group uses.
 * `12-API_SPEC.md` §5 specifies `/api/v2/` as the client-visible prefix; the
 * application mounts its routers at the root (`oipulse/api/app.py`), so the prefix
 * is stripped here rather than being absent from the client.
 *
 * `OIPULSE_V2_API_URL` deliberately has **no** `NEXT_PUBLIC_` prefix. Next inlines
 * `NEXT_PUBLIC_*` into the client bundle; this value is read only here, at build and
 * request time on the server, so the backend's real address never ships to a
 * browser and no client-side code can point a request somewhere else (brief §23).
 */
const nextConfig = {
  async rewrites() {
    const legacyBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const v2Base = process.env.OIPULSE_V2_API_URL ?? "http://localhost:8001";
    return {
      // `beforeFiles` so `/api/v2/*` is matched before the broader `/api/*` rule
      // below could swallow it.
      beforeFiles: [
        {
          source: "/api/v2/:path*",
          destination: `${v2Base}/:path*`,
        },
      ],
      afterFiles: [
        {
          source: "/api/:path*",
          destination: `${legacyBase}/api/:path*`,
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
