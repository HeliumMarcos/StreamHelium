// Google Drive streaming proxy - hides the OAuth token from the player,
// supports multiple pool accounts. No edge caching: Cloudflare's cache
// never stores 206 Partial Content responses (which is what every ranged
// video request gets), so there's no simple way to cache this traffic
// without a proper manual Range-slicing implementation against the Cache
// API - not done here, to keep playback reliable.
//
// Deploy with `npx wrangler deploy` (see wrangler.toml), or let the
// deploy-worker GitHub Action do it on every push to main that touches
// this file. Editing the code in the Cloudflare dashboard works too, but
// then what's live silently stops matching what's in the repo.
//
// == Credentials ==
//
// Preferred: point the Worker at the addon and let it mint tokens.
//
//   TOKEN_ENDPOINT         (var)    https://<addon>/internal/drive-token
//   TOKEN_ENDPOINT_SECRET  (secret) same value as the addon's
//                                   PROXY_SHARED_SECRET env var
//
// The addon already holds every pool account's refresh token in Postgres,
// so this keeps that credential in one place. The alternative below keeps
// a second copy here, which is exactly what drifted out of sync before and
// turned every proxied request into a 502.
//
// Fallback (used only when TOKEN_ENDPOINT is unset): the ACCOUNTS secret,
// a JSON object mapping drive_account_id (the UUID shown in /admin/drives)
// to its credentials. /admin/drives/worker-config generates it for you.
//
//   {
//     "11111111-1111-1111-1111-111111111111": {
//       "client_id": "...apps.googleusercontent.com",
//       "client_secret": "...",
//       "refresh_token": "..."
//     },
//     "22222222-2222-2222-2222-222222222222": { ... }
//   }

let accountsCache = null
function getAccounts(env) {
  if (accountsCache) return accountsCache
  try {
    accountsCache = JSON.parse(env.ACCOUNTS || "{}")
  } catch (e) {
    console.error("ACCOUNTS is not valid JSON - falling back to no accounts")
    accountsCache = {}
  }
  return accountsCache
}

// Per-account access token cache. Lives only as long as this Worker
// isolate stays warm - a cold isolate just re-fetches a new token, which is
// cheap and normal.
const tokenCache = new Map()

// Carries the status the client should see, so a misconfigured account
// reads as 404 and a broken credential as 502 instead of everything
// collapsing into one opaque error.
class TokenError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range, Content-Type",
  "Access-Control-Expose-Headers":
    "Content-Length, Content-Range, Content-Type, Accept-Ranges",
  "Access-Control-Max-Age": "86400",
}

async function handleRequest(request, env) {
  const url = new URL(request.url)
  const parts = url.pathname.split("/").filter(Boolean)

  if (url.pathname === "/") {
    return new Response("200 Online!", { status: 200 })
  }

  // Players that run in a WebView (and anything browser-based) send a CORS
  // preflight before the actual media request. Answering it here costs
  // nothing and stops those clients from failing before playback starts.
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS })
  }

  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("405 Method Not Allowed", {
      status: 405,
      headers: { Allow: "GET, HEAD, OPTIONS" },
    })
  }

  // Current route: /proxy/<account_id>/load/<file_id>/<file_name>
  if (parts[0] === "proxy" && parts[2] === "load") {
    const accountId = parts[1]
    const fileId = parts[3]
    return authorizeAndStream(env, request, url, accountId, fileId)
  }

  // Legacy single-account route from before the pool model existed:
  // /load/<file_id>/<file_name>. Uses whichever credentials are registered
  // under the "default" key in ACCOUNTS.
  if (parts[0] === "load") {
    const fileId = parts[1]
    return authorizeAndStream(env, request, url, "default", fileId)
  }

  return new Response("404 Not Found!", { status: 404 })
}

// A playback URL carries ?u=<viewer>&n=<session>&e=<expiry>&s=<signature>,
// signed by the addon (see sgd/signing.py). Two things follow from it:
// the URL stops being an eternal public link to the file, and the Worker
// finally knows which viewer the bytes are for - which is what makes a
// real one-device-at-a-time limit possible, since the addon can be asked
// whether this session still holds that viewer's slot.
async function authorizeAndStream(env, request, url, accountId, fileId) {
  const viewer = url.searchParams.get("u")
  const session = url.searchParams.get("n")
  const expiry = url.searchParams.get("e")
  const signature = url.searchParams.get("s")

  if (!viewer || !session || !expiry || !signature) {
    // Unsigned. Old links handed out before signing existed still look
    // like this, so they keep working until REQUIRE_SIGNED_URLS is turned
    // on - flip it once no player is holding a pre-signing URL any more.
    if (env.REQUIRE_SIGNED_URLS === "true") {
      console.error(`Rejected unsigned request for file ${fileId}`)
      return new Response("403 Unsigned playback URL", { status: 403 })
    }
    return streamFile(env, request, accountId, fileId)
  }

  const valid = await verifySignature(env, {
    accountId, fileId, viewer, session, expiry, signature,
  })
  if (!valid) {
    console.error(`Rejected bad or expired signature for file ${fileId}`)
    return new Response("403 Invalid or expired playback URL", { status: 403 })
  }

  const slot = await claimPlaybackSlot(env, viewer, session)
  if (slot === "taken") {
    return new Response("403 Already playing on another device", { status: 403 })
  }

  return streamFile(env, request, accountId, fileId)
}

async function verifySignature(env, { accountId, fileId, viewer, session, expiry, signature }) {
  const secret = env.TOKEN_ENDPOINT_SECRET
  if (!secret) {
    console.error("Signed URL received but TOKEN_ENDPOINT_SECRET is not set")
    return false
  }

  const expiresAt = parseInt(expiry, 10)
  if (!Number.isFinite(expiresAt) || expiresAt < Date.now() / 1000) return false

  // Must match sgd/signing.py exactly: HMAC-SHA256 over
  // account:file:user:session:expiry, base64url, padding stripped.
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  )
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${accountId}:${fileId}:${viewer}:${session}:${expiresAt}`),
  )
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "")

  return timingSafeEqual(expected, signature)
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return diff === 0
}

// Verdicts are cached per viewer+session so a two-hour film doesn't turn
// into thousands of calls to the addon - a player issues a fresh range
// request for every seek and every buffer top-up. The cache window has to
// stay well under PLAYBACK_IDLE_SECONDS on the addon side, or a viewer
// would let their own slot go stale and evict themselves.
const slotCache = new Map()
const SLOT_CACHE_MS = 45_000

async function claimPlaybackSlot(env, viewer, session) {
  if (!env.TOKEN_ENDPOINT || !env.TOKEN_ENDPOINT_SECRET) return "granted"

  const key = `${viewer}|${session}`
  const cached = slotCache.get(key)
  if (cached && cached.until > Date.now()) return cached.verdict

  const base = env.TOKEN_ENDPOINT.replace(/\/drive-token\/?$/, "").replace(/\/$/, "")
  let resp
  try {
    resp = await fetch(`${base}/playback/${encodeURIComponent(viewer)}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.TOKEN_ENDPOINT_SECRET}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ session }),
    })
  } catch (e) {
    // Fail open, like the addon does on a database error. The limit is a
    // convenience; it isn't worth taking the household's video down for.
    console.error(`Playback slot check unreachable (${e.message}) - allowing`)
    return "granted"
  }

  if (resp.status === 409) {
    // Don't cache a denial for as long as a grant: the other device may
    // stop at any moment, and a viewer retrying shouldn't wait out a stale
    // no.
    slotCache.set(key, { verdict: "taken", until: Date.now() + 5_000 })
    return "taken"
  }

  if (!resp.ok) {
    console.error(`Playback slot check returned ${resp.status} - allowing`)
    return "granted"
  }

  slotCache.set(key, { verdict: "granted", until: Date.now() + SLOT_CACHE_MS })
  return "granted"
}

async function streamFile(env, request, accountId, fileId) {
  const fetchURL = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`
  const range = request.headers.get("Range")

  let resp
  try {
    resp = await fetchFromDrive(fetchURL, env, accountId, range)
    // A cached access token can be revoked (or the account re-authorized)
    // while this isolate is still warm, and the cache entry would keep
    // serving the dead token until the isolate is recycled. Drop it and
    // retry once with a fresh one instead of failing playback.
    if (resp.status === 401) {
      tokenCache.delete(accountId)
      resp = await fetchFromDrive(fetchURL, env, accountId, range)
    }
  } catch (e) {
    // Without this the only trace of a credential problem in the Workers
    // logs is a bare 502 with no reason attached.
    console.error(`Account ${accountId}: ${e.message}`)
    return new Response(`${e.status || 502} ${e.message}`, {
      status: e.status || 502,
    })
  }

  if (resp.status >= 400) {
    console.error(`Account ${accountId}: Drive returned ${resp.status} for file ${fileId}`)
  }

  // Build the response headers explicitly instead of copying everything
  // Google sent - only what the player actually needs to handle Range
  // correctly. Forwarding every original header through an extra hop is
  // more surface area for something to get mangled along the way.
  const headers = new Headers(CORS_HEADERS)
  for (const key of ["content-type", "content-length", "content-range", "accept-ranges"]) {
    const value = resp.headers.get(key)
    if (value) headers.set(key, value)
  }
  if (!headers.has("accept-ranges")) headers.set("accept-ranges", "bytes")

  // For HEAD we still had to issue a GET upstream (the Drive media endpoint
  // only answers GET), so drop the body here rather than handing back a
  // stream nobody will read.
  if (request.method === "HEAD") {
    if (resp.body) resp.body.cancel().catch(() => {})
    return new Response(null, { status: resp.status, headers })
  }

  return new Response(resp.body, { status: resp.status, headers })
}

async function fetchFromDrive(fetchURL, env, accountId, range) {
  const accessToken = await getAccessToken(accountId, env)

  // Plain pass-through: forward the client's Range header to Google as-is.
  // No caching here - Cloudflare never stores 206 Partial Content
  // responses, and fetching the full file to work around that broke
  // playback outright (players expect 206 for a ranged request, not a 200
  // with the whole file). This is just hiding the OAuth token, nothing more.
  //
  // Accept-Encoding: identity - stops Google from compressing the
  // response. Video is already compressed, so gzip/br would do nothing
  // useful, but a compressed-response-vs-declared-length mismatch getting
  // altered somewhere on the way through Cloudflare's edge is a documented
  // cause of intermittent stalls/rebuffers.
  const headers = {
    Authorization: `Bearer ${accessToken}`,
    "Accept-Encoding": "identity",
  }

  // Only send Range when the client actually asked for one. Setting it
  // unconditionally used to send a literal empty `Range:` header on every
  // request that arrived without one, which is malformed.
  if (range) headers.Range = range

  return fetch(fetchURL, { method: "GET", headers })
}

async function getAccessToken(accountId, env) {
  const cached = tokenCache.get(accountId)
  if (cached && cached.expires_at > Date.now()) {
    return cached.access_token
  }

  let token
  if (env.TOKEN_ENDPOINT) {
    try {
      token = await tokenFromAddon(accountId, env)
    } catch (e) {
      // Don't take playback down while the addon side is still being set
      // up, or is briefly unreachable: if the legacy secret is still
      // configured, use it. Loudly, though - a silent fallback is how the
      // two copies of this credential drifted apart unnoticed to begin
      // with. Once ACCOUNTS is deleted there's nothing to fall back to and
      // the error surfaces, which is the point.
      if (!env.ACCOUNTS) throw e
      console.error(`Token endpoint failed (${e.message}) - falling back to ACCOUNTS`)
      token = await tokenFromRefreshToken(accountId, env)
    }
  } else {
    token = await tokenFromRefreshToken(accountId, env)
  }

  tokenCache.set(accountId, {
    access_token: token.access_token,
    // Refresh 60s early so a request never races an expiring token.
    expires_at: Date.now() + (token.expires_in - 60) * 1000,
  })
  return token.access_token
}

async function tokenFromAddon(accountId, env) {
  if (!env.TOKEN_ENDPOINT_SECRET) {
    throw new TokenError("TOKEN_ENDPOINT is set but TOKEN_ENDPOINT_SECRET is not", 500)
  }

  const url = `${env.TOKEN_ENDPOINT.replace(/\/$/, "")}/${encodeURIComponent(accountId)}`
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${env.TOKEN_ENDPOINT_SECRET}` },
  })

  if (resp.status === 404) {
    throw new TokenError(`Unknown or disconnected account ${accountId}`, 404)
  }
  if (!resp.ok) {
    const body = (await resp.text()).slice(0, 200)
    throw new TokenError(`Addon token endpoint returned ${resp.status}: ${body}`, 502)
  }

  const data = await resp.json()
  if (!data.access_token) {
    throw new TokenError("Addon token endpoint returned no access_token", 502)
  }
  return { access_token: data.access_token, expires_in: data.expires_in || 300 }
}

async function tokenFromRefreshToken(accountId, env) {
  const credentials = getAccounts(env)[accountId]
  if (!credentials) {
    throw new TokenError(`Unknown account ${accountId}`, 404)
  }

  const resp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: credentials.client_id,
      client_secret: credentials.client_secret,
      refresh_token: credentials.refresh_token,
      grant_type: "refresh_token",
    }),
  })
  const data = await resp.json()
  if (!data.access_token) {
    // `invalid_grant` here almost always means the copy of the refresh
    // token in ACCOUNTS is stale - the account was re-authorized and only
    // the addon's database got the new one.
    throw new TokenError(`Token refresh failed: ${JSON.stringify(data)}`, 502)
  }
  return data
}

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env)
    } catch (e) {
      console.error(`Unhandled: ${e.stack || e.message}`)
      return new Response(`500 ${e.message}`, { status: 500 })
    }
  },
}
