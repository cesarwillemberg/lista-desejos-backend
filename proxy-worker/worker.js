/**
 * Proxy opcional do Wishly. Fica desligado até o backend receber
 * WISHLY_FETCH_PROXY_URL apontando para este Worker.
 *
 * O pedido precisa do cabeçalho WISHLY_PROXY_SECRET com o mesmo valor
 * gravado no secret do Worker. Só busca domínios da lista.
 */

const ALLOWED_HOSTS = [
  "kabum.com.br",
  "grupokabum.com.br",
  "amazon.com.br",
  "amazon.com",
  "magazineluiza.com.br",
  "magalu.com",
  "americanas.com.br",
  "mercadolivre.com.br",
  "mercadolibre.com",
  "shopee.com.br",
  "casasbahia.com.br",
  "pontofrio.com.br",
  "extra.com.br",
  "carrefour.com.br",
  "netshoes.com.br",
  "submarino.com.br",
  "shoptime.com.br",
];

function allowedHost(hostname) {
  const host = hostname.toLowerCase().replace(/^www\./, "");
  return ALLOWED_HOSTS.some((item) => host === item || host.endsWith(`.${item}`));
}

export default {
  async fetch(request, env) {
    if (request.method !== "GET") {
      return new Response("Método não permitido.", { status: 405 });
    }
    const secret = env.WISHLY_PROXY_SECRET || "";
    if (!secret || request.headers.get("WISHLY_PROXY_SECRET") !== secret) {
      return new Response("Segredo ausente ou incorreto.", { status: 403 });
    }
    const target = new URL(request.url).searchParams.get("url") || "";
    let parsed;
    try {
      parsed = new URL(target);
    } catch {
      return new Response("URL inválida.", { status: 400 });
    }
    if (!["http:", "https:"].includes(parsed.protocol) || !allowedHost(parsed.hostname)) {
      return new Response("Esse domínio não está na lista.", { status: 403 });
    }
    const upstream = await fetch(parsed.toString(), {
      redirect: "follow",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
      },
    });
    const headers = new Headers();
    const type = upstream.headers.get("content-type");
    if (type) headers.set("content-type", type);
    headers.set("x-upstream-status", String(upstream.status));
    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
