# Wishly — backend

API da lista de desejos. Você cola o link de um produto; o servidor abre a página, lê a foto e o preço e guarda o item num SQLite. A interface fica no app Flutter.

## Run locally

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app
```

The API listens on `http://127.0.0.1:47231`. Set a JWT secret before starting (at least 16 characters):

```bash
export JWT_SECRET=dev-only-change-me-please
```

| Variable | Default | |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `47231` | Port |
| `WISHLY_DB` | `data/wishlist.db` | SQLite file. `QUERO_DB` is still read if `WISHLY_DB` is unset |
| `QUERO_WEB_DIR` | `../frontend/build/web` when that folder exists | Optional Flutter web build served on `/` |
| `DATABASE_URL` | unset | Postgres URL (`postgres://` or `postgresql://`). When set, SQLite is not used |
| `JWT_SECRET` | required | Signs login tokens. At least 16 characters. On Render, `render.yaml` generates it |
| `PUBLIC_SHORT_BASE` | the request host | Origin of short links, no trailing slash. Production: `https://lista-desejos-backend.onrender.com` |
| `PUBLIC_WEB_BASE` | the GitHub Pages URL | Where `/s/{code}` sends the browser. Default `https://cesarwillemberg.github.io/wishly-mobile` |
| `ANDROID_PACKAGE` | `com.wishly.app` | Package name inside `/.well-known/assetlinks.json` |
| `ANDROID_SHA256_FINGERPRINTS` | placeholder | Comma-separated signing cert fingerprints. Until you set this, the file contains `REPLACE_WITH_SHA256_FINGERPRINT` |
| `IOS_APP_ID` | `TEAMID.com.wishly.app` | Apple team id plus bundle id for `apple-app-site-association`. Replace `TEAMID` |
| `CORS_ORIGINS` | `https://cesarwillemberg.github.io` | Comma-separated browser origins. Localhost is always allowed |
| `WISHLY_AUTH_RATE` | `20` | Logins and sign-ups allowed per IP each minute. `0` turns the limit off |
| `WISHLY_SCRAPE_RATE` | `30` | Product reads allowed per IP each minute |
| `WISHLY_DB_POOL_MIN` | `1` | Postgres pool size. Ignored for SQLite |
| `WISHLY_DB_POOL_MAX` | `6` | Postgres pool size. A Neon `-pooler` host in `DATABASE_URL` is used as-is; prepared statements stay off so transaction-mode pooling works |
| `WISHLY_MIGRATE` | `1` | `0` skips `alembic upgrade` on startup. Leave it on unless the Render pre-deploy command runs the upgrade |
| `WISHLY_SCRAPE_INLINE` | unset | `1` runs the product fetch in the request. Tests set this. Production fills the photo and price in a background thread |
| `WISHLY_FETCH_PROXY_URL` | unset | Optional. A Cloudflare Worker that accepts `?url=`. Used only after a direct fetch is blocked. See `proxy-worker/README.md` |
| `WISHLY_PROXY_SECRET` | unset | Sent as the `WISHLY_PROXY_SECRET` header when the Worker URL is set |
| `SCRAPER_API_KEY` | unset | Optional ScraperAPI key, also used only after a direct fetch is blocked |

```bash
pytest
python scripts/live_report.py
```

Accounts, lists, and items are created by Alembic. Startup runs `alembic upgrade head`, and if the database is already at head it stops after reading `alembic_version`. Set `WISHLY_MIGRATE=0` only after Render's pre-deploy command is `alembic upgrade head` (`render.yaml` sets that for a new Blueprint). If this database still has the old single `items` table, those rows move into **Minha lista** for the first account that signs up. A brand-new Postgres database starts empty. The free Render disk does not keep SQLite across restarts, so production must set `DATABASE_URL`.

Postgres uses a small `psycopg_pool` (1–6 connections, TCP keepalives, a check before each checkout). Each API request holds one connection. SQLite still opens a connection per request and does not use the pool. Adding or refreshing an item saves it immediately with status `pending` and reads the store page in the background (about 10 seconds). `GET /api/health?db=1` adds `db_ms`, the time to check out a connection and run `SELECT 1`.

## API

- `POST /api/auth/register` and `POST /api/auth/login` — `{ "email", "password" }` returns a JWT (30 days). Send `Authorization: Bearer …`
- `GET /api/auth/me`
- `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}` — a list is visible to its owner, editors (“Pode editar”) and viewers (“Só pode ver”). Editors change items and categories. Viewers can read and reserve. Only the owner renames, deletes, shares, or invites. Existing lists keep their owner when Alembic runs.
- `POST /api/lists/{id}/members` with `{ "email", "role": "editor" | "viewer" }` adds an account that already exists. Otherwise the response asks the owner to send the invite link. No email is sent.
- `PATCH/DELETE /api/lists/{id}/members/{user_id}` changes a role or removes someone. `POST /api/lists/{id}/leave` is how an editor or viewer leaves.
- `POST /api/lists/{id}/invites/{role}`, `POST …/regenerate`, `DELETE …/{role}` manage two invite links. `GET /c/{code}` is the preview page. `POST /api/invites/{code}/accept` joins after login.
- `POST /api/lists/{id}/share`, `POST …/share/regenerate`, `DELETE …/share`
- `GET /api/share/{token}` — public read-only list. `POST/DELETE …/items/{id}/reservation` marks “Já comprei”
- `GET/POST /api/lists/{id}/items`, plus `PATCH /api/items/{id}` (`purchased` marks it bought), refresh, delete, and the image proxy. A new item and a refresh answer immediately with `"status": "pending"`; the photo and price arrive on a later read. `totals` is what is still to buy. `purchased_totals` is what is already bought. Viewers and the public link can see that flag and cannot change it.
- `GET /api/health` and `GET /api/health?db=1` (`db_ms` is the checkout plus `SELECT 1`)

Prices are numbers. `BRL` is formatted by the app as `R$ 1.299,90`. Other ISO currencies are left as returned by the page. The item is saved even when the store page cannot be read. A failed refresh keeps the previous photo and price. Changing the product link does not: the store name is taken from the new domain and the old photo and price are replaced by a new read. A store that answers with a block page is saved with no price and the message “Essa loja bloqueia a busca automática; edite o preço manualmente”.

## How image and price are read

The HTML is fetched server-side (the browser cannot do this because of CORS).

1. **JSON-LD** `schema.org/Product` and `Offer` (`price`, `lowPrice`, `priceCurrency`, `image`), via [extruct](https://github.com/scrapinghub/extruct) and the `application/ld+json` scripts.
2. **Open Graph / product meta**: `og:image`, `product:price:amount`, `og:price:amount`, and the matching currency tags.
3. **Microdata** (`itemprop="price"`, `priceCurrency`, `image`).
4. **Store HTML** when those graphs are missing or incomplete: Amazon buy box (`.priceToPay` / `#corePriceDisplay_desktop_feature_div`, including the split `a-price-whole` + `a-price-fraction` markup), Mercado Livre `.andes-money-amount__fraction`.
5. **Embedded JSON**, including Next.js `__NEXT_DATA__`, looking for a product-shaped object (`bestPrice`, `priceWithDiscount`, `price`, `image`).
6. A visible `R$` amount, then the best remaining `<img>`.

The request sends a normal browser `Accept-Language: pt-BR` and, first, a Chrome TLS fingerprint through `curl_cffi`. `httpx` is the fallback when that client errors. Private and loopback addresses are rejected.

## Extraction test (16 Nov 2026)

Live fetches from this environment:

| Store | Page | Result |
| --- | --- | --- |
| KaBuM! | [PS5 Slim, código 953110](https://www.kabum.com.br/produto/953110/console-playstation-5-slim-sony-1tb-midia-fisica-cfi-2114a) | Photo and **R$ 6.196,39** (BRL) from JSON-LD `Offer`. HTTP 200 via curl_cffi. |
| Americanas | [Echo Dot 5ª geração](https://www.americanas.com.br/echo-dot-5%C2%AA-geracao-smart-speaker-com-alexa-8387649/p) | Photo and **R$ 616,55** (BRL) from JSON-LD, matching `product:price:amount`. HTTP 200. |
| Amazon.com.br | [Echo Dot `B09B8XVSDP`](https://www.amazon.com.br/dp/B09B8XVSDP) | Photo from `#landingImage` (`data-old-hires`) and **R$ 584,10** from the buy box. The hidden `.a-offscreen` inside `.priceToPay` is blank, so the price is rebuilt from `a-price-whole` + `a-price-fraction`. The same page also shows R$ 649,00; the featured à vista / Pix price is the one kept. `httpx` got HTTP 503; curl_cffi got HTTP 200. |
| Magazine Luiza | [Kindle Paperwhite 12ª geração](https://www.magazineluiza.com.br/kindle-paperwhite-12a-geracao-2024-amazon-7-16gb-300-ppi-wi-fi-a-prova-de-agua-preto-metalico/p/238872100/tb/k12g/) | **Not read.** Akamai returns HTTP 403, “Não é possível acessar a página”, even with a Chrome TLS fingerprint. The item is still saved, with a placeholder and “preço indisponível”. |
| Mercado Livre | [Kindle Paperwhite, MLB25909879](https://www.mercadolivre.com.br/amazon-kindle-paperwhite-11th-genera-68-16-gb-sem-anuncios-cor-preta/p/MLB25909879) | **Not read.** The response is the account-verification wall (`/gz/account-verification`), not the product. Same graceful save. |

`pytest` covers the JSON-LD, Open Graph, microdata, Amazon buy box, Mercado Livre andes classes, and `__NEXT_DATA__` shapes without calling the stores. Those parsers are ready for Magazine Luiza and Mercado Livre when the HTML is actually the product page (a residential network often gets it; this datacenter IP does not).

## Env vars on the existing Render service

The service already live is `https://lista-desejos-backend.onrender.com`. Set these there and redeploy; do not create a second web service.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Internal URL of a Postgres database. `render.yaml` wires a free database named `lista-desejos-db`. Free Render Postgres expires 30 days after creation. A Neon or Supabase URL works the same way |
| `JWT_SECRET` | Long random string, at least 16 characters. `render.yaml` uses `generateValue: true` so a new Blueprint creates one and keeps it. If the service already exists, paste one yourself |
| `CORS_ORIGINS` | `https://cesarwillemberg.github.io` |

`PORT` is set by Render. Leave `QUERO_DB` unset in production.

## Deploy on Render (free)

The web service disk is ephemeral. A free restart wipes a SQLite file inside the container. Set `DATABASE_URL` to hosted Postgres and the list survives. With no `DATABASE_URL`, the app uses SQLite.

`render.yaml` is a Blueprint: a free web service (`lista-desejos-api`) plus a free Postgres database (`lista-desejos-db`), wired into `DATABASE_URL`, with CORS open to `https://cesarwillemberg.github.io`. Free Render Postgres expires 30 days after creation, and a workspace can have only one. A Neon or Supabase connection string in `DATABASE_URL` works the same way and does not expire as fast.

```bash
docker build -t quero-api .
docker run --rm -p 47231:10000 -e PORT=10000 quero-api
```

`GET /api/health` returns `"database": "postgres"` or `"sqlite"`. `GET /api/health?db=1` also returns `db_ms`.

### Render and Neon

You do not have to change anything for this deploy to keep working. Startup still migrates, and it returns immediately when the database is already at head.

Optional, on the existing Render service `lista-desejos-backend` (Oregon):

1. Set **Pre-Deploy Command** to `alembic upgrade head`.
2. After a deploy shows that command succeeded, you may set `WISHLY_MIGRATE=0`. If you set that variable without the pre-deploy command, later schema changes will not run.

Do not change the region. Neon can stay in `sa-east-1`. If `DATABASE_URL` already uses the `-pooler` host, it is used as-is. A direct host works too. The app opens at most 6 connections (`WISHLY_DB_POOL_MAX`).

`.github/workflows/keepalive.yml` pings `https://lista-desejos-backend.onrender.com/api/health` every 10 minutes. GitHub schedules are best-effort and often start late. It runs only from the backend repository's default branch.

## Limitations

- No headless browser. Pages that only paint the price with JavaScript, or that answer with a bot wall, come back without a price.
- Magazine Luiza and Mercado Livre block this network. Amazon’s featured price can be the Pix price rather than the card price when the page has no JSON-LD.
- One SQLite file. Fine for a single personal list, not for many users.
- The image proxy caches a few images in memory and only fetches URLs already stored on an item.
