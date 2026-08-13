# Migração: pool de Drives + contas de família com expiração

## O que mudou

- Você (admin) conecta as contas Google Drive em `/admin/drives` — não é
  mais a família que autoriza o próprio Drive.
- Ao criar uma conta de família em `/admin`, ela é atribuída automaticamente
  à conta Drive com menos gente (não é um round-robin fixo por índice — é
  sempre "quem tem menos" no momento da criação, então continua equilibrado
  mesmo se você adicionar/remover contas Drive depois).
- Cada conta de família pode ter um prazo (`expira em X dias`, opcional).
  Passou do prazo, o addon dela para de responder (404) sem precisar
  remover a conta — clique em **+30d** pra renovar.
- O banco existente **não precisa ser recriado**: as mudanças no schema são
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, então contas já cadastradas
  continuam existindo (só ficam sem `drive_account_id` até você reatribuir
  manualmente ou recriar — o mais simples é remover e recriar as contas de
  família já que ninguém instalou de verdade ainda).

## Passo a passo

1. Aplique o código (zip anexo) e dê push/deploy como das outras vezes.
2. `/admin/drives` → adicione um nome (ex: "Drive 1") → **Conectar ao
   Google** → autorize com a conta Google dona da pasta compartilhada.
   Repita pra cada conta do pool.
3. `/admin` → crie as contas de família normalmente, agora com o campo
   opcional de expiração em dias.
4. Cada família recebe o link de convite de sempre (`/connect/<token>`) —
   só que agora não tem mais passo de autorizar Google, só o campo opcional
   de chave TMDB.

## Cloudflare Worker (`cf_proxy.js`)

O Worker não guarda mais credencial nenhuma. Ele pede um access token de
curta duração pro addon, que é quem já tem os `refresh_token` no banco.

Antes existiam duas cópias do mesmo `refresh_token` — uma no banco, outra no
secret `ACCOUNTS` do Worker — e nada mantinha as duas em sincronia.
Reconectar uma conta em `/admin/drives` grava um token novo no banco e deixa
o do Worker pra trás; a partir daí o Worker falha o refresh com
`invalid_grant` e **todo** request proxiado vira 502, enquanto com o proxy
desligado continua tudo funcionando. Foi exatamente isso que aconteceu.

### Configuração

Na aplicação (Vercel → Environment Variables):

- **`PROXY_SHARED_SECRET`** — qualquer string aleatória. Gere com
  `python -c "import secrets; print(secrets.token_hex(32))"`. Sem ela, o
  endpoint `/internal/drive-token/<id>` responde 503.

No Worker (Settings → Variables):

- **`TOKEN_ENDPOINT`** (texto) — `https://<seu-addon>/internal/drive-token`.
  Já vem preenchido no `wrangler.toml`.
- **`TOKEN_ENDPOINT_SECRET`** (Secret) — o mesmo valor de
  `PROXY_SHARED_SECRET`. Defina com
  `npx wrangler secret put TOKEN_ENDPOINT_SECRET`.

Com `TOKEN_ENDPOINT` definido, o `ACCOUNTS` deixa de ser lido e pode ser
apagado. Ele continua funcionando como fallback pra quem ainda não migrou —
`/admin/drives/worker-config` gera o JSON.

Não existe mais `CACHE_TTL_SECONDS`: o cache de borda foi removido porque a
Cloudflare não guarda respostas `206 Partial Content`, que é o que todo
request de vídeo com `Range` recebe.

### Deploy

O deploy da Vercel **não** publica o Worker — são dois lugares separados, e
editar o código pelo dashboard foi como o publicado passou a divergir do
repositório. Agora:

- Todo push na `main` que mexa em `cf_proxy.js` ou `wrangler.toml` publica o
  Worker via GitHub Actions (`.github/workflows/deploy-worker.yml`). Precisa
  do secret `CLOUDFLARE_API_TOKEN` no repositório (Cloudflare → My Profile →
  API Tokens → template *Edit Cloudflare Workers*).
- Na mão, quando precisar: `npx wrangler deploy`.

Secrets do Worker não são afetados por deploy — sobrevivem sem precisar
reconfigurar.

### Testes

- `python -m pytest -q` — addon
- `node --test tests/cf_proxy.test.mjs` — Worker (roda fora da Cloudflare,
  com `fetch` stubado)

Os dois rodam no CI a cada push e PR.

## O que eu não fiz

Não toquei no seu GitHub nem na Vercel — mesma regra de sempre. E não gerei
os `refresh_token` de cada conta do pool pra você (isso só existe depois
que você mesmo autorizar cada uma em `/admin/drives`).
