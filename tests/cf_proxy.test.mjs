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

// --- signed playback URLs --------------------------------------------------

const SIGN_SECRET = "golden-test-secret"
const FAR_FUTURE = 4102444800 // year 2100

const SIGNED_ENV = {
  TOKEN_ENDPOINT: "https://addon.example/internal/drive-token",
  TOKEN_ENDPOINT_SECRET: SIGN_SECRET,
}

/** Same construction the Worker verifies against. The golden-vector test
 *  below is what ties it to what sgd/signing.py actually produces. */
async function hmac(payload, secret = SIGN_SECRET) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  )
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload))
  return btoa(String.fromCharCode(...new Uint8Array(mac)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "")
}

// The Worker caches slot verdicts per viewer+session, so every test needs
// its own viewer for the same reason the token tests need their own account.
let viewerCounter = 0
function nextViewer() {
  viewerCounter += 1
  return `22222222-2222-2222-2222-${String(viewerCounter).padStart(12, "0")}`
}

async function signedUrl(overrides = {}) {
  const account = overrides.account || nextAccount()
  const file = overrides.file || "FILEID"
  const viewer = overrides.viewer || nextViewer()
  const session = overrides.session || "sess-abc"
  const expiry = overrides.expiry || FAR_FUTURE
  const signature =
    overrides.signature ||
    (await hmac(`${account}:${overrides.signedFile || file}:${viewer}:${session}:${expiry}`))

  return `https://w.dev/proxy/${account}/load/${file}/Movie.mkv` +
    `?u=${viewer}&n=${session}&e=${expiry}&s=${signature}`
}

function signedHappyPath(url, init) {
  if (url.includes("/internal/playback/")) {
    return new Response(JSON.stringify({ granted: true }), { status: 200 })
  }
  return happyPath(url, init)
}

test("the Worker's signature matches what sgd/signing.py produces", async () => {
  // Golden vector generated by sgd/signing.py. If this fails, the Python
  // signer and the Worker verifier have drifted and every playback URL is
  // about to be rejected - fix the payload format on both sides rather
  // than updating the constant.
  const payload =
    "11111111-1111-1111-1111-111111111111:FILEID:" +
    "22222222-2222-2222-2222-222222222222:sess-abc:4102444800"

  assert.equal(await hmac(payload), "6w5w36bzXv4JuDUzD3Txsx5faMHUvnp8z4mbt8jYqtI")
})

test("a correctly signed URL plays", async () => {
  stubFetch(signedHappyPath)
  const resp = await worker.fetch(new Request(await signedUrl()), SIGNED_ENV)

  assert.equal(resp.status, 200)
})

test("swapping the file id for another invalidates the signature", async () => {
  stubFetch(signedHappyPath)
  // Signed for one file, requested for another - the whole point of
  // signing the file id into the payload.
  const url = await signedUrl({ file: "SOMEONE-ELSES-FILE", signedFile: "FILEID" })
  const resp = await worker.fetch(new Request(url), SIGNED_ENV)

  assert.equal(resp.status, 403)
})

test("an expired URL is rejected", async () => {
  stubFetch(signedHappyPath)
  const resp = await worker.fetch(
    new Request(await signedUrl({ expiry: 1000 })),
    SIGNED_ENV,
  )

  assert.equal(resp.status, 403)
})

test("an unsigned URL plays until REQUIRE_SIGNED_URLS is turned on", async () => {
  stubFetch(signedHappyPath)
  const url = `https://w.dev/proxy/${nextAccount()}/load/FILEID/Movie.mkv`

  assert.equal((await worker.fetch(new Request(url), SIGNED_ENV)).status, 200)
  assert.equal(
    (await worker.fetch(new Request(url), { ...SIGNED_ENV, REQUIRE_SIGNED_URLS: "true" }))
      .status,
    403,
  )
})

test("a 409 from the addon no longer stops playback", async () => {
  // O limite de um aparelho por vez saiu. Mesmo um addon antigo,
  // ainda respondendo 409, nao pode travar o player: era esse 409 que
  // virava 403 e fazia o filme parar no meio.
  const calls = stubFetch((url, init) =>
    url.includes("/internal/playback/")
      ? new Response(JSON.stringify({ granted: false }), { status: 409 })
      : signedHappyPath(url, init),
  )
  const viewer = nextViewer()

  const resp = await worker.fetch(new Request(await signedUrl({ viewer })), SIGNED_ENV)

  assert.equal(resp.status, 200)
  const claim = calls.find((c) => c.url.includes("/internal/playback/"))
  assert.equal(claim.method, "POST")
  assert.ok(claim.url.endsWith(`/playback/${viewer}`))
})

test("the slot verdict is cached so one film isn't thousands of claims", async () => {
  const calls = stubFetch(signedHappyPath)
  const viewer = nextViewer()
  const url = await signedUrl({ viewer, session: "sess-cache" })

  await worker.fetch(new Request(url), SIGNED_ENV)
  await worker.fetch(new Request(url), SIGNED_ENV)
  await worker.fetch(new Request(url), SIGNED_ENV)

  assert.equal(calls.filter((c) => c.url.includes("/internal/playback/")).length, 1)
})

test("an unreachable addon fails open rather than blocking playback", async () => {
  stubFetch((url, init) => {
    if (url.includes("/internal/playback/")) throw new Error("network down")
    return signedHappyPath(url, init)
  })

  const resp = await worker.fetch(new Request(await signedUrl()), SIGNED_ENV)

  assert.equal(resp.status, 200)
})
