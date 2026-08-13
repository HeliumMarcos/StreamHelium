// Behaviour tests for the Cloudflare Worker. Run with:
//   node --test tests/
//
// The Worker is plain ESM with no dependencies beyond the platform's fetch,
// so a stubbed global fetch is enough to exercise it outside Cloudflare.
//
// cf_proxy.js is copied to a .mjs temp file before importing: there is no
// package.json at the repo root (adding one risks confusing Vercel's Python
// build), so Node would otherwise read a bare .js as CommonJS and refuse the
// `export default`.

import test from "node:test"
import assert from "node:assert/strict"
import { copyFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const root = join(dirname(fileURLToPath(import.meta.url)), "..")
const copy = join(tmpdir(), `cf_proxy.${process.pid}.mjs`)
copyFileSync(join(root, "cf_proxy.js"), copy)
const worker = (await import(pathToFileURL(copy).href)).default

const ENV = {
  TOKEN_ENDPOINT: "https://addon.example/internal/drive-token",
  TOKEN_ENDPOINT_SECRET: "s3cr3t",
}

// The Worker caches access tokens per account for the life of the isolate,
// and importing the module once means every test shares that cache. Give
// each test its own account so one test's cached token can't decide whether
// the next one reaches the token endpoint.
let accountCounter = 0
function nextAccount() {
  accountCounter += 1
  return `11111111-1111-1111-1111-${String(accountCounter).padStart(12, "0")}`
}
function nextUrl() {
  return `https://w.dev/proxy/${nextAccount()}/load/FILEID/Movie.mkv`
}

// ACCOUNTS is parsed once per isolate and memoized - right in production,
// where the secret can only change via a redeploy, but it means the legacy
// tests can't each pass their own ACCOUNTS. They share this one.
const LEGACY_ENV = {
  ACCOUNTS: JSON.stringify({
    "legacy-ok": { client_id: "c", client_secret: "s", refresh_token: "r" },
    "legacy-stale": { client_id: "c", client_secret: "s", refresh_token: "stale" },
    "legacy-fallback": { client_id: "c", client_secret: "s", refresh_token: "r" },
  }),
}

/** Installs a fetch stub and returns the list it records calls into. */
function stubFetch(handler) {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), headers: init.headers || {}, method: init.method })
    return handler(String(url), init)
  }
  return calls
}

function happyPath(url, init) {
  if (url.includes("/internal/drive-token/")) {
    return new Response(
      JSON.stringify({ access_token: "tok-from-addon", expires_in: 3000 }),
      { status: 200, headers: { "content-type": "application/json" } },
    )
  }
  const ranged = init.headers && init.headers.Range
  return new Response("VIDEOBYTES", {
    status: ranged ? 206 : 200,
    headers: {
      "content-type": "video/x-matroska",
      "content-length": "10",
      ...(ranged ? { "content-range": "bytes 0-9/100" } : {}),
    },
  })
}

test("a request without Range does not send an empty Range upstream", async () => {
  // The regression that broke playback in every player that fetches the URL
  // directly: `Range: ""` is malformed and Google rejects it outright.
  const calls = stubFetch(happyPath)
  const resp = await worker.fetch(new Request(nextUrl()), ENV)

  assert.equal(resp.status, 200)
  const drive = calls.find((c) => c.url.includes("googleapis"))
  assert.ok(!("Range" in drive.headers))
})

test("the access token comes from the addon, authenticated with the shared secret", async () => {
  const calls = stubFetch(happyPath)
  await worker.fetch(new Request(nextUrl()), ENV)

  assert.equal(calls[0].headers.Authorization, "Bearer s3cr3t")
  const drive = calls.find((c) => c.url.includes("googleapis"))
  assert.equal(drive.headers.Authorization, "Bearer tok-from-addon")
})

test("a Range request is forwarded and its 206 preserved", async () => {
  const calls = stubFetch(happyPath)
  const resp = await worker.fetch(
    new Request(nextUrl(), { headers: { Range: "bytes=0-9" } }),
    ENV,
  )

  assert.equal(resp.status, 206)
  assert.equal(resp.headers.get("content-range"), "bytes 0-9/100")
  assert.equal(calls.find((c) => c.url.includes("googleapis")).headers.Range, "bytes=0-9")
})

test("HEAD returns the headers without a body", async () => {
  stubFetch(happyPath)
  const resp = await worker.fetch(new Request(nextUrl(), { method: "HEAD" }), ENV)

  assert.equal(resp.status, 200)
  assert.equal(resp.headers.get("content-length"), "10")
  assert.equal(await resp.text(), "")
})

test("OPTIONS is answered as a CORS preflight", async () => {
  stubFetch(happyPath)
  const resp = await worker.fetch(new Request(nextUrl(), { method: "OPTIONS" }), ENV)

  assert.equal(resp.status, 204)
  assert.equal(resp.headers.get("access-control-allow-origin"), "*")
})

test("other methods are rejected instead of being treated as GET", async () => {
  stubFetch(happyPath)
  const resp = await worker.fetch(new Request(nextUrl(), { method: "POST" }), ENV)

  assert.equal(resp.status, 405)
})

test("an unknown account surfaces as 404, not a blanket 502", async () => {
  stubFetch((url) =>
    url.includes("drive-token")
      ? new Response("{}", { status: 404 })
      : new Response("x", { status: 200 }),
  )
  const resp = await worker.fetch(
    new Request("https://w.dev/proxy/22222222-2222-2222-2222-222222222222/load/F/n.mkv"),
    ENV,
  )

  assert.equal(resp.status, 404)
})

test("a failing token endpoint is a 502 that names the reason", async () => {
  stubFetch((url) =>
    url.includes("drive-token")
      ? new Response("boom", { status: 500 })
      : new Response("x", { status: 200 }),
  )
  const resp = await worker.fetch(new Request(nextUrl()), ENV)

  assert.equal(resp.status, 502)
  assert.match(await resp.text(), /token endpoint returned 500/)
})

test("the legacy ACCOUNTS secret still works when TOKEN_ENDPOINT is unset", async () => {
  const calls = stubFetch((url) =>
    url.includes("oauth2.googleapis.com")
      ? new Response(JSON.stringify({ access_token: "tok-legacy", expires_in: 3600 }), {
          status: 200,
        })
      : new Response("V", { status: 200, headers: { "content-type": "video/mp4" } }),
  )

  const resp = await worker.fetch(
    new Request("https://w.dev/proxy/legacy-ok/load/F/n.mkv"),
    LEGACY_ENV,
  )

  assert.equal(resp.status, 200)
  assert.ok(calls.some((c) => c.url.includes("oauth2")))
})

test("a stale refresh token in ACCOUNTS reports what Google said", async () => {
  stubFetch((url) =>
    url.includes("oauth2.googleapis.com")
      ? new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 })
      : new Response("V", { status: 200 }),
  )

  const resp = await worker.fetch(
    new Request("https://w.dev/proxy/legacy-stale/load/F/n.mkv"),
    LEGACY_ENV,
  )

  assert.equal(resp.status, 502)
  assert.match(await resp.text(), /invalid_grant/)
})

test("a broken token endpoint falls back to ACCOUNTS instead of failing", async () => {
  // Lets the Worker be pointed at the addon before the addon side is
  // finished: playback keeps working off the old secret meanwhile.
  const calls = stubFetch((url) => {
    if (url.includes("drive-token")) return new Response("nope", { status: 500 })
    if (url.includes("oauth2.googleapis.com")) {
      return new Response(JSON.stringify({ access_token: "tok-legacy", expires_in: 3600 }), {
        status: 200,
      })
    }
    return new Response("V", { status: 200, headers: { "content-type": "video/mp4" } })
  })

  const resp = await worker.fetch(
    new Request("https://w.dev/proxy/legacy-fallback/load/F/n.mkv"),
    { ...ENV, ACCOUNTS: LEGACY_ENV.ACCOUNTS },
  )

  assert.equal(resp.status, 200)
  assert.ok(calls.some((c) => c.url.includes("drive-token")))
  assert.ok(calls.some((c) => c.url.includes("oauth2")))
})
