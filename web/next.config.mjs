/** @type {import('next').NextConfig} */
const repoName = "reelbrain";
// GitHub Actions sets GITHUB_ACTIONS=true during CI builds. Locally
// (npm run dev / a manual build) this stays unset, so `npm run dev` still
// serves at the plain root instead of requiring /reelbrain in every URL.
const isGithubActions = process.env.GITHUB_ACTIONS === "true";

const nextConfig = {
  reactStrictMode: true,
  // GitHub Pages serves plain static files -- no Node server, so no ISR, no
  // route handlers, no middleware. `output: "export"` makes `next build`
  // emit a fully static `out/` directory instead of a server bundle. Every
  // data-driven page in this app is a client component that fetches the
  // public API directly (see src/lib/api.ts) specifically so this works.
  output: "export",
  // A project Pages site (garvbardia.github.io/reelbrain/) is served from a
  // subpath, not the domain root -- without these, every asset URL Next
  // emits (JS chunks, CSS) would 404 by looking for them at the root.
  basePath: isGithubActions ? `/${repoName}` : "",
  assetPrefix: isGithubActions ? `/${repoName}/` : "",
  // Static export has no image optimization server to route through.
  images: { unoptimized: true },
  // GitHub Pages resolves `/library` by looking for a literal `library` file
  // unless it ends in `/` or `.html`; trailingSlash makes Next emit
  // `library/index.html` so directory-style URLs resolve correctly.
  trailingSlash: true,
};

export default nextConfig;
