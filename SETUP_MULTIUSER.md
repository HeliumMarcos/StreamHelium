# Setup multiusuário — StreamHelium

## 1. Google Cloud — trocar o OAuth Client para "Web application"

O client atual (tipo *Desktop*, usado no fluxo manual via Colab) não serve mais.
Precisa de um novo, tipo **Web**, porque agora o próprio usuário autoriza pelo navegador.

1. https://console.cloud.google.com/apis/credentials (no mesmo projeto que já tem a Drive API habilitada)
2. **Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Authorized redirect URIs → adicione exatamente:
   ```
   https://<seu-dominio>.vercel.app/oauth/callback
   ```
   Troque `<seu-dominio>` pelo domínio real de produção. Se for usar um domínio
   customizado depois, adicione os dois (o `.vercel.app` e o customizado) — dá
   pra ter várias URIs cadastradas ao mesmo tempo.
5. Copie o **Client ID** e o **Client Secret** gerados — vão virar env vars.
6. Na tela **OAuth consent screen**, o escopo `drive.readonly` já deve estar
   liberado (era `drive` completo antes; reduzi para `drive.readonly` no
   código já que o addon só lê arquivos, nunca escreve).

⚠️ Enquanto o app estiver em modo "Testing" no consent screen, só os emails
que você adicionar em **Test users** conseguem autorizar — é exatamente o
controle de acesso que você quer, então não precisa publicar o app nem passar
pela verificação do Google por enquanto. Adicione ali o email de cada pessoa
que você for convidar.

## 2. Vercel — banco de dados

1. No projeto na Vercel → aba **Storage** → **Create Database** → **Postgres**.
2. Ao conectar ao projeto, a Vercel injeta `POSTGRES_URL` automaticamente nas
   env vars — não precisa configurar isso manualmente.

## 3. Vercel — variáveis de ambiente

Em **Settings → Environment Variables**, adicione (Production e Preview):

| Nome | Valor |
|---|---|
| `GOOGLE_CLIENT_ID` | do passo 1 |
| `GOOGLE_CLIENT_SECRET` | do passo 1 |
| `SECRET_KEY` | `98fb9af9595ffebcd12b335aca6d040c50c71f94120a9026c696788605cf070f` |
| `ENCRYPTION_KEY` | `CZmY5YILZA6375xEgwd2mjcq_l2LMbvvdMasq9Bt37I=` |
| `ADMIN_PASSWORD` | `LZViOfDvf1mtFFPl` (troque por uma senha sua) |
| `TMDB_API_KEY` | opcional — só usada como fallback se um usuário não tiver a própria |
| `CF_PROXY_URL` | opcional, como já era |

Os três primeiros valores acima (`SECRET_KEY`, `ENCRYPTION_KEY`, sugestão de
`ADMIN_PASSWORD`) foram gerados agora, só para você — não estão em nenhum
outro lugar. Guarde-os num gerenciador de senhas. **Perder o `ENCRYPTION_KEY`
depois de ter usuários cadastrados torna os tokens deles irrecuperáveis** —
cada um teria que reconectar o Drive.

`TOKEN` (a env var antiga) não é mais usada — pode remover.

## 4. Deploy e uso

1. Depois do deploy, acesse `https://<seu-dominio>/admin`, entre com o
   `ADMIN_PASSWORD`.
2. Adicione um usuário pelo email — isso gera um link `/connect/<token>`.
3. Mande esse link pro usuário. Ele abre, conecta o Google Drive dele e
   (opcionalmente) coloca a própria chave TMDB.
4. Depois de conectado, ele acessa `/u/<user_id>/` para ver a URL de manifest
   e instalar no Stremio.
5. Pra revogar acesso: `/admin` → **Remover** — a URL do addon dele para de
   responder imediatamente.

## O que eu não fiz (de propósito)

- Não toquei no seu repositório GitHub real nem no projeto Vercel — só
  trabalhei numa cópia local. Push e deploy exigem sua confirmação explícita.
- Não escolhi `ADMIN_PASSWORD` por você além de sugerir um valor — troque
  pelo que preferir.
