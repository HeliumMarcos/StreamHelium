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

Se você usa `CF_PROXY_URL`, precisa reimplantar o Worker com o arquivo novo
e configurar:

- **`ACCOUNTS`** (Secret) — JSON mapeando `drive_account_id` (o UUID que
  aparece em `/admin/drives`) pra `{client_id, client_secret, refresh_token}`
  de cada conta. Pegue o `refresh_token` de cada conta conectada — ele fica
  só no banco (criptografado), então a forma mais simples é gerar via o
  mesmo fluxo OAuth manualmente (rclone, ou o notebook que você já tinha)
  pra cada conta do pool, ou usar o `/admin/drives/<id>/connect-google` e
  depois consultar o valor no banco (peça ajuda se for esse o caminho).
- **`CACHE_TTL_SECONDS`** (opcional, padrão 6h) — quanto tempo os bytes
  ficam cacheados na borda da Cloudflare. É essa parte, não o pool de
  contas, que reduz os "muitos acessos" no arquivo.

O worker mudou de sintaxe (Service Worker → ES Modules com `export default`)
— confira se o seu `wrangler.toml`/dashboard está configurado como Module,
que é o padrão em projetos novos.

## O que eu não fiz

Não toquei no seu GitHub nem na Vercel — mesma regra de sempre. E não gerei
os `refresh_token` de cada conta do pool pra você (isso só existe depois
que você mesmo autorizar cada uma em `/admin/drives`).
