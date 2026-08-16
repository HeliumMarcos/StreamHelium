"""The addon's landing page.

Kept as a plain string template (instead of a Jinja template file) so the
page ships with the module itself and doesn't depend on a `templates/`
folder surviving the serverless build.

`string.Template` is used rather than str.format/f-strings because the page
is mostly CSS, and `{`/`}` are everywhere. `$` appears nowhere in the CSS or
JS below, so the placeholders stay unambiguous.
"""

from string import Template

from sgd.branding import admin_whatsapp_html

PAGE = Template(
    """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>$name</title>
<link rel="icon" href="$favicon">
<style>
  :root {
    --bg: #0a0710;
    --bg-soft: #120c1c;
    --card: rgba(255, 255, 255, .045);
    --card-brd: rgba(255, 255, 255, .09);
    --txt: #ece8f4;
    --txt-dim: #a79fbb;
    --accent: #8b5cf6;
    --accent-2: #22d3ee;
    --ok: #34d399;
    --warn: #fbbf24;
    --off: #6b7280;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--txt);
    font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; }
  :where(a, button):focus-visible { outline: 3px solid var(--accent-2); outline-offset: 3px; }
  .skip-link {
    position: fixed; top: 10px; left: 10px; z-index: 20;
    padding: 8px 12px; border-radius: 8px; background: #fff; color: #111;
    transform: translateY(-160%);
  }
  .skip-link:focus { transform: translateY(0); }

  .bg {
    position: fixed;
    inset: 0;
    background-image: url("$background");
    background-size: cover;
    background-position: center;
    opacity: .16;
    filter: saturate(1.2);
    z-index: -2;
  }
  .bg::after {
    content: "";
    position: absolute;
    inset: 0;
    background:
      radial-gradient(70% 55% at 50% 0%, rgba(139, 92, 246, .38), transparent 70%),
      linear-gradient(180deg, rgba(10, 7, 16, .55) 0%, var(--bg) 65%);
  }

  .wrap { max-width: 980px; margin: 0 auto; padding: 0 20px 72px; }

  /* ---------- hero ---------- */
  header { text-align: center; padding: 64px 0 40px; }
  .logo {
    width: 104px; height: 104px;
    object-fit: contain;
    border-radius: 24px;
    background: rgba(255, 255, 255, .05);
    border: 1px solid var(--card-brd);
    padding: 10px;
    box-shadow: 0 18px 50px rgba(139, 92, 246, .35);
  }
  h1 {
    margin: 20px 0 6px;
    font-size: clamp(30px, 6vw, 46px);
    letter-spacing: -.02em;
    background: linear-gradient(92deg, #fff 20%, var(--accent-2));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .tagline { margin: 0 auto; max-width: 46ch; color: var(--txt-dim); }
  .pills { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin: 18px 0 0; }
  .pill {
    font-size: 12.5px; font-weight: 600; letter-spacing: .02em;
    padding: 5px 12px; border-radius: 999px;
    border: 1px solid var(--card-brd);
    background: var(--card);
    color: var(--txt-dim);
  }
  .pill.live { color: var(--ok); border-color: rgba(52, 211, 153, .35); }
  .pill.live::before {
    content: ""; display: inline-block;
    width: 7px; height: 7px; margin-right: 7px;
    border-radius: 50%; background: var(--ok);
    box-shadow: 0 0 0 0 rgba(52, 211, 153, .6);
    animation: ping 2s ease-out infinite;
  }
  @keyframes ping {
    70% { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
    100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
  }

  /* ---------- install ---------- */
  .install { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; margin: 30px 0 14px; }
  .btn {
    display: inline-flex; align-items: center; gap: 9px;
    padding: 13px 24px; border-radius: 12px;
    font-weight: 650; font-size: 15px;
    text-decoration: none; cursor: pointer;
    border: 1px solid transparent;
    transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
  }
  .btn:hover { transform: translateY(-2px); }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent), #6d28d9);
    color: #fff;
    box-shadow: 0 12px 30px rgba(139, 92, 246, .42);
  }
  .btn-ghost {
    background: var(--card);
    border-color: var(--card-brd);
    color: var(--txt);
  }
  .btn-ghost:hover { background: rgba(255, 255, 255, .09); }
  .access-notice {
    max-width: 38rem; margin: 30px auto 14px; padding: 16px 18px;
    border: 1px solid rgba(251, 191, 36, .38); border-radius: 14px;
    background: rgba(251, 191, 36, .08); color: var(--txt-dim);
  }
  .access-notice strong { display: block; color: var(--warn); margin-bottom: 4px; }
  .access-notice a { color: var(--accent-2); }
  .url {
    display: block; margin: 0 auto; max-width: 100%;
    overflow-x: auto; white-space: nowrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px; color: var(--txt-dim);
    background: rgba(0, 0, 0, .35);
    border: 1px solid var(--card-brd);
    border-radius: 10px;
    padding: 10px 14px;
    text-align: center;
  }

  /* ---------- sections ---------- */
  section { margin-top: 52px; }
  h2 {
    font-size: 13px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .14em;
    color: var(--txt-dim);
    margin: 0 0 16px;
  }
  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); }
  .grid-2 { grid-template-columns: repeat(2, 1fr); margin-bottom: 14px; }
  .grid-3 { grid-template-columns: repeat(3, 1fr); }
  @media (max-width: 640px) {
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
  }
  .card {
    background: var(--card);
    border: 1px solid var(--card-brd);
    border-radius: 16px;
    padding: 18px;
    backdrop-filter: blur(10px);
  }
  .card h3 {
    margin: 0 0 10px;
    font-size: 13px; font-weight: 650;
    color: var(--txt-dim);
    text-transform: uppercase; letter-spacing: .07em;
    display: flex; align-items: center; gap: 8px;
  }
  .val { font-size: 19px; font-weight: 650; letter-spacing: -.01em; }
  .sub { font-size: 13px; color: var(--txt-dim); margin-top: 4px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--off); flex: none; }
  .dot.ok { background: var(--ok); box-shadow: 0 0 10px rgba(52, 211, 153, .7); }
  .dot.warn { background: var(--warn); }
  .dot.err { background: #f87171; }

  .bar { height: 6px; border-radius: 999px; background: rgba(255, 255, 255, .1); margin-top: 12px; overflow: hidden; }
  .bar > i { display: block; height: 100%; width: 0; border-radius: 999px; background: linear-gradient(90deg, var(--accent-2), var(--accent)); transition: width .8s ease; }

  ol.steps { counter-reset: s; list-style: none; margin: 0; padding: 0; }
  ol.steps li {
    counter-increment: s;
    position: relative;
    padding: 0 0 20px 46px;
    border-left: 1px solid var(--card-brd);
    margin-left: 14px;
  }
  ol.steps li:last-child { border-left-color: transparent; padding-bottom: 0; }
  ol.steps li::before {
    content: counter(s);
    position: absolute; left: -15px; top: -2px;
    width: 29px; height: 29px;
    display: grid; place-items: center;
    border-radius: 50%;
    background: var(--bg-soft);
    border: 1px solid var(--card-brd);
    font-size: 13px; font-weight: 700; color: var(--accent-2);
  }
  ol.steps b { display: block; font-weight: 620; }
  ol.steps span { color: var(--txt-dim); font-size: 14.5px; }

  .legend { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
  .legend div {
    display: flex; gap: 10px; align-items: baseline;
    font-size: 14px; color: var(--txt-dim);
  }
  .legend b { color: var(--txt); font-weight: 600; }
  .sample {
    margin-top: 16px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12.5px; line-height: 1.75;
    white-space: pre-wrap; word-break: break-word;
    background: rgba(0, 0, 0, .35);
    border: 1px solid var(--card-brd);
    border-radius: 12px;
    padding: 14px 16px;
    color: var(--txt-dim);
  }

  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  table td { padding: 11px 8px; border-bottom: 1px solid var(--card-brd); vertical-align: top; }
  table tr:last-child td { border-bottom: 0; }
  table td:first-child {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px; color: var(--accent-2); white-space: nowrap; width: 1%; padding-right: 18px;
  }
  table td:last-child { color: var(--txt-dim); }
  table caption { text-align: left; color: var(--txt-dim); margin-bottom: 10px; }

  footer {
    margin-top: 56px; padding-top: 22px;
    border-top: 1px solid var(--card-brd);
    text-align: center; font-size: 13px; color: var(--txt-dim);
  }
  footer a { color: var(--accent-2); text-decoration: none; }

  @media (max-width: 560px) {
    header { padding-top: 44px; }
    .btn { flex: 1 1 100%; justify-content: center; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
  }
</style>
</head>
<body>
<a class="skip-link" href="#conteudo">Ir para o conteúdo</a>

<div class="bg"></div>

<div class="wrap">
  <header>
    <img class="logo" src="$logo" alt="Logotipo do $name" onerror="this.style.display='none'">
    <h1>$name</h1>
    <p class="tagline">$description</p>
    <div class="pills">
      <span class="pill live">Add-on online</span>
      <span class="pill">v$version</span>
      $type_pills
      <span class="pill">Fonte: Google Drive</span>
    </div>

    $install_block
    $header_extra
  </header>

  <main id="conteudo">
  <section>
    <h2>Status do sistema</h2>
    <div class="grid grid-2">
      $account_status_card
      <div class="card">
        <h3><span class="dot" id="drive-dot"></span> Google Drive</h3>
        <div class="val" id="drive-val">Verificando…</div>
        <div class="sub" id="drive-sub" aria-live="polite">Consultando a conta conectada</div>
        <div class="bar"><i id="drive-bar"></i></div>
      </div>
    </div>
    <div class="grid grid-3">
      <div class="card">
        <h3><span class="dot $tmdb_dot"></span> Metadados TMDB</h3>
        <div class="val">$tmdb_val</div>
        <div class="sub">$tmdb_sub</div>
      </div>
      <div class="card">
        <h3><span class="dot $proxy_dot"></span> Entrega do vídeo</h3>
        <div class="val">$proxy_val</div>
        <div class="sub">$proxy_sub</div>
      </div>
      <div class="card">
        <h3><span class="dot ok"></span> Cobertura</h3>
        <div class="val">Filmes e séries</div>
        <div class="sub">Busca por título, títulos alternativos e ID (IMDb/TMDB)</div>
      </div>
    </div>
  </section>


  <section>
    <h2>Como funciona</h2>
    <div class="card">
      <ol class="steps">
        <li><b>Você abre um filme ou episódio no Stremio</b><span>O Stremio pergunta ao add-on se existe algum stream para aquele ID (tt… ou tmdb:…).</span></li>
        <li><b>O add-on resolve os metadados</b><span>Título em português, título original, títulos alternativos e ano vêm de TMDB, Cinemeta e IMDb — com cache de 7 dias.</span></li>
        <li><b>Busca no seu Drive</b><span>Uma busca em lote varre Meu Drive e todos os drives compartilhados procurando arquivos de vídeo pelo nome, pelas variações do título e pelo próprio ID no nome do arquivo.</span></li>
        <li><b>Filtro e ranqueamento</b><span>Resultados duplicados são removidos, o ano (filmes) e o SxxEyy (séries) são conferidos e o que sobra é ordenado por resolução, fonte, HDR/Dolby Vision, áudio e faixa em português.</span></li>
        <li><b>Play</b><span>O stream é entregue ao Stremio já com nome, qualidade e tamanho formatados.</span></li>
      </ol>
    </div>
  </section>

  <section>
    <h2>O que cada stream mostra</h2>
    <div class="card">
      <div class="legend">
        <div><b>💎</b><span>Resolução + HDR10 / HDR10+ / DV / SDR</span></div>
        <div><b>🔊</b><span>Codec de áudio e canais (Atmos, DD+, DTS-HD, 5.1…)</span></div>
        <div><b>📺</b><span>Serviço de origem (NF, AMZN, DSNP, MAX, ATVP…)</span></div>
        <div><b>💿</b><span>Fonte: Remux, BluRay, WEB-DL, WebRip ou HDTV</span></div>
        <div><b>⚙️</b><span>Codec de vídeo: AV1, H.265 ou H.264</span></div>
        <div><b>💾</b><span>Tamanho real do arquivo no Drive</span></div>
      </div>
      <div class="sample">▶️ $name [4k] 🇧🇷
🎬 Nome do Filme - (2024)
💎 2160p HDR10+ DV   🔊 Atmos 7.1   📺 NF
💿 Remux   ⚙️ H.265   💾 54.20GiB</div>
    </div>
  </section>

  <section>
    <h2>Endpoints</h2>
    <div class="card">
      <table>
        <caption>Rotas utilizadas por esta conta do add-on</caption>
        <tbody>
        <tr><td>/u/&lt;seu-id&gt;/</td><td>Esta página de instruções</td></tr>
        <tr><td>/u/&lt;seu-id&gt;/manifest.json</td><td>Manifesto instalado no Stremio</td></tr>
        <tr><td>/u/&lt;seu-id&gt;/stream/movie/&lt;id&gt;.json</td><td>Streams de um filme (ex.: <code>tt0111161</code> ou <code>tmdb:278</code>)</td></tr>
        <tr><td>/u/&lt;seu-id&gt;/stream/series/&lt;id&gt;.json</td><td>Streams de um episódio (ex.: <code>tt0903747:1:1</code>)</td></tr>
        <tr><td>/u/&lt;seu-id&gt;/health</td><td>Diagnóstico em JSON da conta e do Drive</td></tr>
        </tbody>
      </table>
    </div>
  </section>
  </main>

  <footer>
    <span>$name · v$version<span id="host"></span></span><br>
    <a href="https://github.com/HeliumMarcos/Gdrive-Stremio-Update">Código-fonte no GitHub</a>
  </footer>
</div>

<script>
(function () {
  var urlEl = document.getElementById("manifest-url");
  var btn = document.getElementById("copy");
  if (urlEl && btn) btn.addEventListener("click", function () {
    var url = urlEl.textContent.trim();
    var done = function () {
      btn.textContent = "✅ Copiado!";
      setTimeout(function () { btn.textContent = "🔗 Copiar manifest"; }, 1800);
    };
    var failed = function () {
      btn.textContent = "Não foi possível copiar";
      setTimeout(function () { btn.textContent = "🔗 Copiar manifest"; }, 2200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, failed);
    } else {
      var t = document.createElement("textarea");
      t.value = url;
      document.body.appendChild(t);
      t.select();
      var copied = false;
      try { copied = document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(t);
      copied ? done() : failed();
    }
  });

  if (location.host) {
    document.getElementById("host").textContent = " · " + location.host;
  }

  var dot = document.getElementById("drive-dot");
  var val = document.getElementById("drive-val");
  var sub = document.getElementById("drive-sub");
  var bar = document.getElementById("drive-bar");

  var healthUrl = "$health_url";
  if (!healthUrl) {
    dot.className = "dot warn";
    val.textContent = "Acesso indisponível";
    sub.textContent = "Regularize a conta ou peça ao administrador para verificar o Drive.";
    bar.parentNode.style.display = "none";
    return;
  }

  fetch(healthUrl, { headers: { "Accept": "application/json" } })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var drv = d.drive || {};
      if (!drv.connected) {
        dot.className = "dot err";
        val.textContent = "Sem conexão";
        sub.textContent = "Não foi possível falar com a API do Drive"
          + (drv.error ? " (" + drv.error + ")" : "") + " — fale com o administrador.";
        bar.parentNode.style.display = "none";
        return;
      }
      dot.className = "dot ok";
      val.textContent = drv.account || "Conta conectada";
      var parts = [];
      if (typeof drv.shared_drives === "number") {
        parts.push(drv.shared_drives + (drv.shared_drives === 1 ? " drive compartilhado" : " drives compartilhados"));
      }
      if (drv.usage_human) {
        parts.push(drv.limit_human ? drv.usage_human + " de " + drv.limit_human + " em uso" : drv.usage_human + " em uso");
      }
      sub.textContent = parts.join(" · ") || "Conectado";
      if (typeof drv.usage_pct === "number") {
        bar.style.width = Math.min(100, Math.max(2, drv.usage_pct)) + "%";
      } else {
        bar.parentNode.style.display = "none";
      }
    })
    .catch(function () {
      dot.className = "dot err";
      val.textContent = "Indisponível";
      sub.textContent = "O diagnóstico não respondeu";
      bar.parentNode.style.display = "none";
    });
})();
</script>
</body>
</html>
"""
)


def render_landing():
    """Kept only for tests/back-compat imports; '/' now redirects straight
    to /login instead of rendering a static page (see routes.py)."""
    return ""


def _pill(text):
    return f'<span class="pill">{text}</span>'


def render(manifest, manifest_url, stremio_url, tmdb_enabled, proxy_enabled,
           health_url=None, connect_url=None, account_status=None, logged_in=False,
           service_available=True):
    type_labels = {"movie": "Filmes", "series": "Séries"}
    type_pills = "\n      ".join(
        _pill(type_labels.get(t, t.capitalize())) for t in manifest.get("types", [])
    )

    if tmdb_enabled:
        tmdb = ("ok", "Ativo", "IDs tmdb: resolvidos e títulos em pt-BR disponíveis")
    else:
        tmdb = (
            "warn", "Não configurado",
            admin_whatsapp_html("Falta configurar a TMDB"),
        )

    if proxy_enabled:
        proxy = (
            "ok",
            "Proxy Cloudflare",
            "O vídeo passa pelo Worker — o token OAuth não é exposto ao player",
        )
    else:
        proxy = (
            "warn",
            "API do Google direto",
            "Defina CF_PROXY_URL para servir o vídeo por um Worker Cloudflare",
        )

    if account_status:
        if not account_status.get("active", True):
            acc = ("", "Desativada", f"Conta desativada. {admin_whatsapp_html('Fale com o admin')}")
        elif account_status.get("expired"):
            acc = ("", "Expirada", f"Acesso expirado. {admin_whatsapp_html('Peça uma renovação')}")
        elif account_status.get("days_left") is not None:
            days = account_status["days_left"]
            acc = (
                "warn" if days <= 3 else "ok",
                f"Expira em {days} dia{'s' if days != 1 else ''}",
                admin_whatsapp_html("Preciso de mais tempo"),
            )
        else:
            acc = ("ok", "Ativa", "Sem prazo de expiração")
        account_status_card = f"""
      <div class="card">
        <h3><span class="dot {acc[0]}"></span> Sua conta</h3>
        <div class="val">{acc[1]}</div>
        <div class="sub">{acc[2]}</div>
      </div>"""
    else:
        account_status_card = ""

    account_available = True if account_status is None else bool(account_status.get("active", True))
    account_available = account_available and not bool(account_status and account_status.get("expired"))
    can_install = account_available and service_available

    if can_install:
        install_block = f"""
    <div class="install">
      <a class="btn btn-primary" href="{stremio_url}">⬇️ Instalar no Stremio</a>
      <button class="btn btn-ghost" id="copy" type="button">🔗 Copiar manifest</button>
    </div>
    <code class="url" id="manifest-url">{manifest_url}</code>"""
    else:
        if account_status and not account_status.get("active", True):
            reason = "Esta conta foi desativada."
        elif account_status and account_status.get("expired"):
            reason = "O período de acesso desta conta terminou."
        else:
            reason = "A conta está ativa, mas não há uma conta Google Drive disponível."
        install_block = f"""
    <div class="access-notice" role="alert">
      <strong>Instalação temporariamente indisponível</strong>
      <span>{reason} {admin_whatsapp_html('Fale com o administrador')}</span>
    </div>"""

    header_extra = (
        '<p style="margin-top:10px"><a href="/logout" style="color:var(--accent-2);'
        'font-size:13px" onclick="event.preventDefault();'
        'document.getElementById(\'logout-form\').submit()">Sair da conta</a>'
        '<form id="logout-form" method="post" action="/logout" style="display:none"></form></p>'
        if logged_in else ""
    )

    return PAGE.safe_substitute(
        name=manifest.get("name", "Stremio Add-on"),
        version=manifest.get("version", ""),
        description=manifest.get("description", ""),
        logo=manifest.get("logo", ""),
        favicon=manifest.get("favicon", ""),
        background=manifest.get("background", ""),
        install_block=install_block,
        type_pills=type_pills,
        manifest_url=manifest_url,
        stremio_url=stremio_url,
        health_url=health_url if can_install else "",
        account_status_card=account_status_card,
        header_extra=header_extra,
        tmdb_dot=tmdb[0],
        tmdb_val=tmdb[1],
        tmdb_sub=tmdb[2],
        proxy_dot=proxy[0],
        proxy_val=proxy[1],
        proxy_sub=proxy[2],
    )
