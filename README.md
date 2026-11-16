# Quero — backend

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
| `QUERO_DB` | `data/wishlist.db` | SQLite file |
| `QUERO_WEB_DIR` | `../frontend/build/web` when that folder exists | Optional Flutter web build served on `/` |
| `DATABASE_URL` | unset | Postgres URL (`postgres://` or `postgresql://`). When set, SQLite is not used |
| `JWT_SECRET` | required | Signs login tokens. At least 16 characters. On Render, `render.yaml` generates it |
| `CORS_ORIGINS` | `https://cesarwillemberg.github.io` | Comma-separated browser origins. Localhost is always allowed |

```bash
pytest
python scripts/live_report.py
```

Accounts, lists, and items are created by Alembic (`alembic upgrade head` runs on startup). If this database still has the old single `items` table, those rows move into **Minha lista** for the first account that signs up. A brand-new Postgres database starts empty. The free Render disk does not keep SQLite across restarts, so production must set `DATABASE_URL`.

## API

- `POST /api/auth/register` and `POST /api/auth/login` — `{ "email", "password" }` returns a JWT (30 days). Send `Authorization: Bearer …`
- `GET /api/auth/me`
- `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}`
- `POST /api/lists/{id}/share`, `POST …/share/regenerate`, `DELETE …/share`
- `GET /api/share/{token}` — public read-only list. `POST/DELETE …/items/{id}/reservation` marks “Já comprei”
- `GET/POST /api/lists/{id}/items`, plus `PATCH /api/items/{id}`, refresh, delete, and the image proxy
- `GET /api/health`

Prices are numbers. `BRL` is formatted by the app as `R$ 1.299,90`. Other ISO currencies are left as returned by the page. The item is saved even when the store page cannot be read. A failed refresh keeps the previous photo and price.

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

`GET /api/health` returns `"database": "postgres"` or `"sqlite"`.

## Limitations

- No headless browser. Pages that only paint the price with JavaScript, or that answer with a bot wall, come back without a price.
- Magazine Luiza and Mercado Livre block this network. Amazon’s featured price can be the Pix price rather than the card price when the page has no JSON-LD.
- One SQLite file. Fine for a single personal list, not for many users.
- The image proxy caches a few images in memory and only fetches URLs already stored on an item.
