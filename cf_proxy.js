// Multi-account Google Drive streaming proxy, with edge caching.
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
//
// Optional env var: CACHE_TTL_SECONDS (default 21600 = 6h). This is the
// part that actually reduces "too many people accessed this file" errors
// from Google: repeat requests for the same file (and byte range) within
// the TTL are served from Cloudflare's edge cache and never reach Drive at
// all, regardless of how many pool accounts or family accounts exist.
// Splitting accounts into a pool does NOT do this by itself - only caching
// does, since the underlying file is shared across every account either way.

const DEFAULT_CACHE_TTL_SECONDS = 21600

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

async function handleRequest(request, env) {
  const url = new URL(request.url)
  const parts = url.pathname.split("/").filter(Boolean)

  if (url.pathname === "/") {
    return new Response("200 Online!", { status: 200 })
  }

  // Current route: /proxy/<account_id>/load/<file_id>/<file_name>
  if (parts[0] === "proxy" && parts[2] === "load") {
    const accountId = parts[1]
    const fileId = parts[3]
    const fileName = parts[4] || "file_name.vid"
    return streamFile(env, accountId, request.headers.get("Range"), fileId, fileName)
  }

  // Legacy single-account route from before the pool model existed:
  // /load/<file_id>/<file_name>. Uses whichever credentials are registered
  // under the "default" key in ACCOUNTS.
  if (parts[0] === "load") {
    const fileId = parts[1]
    const fileName = parts[2] || "file_name.vid"
    return streamFile(env, "default", request.headers.get("Range"), fileId, fileName)
  }

  return new Response("404 Not Found!", { status: 404 })
}

async function streamFile(env, accountId, range, fileId, fileName) {
  const accounts = getAccounts(env)
  const credentials = accounts[accountId]
  if (!credentials) {
    return new Response(`404 Unknown account: ${accountId}`, { status: 404 })
  }

  let accessToken
  try {
    accessToken = await getAccessToken(accountId, credentials)
  } catch (e) {
    return new Response(`502 ${e.message}`, { status: 502 })
  }

  const ttl = parseInt(env.CACHE_TTL_SECONDS, 10) || DEFAULT_CACHE_TTL_SECONDS
  const fetchURL = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`

  const resp = await fetch(fetchURL, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Range: range || "",
    },
    cf: {
      cacheEverything: true,
      cacheTtl: ttl,
    },
  })

  const { readable, writable } = new TransformStream()
  resp.body.pipeTo(writable)
  return new Response(readable, resp)
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
