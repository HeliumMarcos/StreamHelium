// Google Drive streaming proxy - hides the OAuth token from the player,
// supports multiple pool accounts. No edge caching: Cloudflare's cache
// never stores 206 Partial Content responses (which is what every ranged
// video request gets), so there's no simple way to cache this traffic
// without a proper manual Range-slicing implementation against the Cache
// API - not done here, to keep playback reliable.
//
// Set the ACCOUNTS environment variable (Cloudflare dashboard -> Workers ->
// your worker -> Settings -> Variables -> add as a Secret, or via
// `wrangler secret put ACCOUNTS`) to a JSON object mapping drive_account_id
// (the same UUID shown in /admin/drives) -> its credentials:
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
    accountsCache = {}
  }
  return accountsCache
}

// Per-account access token cache. Lives only as long as this Worker
// isolate stays warm - a cold isolate just re-fetches a new token, which is
// cheap and normal.
const tokenCache = new Map()

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
    return streamFile(env, request, accountId, fileId)
  }

  // Legacy single-account route from before the pool model existed:
  // /load/<file_id>/<file_name>. Uses whichever credentials are registered
  // under the "default" key in ACCOUNTS.
  if (parts[0] === "load") {
    const fileId = parts[1]
    return streamFile(env, request, "default", fileId)
  }

  return new Response("404 Not Found!", { status: 404 })
}

async function streamFile(env, request, accountId, fileId) {
  const accounts = getAccounts(env)
  const credentials = accounts[accountId]
  if (!credentials) {
    return new Response(`404 Unknown account: ${accountId}`, { status: 404 })
  }

  const fetchURL = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`
  const range = request.headers.get("Range")

  let resp
  try {
    resp = await fetchFromDrive(fetchURL, accountId, credentials, range)
    // A cached access token can be revoked (or the account re-authorized)
    // while this isolate is still warm, and the cache entry would keep
    // serving the dead token until the isolate is recycled. Drop it and
    // retry once with a fresh one instead of failing playback.
    if (resp.status === 401) {
      tokenCache.delete(accountId)
      resp = await fetchFromDrive(fetchURL, accountId, credentials, range)
    }
  } catch (e) {
    return new Response(`502 ${e.message}`, { status: 502 })
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

async function fetchFromDrive(fetchURL, accountId, credentials, range) {
  const accessToken = await getAccessToken(accountId, credentials)

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
  // request that arrived without one, and Google rejects that as malformed
  // instead of ignoring it - so the very first request of a playback
  // session failed and the video never started. Stremio hid the bug
  // because its streaming server always opens with `Range: bytes=0-`;
  // players that fetch the URL directly (Nuvio, and ExoPlayer generally)
  // send no Range header when starting from byte 0, so they hit it every
  // time.
  if (range) headers.Range = range

  return fetch(fetchURL, { method: "GET", headers })
}

async function getAccessToken(accountId, credentials) {
  const cached = tokenCache.get(accountId)
  if (cached && cached.expires_at > Date.now()) {
    return cached.access_token
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
    throw new Error(`Token refresh failed for account ${accountId}: ${JSON.stringify(data)}`)
  }

  tokenCache.set(accountId, {
    access_token: data.access_token,
    // Refresh 60s early so a request never races an expiring token.
    expires_at: Date.now() + (data.expires_in - 60) * 1000,
  })
  return data.access_token
}

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env)
    } catch (e) {
      return new Response(`500 ${e.message}`, { status: 500 })
    }
  },
}
