# Proxy opcional para lojas que bloqueiam o Render

O backend da Wishly, no Render (Oregon), recebe HTTP 403 da KaBuM! e da API pública da loja. Um navegador ou outro IP nos Estados Unidos recebe a página normal. Este Worker é um gancho **desligado por padrão**: sem as variáveis no Render, nada muda.

Ele só é usado depois que a leitura direta volta bloqueada.

## O que ele faz

- Exige o cabeçalho `WISHLY_PROXY_SECRET` igual ao secret do Worker.
- Só busca domínios da lista (KaBuM!, Amazon, Magalu, Americanas, Mercado Livre, Shopee, Casas Bahia e os outros que a Wishly já reconhece).
- Devolve o HTML e o status da loja.

## Publicar o Worker

Na pasta `proxy-worker`, com o Wrangler autenticado na sua conta Cloudflare:

```bash
npx wrangler deploy
npx wrangler secret put WISHLY_PROXY_SECRET
```

Escolha uma frase longa. Anote a URL `https://wishly-fetch-proxy.<sua-conta>.workers.dev`.

## Ligar no Render

No serviço `lista-desejos-backend`, em Environment:

| Variável | Valor |
| --- | --- |
| `WISHLY_FETCH_PROXY_URL` | A URL do Worker, sem barra no final |
| `WISHLY_PROXY_SECRET` | A mesma frase do secret |

Salve e faça um deploy. Abra o item da KaBuM! e toque em **Atualizar**.

Para desligar de novo, apague `WISHLY_FETCH_PROXY_URL`. O backend volta a ler só direto.

## Alternativa ScraperAPI

Em vez do Worker, defina `SCRAPER_API_KEY` com a chave da ScraperAPI. O backend chama `https://api.scraperapi.com/` com `country_code=br`, também só quando a leitura direta está bloqueada. Se as duas variáveis existirem, o Worker é tentado primeiro.

Nenhuma das duas é obrigatória. Sem elas, o item bloqueado guarda o nome a partir do link (por exemplo `Smart Tv 50u7066 78g Dled 50 4k Wi Fi Roku Tv Aoc`) e a mensagem para editar o preço à mão.
