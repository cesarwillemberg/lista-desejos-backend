"""Read a product image and price from a store page.

Layers, in order, fill only the fields still empty:

1. JSON-LD schema.org Product / Offer (extruct, then raw script tags)
2. Open Graph and product meta tags (og:image, product:price:amount, og:price:amount)
3. Microdata
4. Store HTML that is not in those graphs (Amazon buy box, Mercado Livre andes price)
5. Embedded JSON such as __NEXT_DATA__
6. A visible "R$" on the page, then the best remaining image
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.fetch import FetchResult, fetch_html

_TYPE_SPLIT = re.compile(r"[/#:]")


@dataclass
class Extracted:
    image_url: str | None = None
    price: float | None = None
    currency: str | None = None
    title: str | None = None
    status: str = "failed"
    message: str | None = None
    image_source: str | None = None
    price_source: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    via: str | None = None


@dataclass
class _Draft:
    image: str | None = None
    image_source: str | None = None
    price: float | None = None
    currency: str | None = None
    price_source: str | None = None
    title: str | None = None
    title_source: str | None = None
    base: str = ""
    notes: list[str] = field(default_factory=list)

    def set_image(self, value, source: str) -> None:
        if self.image:
            return
        chosen = _best_image(_collect_urls(value), self.base)
        if chosen:
            self.image = chosen
            self.image_source = source

    def set_price(self, value, source: str, currency=None, *, br_text: bool = False) -> None:
        if self.price is not None:
            if not self.currency:
                self.currency = normalize_currency(currency) or currency_from_text(str(value) if value is not None else "")
            return
        parsed = parse_price(value, br_text=br_text)
        if parsed is None:
            return
        self.price = parsed
        self.price_source = source
        self.currency = (
            normalize_currency(currency)
            or currency_from_text(value if isinstance(value, str) else None)
            or self.currency
        )

    def set_title(self, value, source: str) -> None:
        if self.title or not isinstance(value, str):
            return
        text = " ".join(value.split())
        if text:
            self.title = text[:180]
            self.title_source = source


def extract_product(url: str) -> Extracted:
    fetched = fetch_html(url)
    return _from_fetch(fetched)


def extract_html(html: str, url: str, *, status: int = 200, final_url: str | None = None) -> Extracted:
    fetched = FetchResult(True, url, final_url or url, status, html or "", None, "fixture")
    return _from_fetch(fetched)


def _from_fetch(fetched: FetchResult) -> Extracted:
    final_url = fetched.final_url or fetched.url
    if not fetched.html:
        message = fetched.error or "Não foi possível acessar a página da loja."
        return Extracted(
            status="failed",
            message=message,
            http_status=fetched.status,
            final_url=final_url,
            via=fetched.via,
        )
    draft = _Draft(base=final_url)
    soup = BeautifulSoup(fetched.html, "lxml")
    _apply_extruct(fetched.html, final_url, draft)
    _apply_jsonld_scripts(soup, draft)
    _apply_meta(soup, draft)
    _apply_amazon(soup, draft)
    _apply_mercadolivre(soup, draft)
    _apply_embedded_json(soup, draft)
    _apply_visible_price(soup, draft)
    _apply_generic_image(soup, draft)
    return _finish(draft, fetched, soup)


def parse_price(value, *, br_text: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number < 0 or number > 100_000_000:
            return None
        return number
    if not isinstance(value, str):
        return None
    match = re.search(r"\d[\d\.,]*", value.replace("\xa0", " "))
    if not match:
        return None
    token = match.group(0).strip(".,")
    if not token:
        return None
    number = _number_from_token(token, br_text=br_text)
    if number is None or number < 0 or number > 100_000_000:
        return None
    return number


def normalize_currency(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if text in {"R$", "BRL"}:
        return "BRL"
    if text in {"US$", "USD", "U$"}:
        return "USD"
    if text in {"€", "EUR"}:
        return "EUR"
    if text in {"£", "GBP"}:
        return "GBP"
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    return None


def currency_from_text(value: str | None) -> str | None:
    if not value:
        return None
    if "R$" in value:
        return "BRL"
    if "US$" in value or "U$" in value:
        return "USD"
    if "€" in value:
        return "EUR"
    if "£" in value:
        return "GBP"
    return None


def store_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    rules = (
        ("mercadolivre.", "Mercado Livre"),
        ("mercadolibre.", "Mercado Livre"),
        ("amazon.", "Amazon"),
        ("magazineluiza.", "Magazine Luiza"),
        ("magalu.", "Magazine Luiza"),
        ("americanas.", "Americanas"),
        ("submarino.", "Submarino"),
        ("shoptime.", "Shoptime"),
        ("kabum.", "KaBuM!"),
        ("casasbahia.", "Casas Bahia"),
        ("pontofrio.", "Ponto"),
        ("extra.com.br", "Extra"),
        ("carrefour.", "Carrefour"),
        ("netshoes.", "Netshoes"),
    )
    for needle, name in rules:
        if needle in host:
            return name
    label = host.split(".")[0] if host else ""
    return (label[:1].upper() + label[1:]) if label else "Loja"


def _number_from_token(token: str, *, br_text: bool) -> float | None:
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        left, right = token.rsplit(",", 1)
        if right.isdigit() and 1 <= len(right) <= 2:
            token = left.replace(".", "") + "." + right
        else:
            token = token.replace(",", "")
    elif br_text and re.fullmatch(r"\d{1,3}(\.\d{3})+", token):
        token = token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def _finish(draft: _Draft, fetched: FetchResult, soup: BeautifulSoup) -> Extracted:
    if draft.image is None and draft.price is None:
        message = _block_message(soup, fetched) or fetched.error or "Não encontramos foto nem preço nessa página."
        status = "failed"
    elif draft.image and draft.price is not None:
        message = None
        status = "ok"
    elif draft.image:
        message = "Encontramos a foto, mas o preço não estava na página. Você pode informá-lo."
        status = "partial"
    else:
        message = "Encontramos o preço, mas não a foto. Você pode informar a imagem."
        status = "partial"
    return Extracted(
        image_url=draft.image,
        price=round(draft.price, 2) if draft.price is not None else None,
        currency=draft.currency,
        title=draft.title,
        status=status,
        message=message,
        image_source=draft.image_source,
        price_source=draft.price_source,
        http_status=fetched.status,
        final_url=fetched.final_url,
        via=fetched.via,
    )


def _block_message(soup: BeautifulSoup, fetched: FetchResult) -> str | None:
    html = str(soup).lower()
    final = (fetched.final_url or fetched.url or "").lower()
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if "account-verification" in final or "suspicious-traffic" in html or "gz-account-verification" in html:
        return "O Mercado Livre pediu uma verificação de acesso e não entregou a página do produto."
    if "não é possível acessar" in title or "nao e possivel acessar" in title:
        if "magalu" in html or "magazine luiza" in html or "magazineluiza" in final:
            return "A Magazine Luiza recusou o acesso automático (erro 403)."
        return "A loja recusou o acesso automático a esta página."
    if fetched.status in {403, 429, 503} and "amazon." in final:
        return "A Amazon não entregou a página do produto."
    if "validatecaptcha" in html or "robot check" in title:
        return "A loja exibiu uma verificação anti-robô em vez do produto."
    if fetched.status and fetched.status >= 400:
        return f"A loja respondeu com o erro {fetched.status}."
    return None


def _apply_extruct(html: str, url: str, draft: _Draft) -> None:
    try:
        import extruct

        data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "microdata", "opengraph"])
    except Exception:
        return
    for node in data.get("json-ld") or []:
        _apply_jsonld_node(node, draft)
    for block in data.get("opengraph") or []:
        _apply_opengraph_block(block, draft)
    for item in data.get("microdata") or []:
        _apply_microdata_item(item, draft)


def _apply_jsonld_scripts(soup: BeautifulSoup, draft: _Draft) -> None:
    for script in soup.find_all("script"):
        kind = (script.get("type") or "").lower()
        if "ld+json" not in kind:
            continue
        raw = script.string or script.get_text() or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _apply_jsonld_node(payload, draft)


def _apply_jsonld_node(node, draft: _Draft) -> None:
    products = [item for item in _walk_nodes(node) if "Product" in _types(item)]
    if not products:
        return
    products.sort(key=lambda item: _product_score(item, draft.base), reverse=True)
    product = products[0]
    draft.set_title(_first_str(product.get("name")), "json-ld")
    draft.set_image(product.get("image"), "json-ld")
    for offer in _offers(product):
        price, currency = _price_from_offer(offer)
        if price is None:
            continue
        draft.set_price(price, "json-ld", currency)
        if draft.price is not None:
            break


def _apply_opengraph_block(block: dict, draft: _Draft) -> None:
    props: dict[str, list] = {}
    for pair in block.get("properties") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            props.setdefault(str(pair[0]), []).append(pair[1])
    for key, value in block.items():
        if key.startswith("og:") or key.startswith("product:"):
            props.setdefault(key, []).append(value)
    images = props.get("og:image") or props.get("og:image:secure_url") or []
    draft.set_image(images, "opengraph")
    draft.set_title(_first_str(props.get("og:title")), "opengraph")
    amount = _first_str(props.get("product:price:amount") or props.get("og:price:amount"))
    currency = _first_str(props.get("product:price:currency") or props.get("og:price:currency"))
    if amount:
        draft.set_price(amount, "opengraph", currency)


def _apply_meta(soup: BeautifulSoup, draft: _Draft) -> None:
    def content(*keys: str) -> str | None:
        for key in keys:
            tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return None

    draft.set_image(
        [content("og:image"), content("og:image:secure_url"), content("twitter:image")],
        "meta",
    )
    draft.set_title(content("og:title"), "meta")
    amount = content("product:price:amount", "og:price:amount")
    currency = content("product:price:currency", "og:price:currency")
    if amount:
        draft.set_price(amount, "meta", currency)


def _apply_microdata_item(item: dict, draft: _Draft) -> None:
    if "Product" not in _types(item) and "product" not in str(item.get("type") or "").lower():
        return
    props = item.get("properties") or {}
    draft.set_title(_first_str(props.get("name")), "microdata")
    draft.set_image(props.get("image"), "microdata")
    offers = props.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    price = _first_str(props.get("price"))
    currency = _first_str(props.get("priceCurrency"))
    for offer in offers if isinstance(offers, list) else []:
        if not isinstance(offer, dict):
            continue
        offer_props = offer.get("properties") or offer
        found = _first_str(offer_props.get("price") or offer_props.get("lowPrice"))
        if found:
            price = found
            currency = _first_str(offer_props.get("priceCurrency")) or currency
            break
    if price:
        draft.set_price(price, "microdata", currency, br_text=True)


def _apply_amazon(soup: BeautifulSoup, draft: _Draft) -> None:
    containers = []
    for selector in ("#corePriceDisplay_desktop_feature_div", "#corePrice_feature_div", ".priceToPay"):
        found = soup.select_one(selector)
        if found is not None and found not in containers:
            containers.append(found)
    for container in containers:
        text = _amazon_price_text(container)
        if text:
            draft.set_price(text, "html:amazon", br_text=True)
            if draft.price is not None:
                break
    if draft.price is None:
        for el in soup.select("span.a-offscreen"):
            text = el.get_text(" ", strip=True)
            if any(ch.isdigit() for ch in text):
                draft.set_price(text, "html:amazon", br_text=True)
                if draft.price is not None:
                    break
    image = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront") or soup.select_one("#main-image")
    if image is not None:
        chosen = image.get("data-old-hires") or image.get("data-a-hires")
        if not chosen:
            dynamic = image.get("data-a-dynamic-image")
            if dynamic:
                try:
                    parsed = json.loads(dynamic)
                    if isinstance(parsed, dict) and parsed:
                        chosen = max(
                            parsed.items(),
                            key=lambda item: item[1][0] if isinstance(item[1], list) and item[1] else 0,
                        )[0]
                except (json.JSONDecodeError, TypeError, ValueError):
                    chosen = None
        draft.set_image(chosen or image.get("src"), "html:amazon")


def _amazon_price_text(container) -> str | None:
    offscreen = container.select_one(".a-offscreen")
    if offscreen is not None:
        text = offscreen.get_text(" ", strip=True)
        if any(ch.isdigit() for ch in text):
            return text
    whole = container.select_one(".a-price-whole")
    if whole is None:
        return None
    direct = "".join(str(node) for node in whole.find_all(string=True, recursive=False))
    direct = re.sub(r"[^\d.]", "", direct)
    fraction_el = container.select_one(".a-price-fraction")
    fraction = re.sub(r"[^\d]", "", fraction_el.get_text(strip=True) if fraction_el else "")
    symbol_el = container.select_one(".a-price-symbol")
    symbol = symbol_el.get_text(strip=True) if symbol_el else ""
    if not direct:
        return None
    if fraction:
        return f"{symbol} {direct},{fraction}".strip()
    return f"{symbol} {direct}".strip()


def _apply_mercadolivre(soup: BeautifulSoup, draft: _Draft) -> None:
    fraction = soup.select_one(".andes-money-amount__fraction")
    if fraction is not None:
        raw = fraction.get_text(strip=True)
        cents = soup.select_one(".andes-money-amount__cents")
        if cents is not None and cents.get_text(strip=True).isdigit():
            raw = f"{raw},{cents.get_text(strip=True)}"
        draft.set_price(raw, "html:mercadolivre", "BRL", br_text=True)
    image = soup.select_one(".ui-pdp-gallery__figure img") or soup.select_one("img.ui-pdp-image")
    if image is not None:
        draft.set_image(image.get("data-zoom") or image.get("data-src") or image.get("src"), "html:mercadolivre")


def _apply_embedded_json(soup: BeautifulSoup, draft: _Draft) -> None:
    blobs: list[tuple[str, str]] = []
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data is not None:
        blobs.append(("json:next-data", next_data.string or next_data.get_text() or ""))
    for script in soup.find_all("script"):
        if script.get("id") == "__NEXT_DATA__":
            continue
        kind = (script.get("type") or "").lower()
        if kind == "application/json":
            blobs.append(("json:embedded", script.string or script.get_text() or ""))
    best: tuple | None = None
    for source, raw in blobs:
        raw = raw.strip()
        if not raw or raw[0] not in "{[":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _best_embedded(payload)
        if found and (best is None or found[0] > best[0]):
            best = (found[0], source, found[1], found[2], found[3])
    if not best:
        return
    _, source, price, currency, image = best
    draft.set_price(price, source, currency, br_text=isinstance(price, str))
    draft.set_image(image, source)


def _apply_visible_price(soup: BeautifulSoup, draft: _Draft) -> None:
    if draft.price is not None:
        return
    text = soup.get_text("\n", strip=True)
    for match in re.finditer(r"R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{2})?", text):
        start = max(0, match.start() - 40)
        context = text[start : match.end() + 24].lower()
        if any(word in context for word in ("frete", "parcela", "juros", "cupom")):
            continue
        draft.set_price(match.group(0), "html:text", "BRL", br_text=True)
        if draft.price is not None:
            return


def _apply_generic_image(soup: BeautifulSoup, draft: _Draft) -> None:
    if draft.image:
        return
    candidates = []
    for image in soup.find_all("img"):
        src = image.get("data-old-hires") or image.get("data-zoom") or image.get("data-src") or image.get("src")
        width = image.get("width")
        try:
            if width is not None and int(str(width)) < 80:
                continue
        except ValueError:
            pass
        candidates.append(src)
    draft.set_image(candidates, "html:img")


def _best_embedded(payload) -> tuple | None:
    best = None

    def walk(node, path: tuple[str, ...]) -> None:
        nonlocal best
        if isinstance(node, dict):
            score, price, currency, image = _score_embedded(node)
            joined = "/".join(path).lower()
            if any(word in joined for word in ("recommend", "related", "breadcrumb", "review", "advert", "freight", "shipping")):
                score -= 6
            if score >= 7 and (best is None or score > best[0]):
                best = (score, price, currency, image)
            for key, value in node.items():
                walk(value, path + (str(key),))
        elif isinstance(node, list):
            for value in node[:50]:
                walk(value, path)

    walk(payload, ())
    return best


def _score_embedded(node: dict) -> tuple:
    price, currency = _price_from_mapping(node)
    image = _best_image(_collect_urls(_image_value(node)), "")
    types = _types(node)
    score = 0
    if price is not None:
        score += 4
    if image:
        score += 3
    if node.get("name") or node.get("title"):
        score += 1
    if "Product" in types:
        score += 4
    if price is None or not (image or "Product" in types):
        return 0, None, None, None
    return score, price, currency, image


def _price_from_mapping(node: dict) -> tuple:
    for key in (
        "priceWithDiscount",
        "salePrice",
        "finalPrice",
        "bestPrice",
        "currentPrice",
        "pixPrice",
        "offerPrice",
        "lowPrice",
        "price",
    ):
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, dict):
            nested_price, nested_currency = _price_from_mapping(value)
            if nested_price is not None:
                return nested_price, nested_currency or value.get("currency") or value.get("priceCurrency")
            continue
        if isinstance(value, list) or isinstance(value, bool):
            continue
        currency = node.get("priceCurrency") or node.get("currency")
        return value, currency
    return None, None


def _image_value(node: dict):
    for key in ("image", "imageUrl", "image_url", "thumbnail", "picture", "photo", "images"):
        if node.get(key):
            return node[key]
    return None


def _offers(product: dict):
    offers = product.get("offers")
    if isinstance(offers, list):
        nodes = offers
    elif isinstance(offers, dict):
        nodes = [offers]
    else:
        nodes = []
    for node in nodes:
        if isinstance(node, dict):
            yield node


def _price_from_offer(offer: dict) -> tuple:
    specs = offer.get("priceSpecification")
    if isinstance(specs, dict):
        specs = [specs]
    if isinstance(specs, list):
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            label = " ".join(_types(spec)) + " " + str(spec.get("priceType") or "")
            if "ListPrice" in label or "StrikethroughPrice" in label:
                continue
            if spec.get("price") is not None:
                return spec.get("price"), spec.get("priceCurrency") or offer.get("priceCurrency")
    if offer.get("lowPrice") is not None and "AggregateOffer" in _types(offer):
        return offer.get("lowPrice"), offer.get("priceCurrency")
    if offer.get("price") is not None:
        return offer.get("price"), offer.get("priceCurrency")
    return None, None


def _product_score(product: dict, page_url: str) -> int:
    score = 0
    identity = str(product.get("url") or product.get("@id") or "")
    if page_url and identity and (page_url.rstrip("/") in identity or identity.rstrip("/") in page_url):
        score += 5
    if product.get("offers"):
        score += 2
    if product.get("image"):
        score += 1
    return score


def _walk_nodes(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_nodes(item)
        return
    if isinstance(node, dict):
        if "@graph" in node:
            yield from _walk_nodes(node["@graph"])
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk_nodes(value)


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or node.get("type")
    values = raw if isinstance(raw, list) else [raw]
    names = []
    for value in values:
        if not isinstance(value, str):
            continue
        piece = _TYPE_SPLIT.split(value)[-1]
        if piece:
            names.append(piece)
    return names


def _first_str(value) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        for item in value:
            found = _first_str(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("name", "url", "contentUrl"):
            found = _first_str(value.get(key))
            if found:
                return found
    return None


def _collect_urls(value) -> list[str]:
    found: list[str] = []

    def add(item) -> None:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            for key in ("url", "contentUrl", "thumbnailUrl", "src"):
                if isinstance(item.get(key), str):
                    found.append(item[key])
                    break
        elif isinstance(item, list):
            for child in item:
                add(child)

    add(value)
    return found


def _best_image(urls: list[str], base: str) -> str | None:
    best = None
    best_rank = -10_000
    for raw in urls:
        absolute = _absolute(raw, base)
        if not absolute or not _usable_image(absolute):
            continue
        rank = _image_rank(absolute)
        if rank > best_rank:
            best = absolute
            best_rank = rank
    return best


def _usable_image(url: str) -> bool:
    lowered = url.lower()
    if ".svg" in lowered or "sprite" in lowered or "1x1" in lowered or "pixel" in lowered:
        return False
    if any(token in lowered for token in ("/logo", "logo-", "error-robot", "blank.gif", "spacer.")):
        return False
    return True


def _absolute(value: str | None, base: str) -> str | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("data:"):
        return None
    if text.startswith("//"):
        text = "https:" + text
    absolute = urljoin(base or "", text)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return absolute


def _image_rank(url: str) -> int:
    lowered = url.lower()
    score = 0
    if any(token in lowered for token in ("large", "zoom", "big", "_ac_sl", "hires")):
        score += 4
    if any(token in lowered for token in ("medium", "regular")):
        score += 1
    if any(token in lowered for token in ("thumb", "small", "tiny", "icon", "sprite", "logo", "pixel", "1x1")):
        score -= 4
    if ".svg" in lowered or lowered.endswith(".gif"):
        score -= 3
    return score
