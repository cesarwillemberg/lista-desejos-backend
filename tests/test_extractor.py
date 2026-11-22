from pathlib import Path

import pytest

from app.extractor import (
    PARSE_MESSAGE,
    extract_html,
    extract_product,
    fallback_product_name,
    parse_price,
    product_from_kabum_catalog,
)
from app.fetch import ensure_public_url

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("raw", "br_text", "expected"),
    [
        ("6196.39", False, 6196.39),
        (6196.39, False, 6196.39),
        ("R$ 6.196,39", True, 6196.39),
        ("R$\u00a0584,10", True, 584.10),
        ("1.299,90", True, 1299.90),
        ("649,00", True, 649.0),
        ("1,299.00", False, 1299.0),
        ("10,5", True, 10.5),
        ("1.299", True, 1299.0),
        ("", True, None),
        ("grátis", True, None),
        ("R$ 10,00 à vista", True, 10.0),
    ],
)
def test_parse_price(raw, br_text, expected):
    assert parse_price(raw, br_text=br_text) == expected


def test_gzip_body_is_decompressed_before_parsing():
    import gzip

    from app.fetch import decode_body

    page = _html("kabum.html").encode()
    html = decode_body(gzip.compress(page), "gzip")
    result = extract_html(html, "https://www.kabum.com.br/produto/953110")
    assert result.price == 6196.39
    assert "__NEXT" not in html[:2]


def test_kabum_jsonld_price_and_image():
    result = extract_html(_html("kabum.html"), "https://www.kabum.com.br/produto/953110")
    assert result.status == "ok"
    assert result.price == 6196.39
    assert result.currency == "BRL"
    assert result.price_source == "json-ld"
    assert result.image_url and "images.kabum.com.br" in result.image_url
    assert "large" in result.image_url


def test_kabum_next_data_prefers_discount_over_list_price():
    result = extract_html(_html("kabum_next.html"), "https://www.kabum.com.br/produto/520369")
    assert result.status == "ok"
    assert result.price == 2499.99
    assert result.currency == "BRL"
    assert result.price_source == "json:next-data"
    assert result.title and "5700X3D" in result.title
    assert result.image_url and "large.jpg" in result.image_url


def test_kabum_offer_list_and_product_meta():
    result = extract_html(_html("kabum_offers.html"), "https://www.kabum.com.br/produto/1")
    assert result.status == "ok"
    assert result.price == 189.90
    assert result.currency == "BRL"
    assert result.price_source == "json-ld"
    assert result.image_url and result.image_url.endswith("teclado-large.jpg")


def test_aggregate_offer_low_price():
    result = extract_html(_html("aggregate_offer.html"), "https://loja.example.com/mouse")
    assert result.price == 89.90
    assert result.currency == "BRL"
    assert result.image_url == "https://cdn.example.com/mouse-large.jpg"


def test_americanas_jsonld_and_meta():
    result = extract_html(
        _html("americanas.html"),
        "https://www.americanas.com.br/echo-dot-5ª-geracao-smart-speaker-com-alexa-8387649/p",
    )
    assert result.status == "ok"
    assert result.price == 616.55
    assert result.currency == "BRL"
    assert result.image_url and "vtexassets.com" in result.image_url


def test_amazon_buy_box_not_list_price():
    result = extract_html(_html("amazon.html"), "https://www.amazon.com.br/dp/B09B8XVSDP")
    assert result.status == "ok"
    assert result.price == 584.10
    assert result.currency == "BRL"
    assert result.price_source == "html:amazon"
    assert result.image_url and result.image_url.endswith("_AC_SL1000_.jpg")


def test_open_graph_only():
    html = """
    <html><head>
      <meta property="og:title" content="Cadeira de escritório">
      <meta property="og:image" content="https://cdn.example.com/cadeira.jpg">
      <meta property="product:price:amount" content="899.90">
      <meta property="product:price:currency" content="BRL">
    </head><body></body></html>
    """
    result = extract_html(html, "https://loja.example.com/cadeira")
    assert result.price == 899.90
    assert result.currency == "BRL"
    assert result.image_url == "https://cdn.example.com/cadeira.jpg"
    assert result.price_source in {"opengraph", "meta"}


def test_microdata():
    html = """
    <html><body>
      <div itemscope itemtype="https://schema.org/Product">
        <span itemprop="name">Fone</span>
        <img itemprop="image" src="https://cdn.example.com/fone.jpg">
        <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
          <span itemprop="price" content="199.90">199,90</span>
          <span itemprop="priceCurrency" content="BRL">BRL</span>
        </div>
      </div>
    </body></html>
    """
    result = extract_html(html, "https://loja.example.com/fone")
    assert result.price == 199.90
    assert result.currency == "BRL"
    assert result.image_url == "https://cdn.example.com/fone.jpg"


def test_mercadolivre_saved_page():
    result = extract_html(_html("mercadolivre.html"), "https://www.mercadolivre.com.br/p/MLB25909879")
    assert result.status == "ok"
    assert result.price == 1299.90
    assert result.currency == "BRL"
    assert result.price_source == "html:mercadolivre"
    assert result.image_url and result.image_url.endswith("123-F.jpg")


def test_mercadolivre_andes_markup():
    html = """
    <html><head>
      <meta property="og:image" content="https://http2.mlstatic.com/D_NQ_NP_123-O.jpg">
    </head><body>
      <span class="andes-money-amount__fraction">1.299</span>
      <span class="andes-money-amount__cents">90</span>
    </body></html>
    """
    result = extract_html(html, "https://www.mercadolivre.com.br/produto/p/MLB1")
    assert result.status == "ok"
    assert result.price == 1299.90
    assert result.currency == "BRL"
    assert result.price_source == "html:mercadolivre"
    assert "mlstatic.com" in result.image_url


def test_next_data_product():
    html = """
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"product":{
        "name":"Kindle",
        "price":{"bestPrice":1034.1,"currency":"BRL"},
        "image":"https://cdn.example.com/kindle-large.jpg"
      }}}}
      </script>
    </body></html>
    """
    result = extract_html(html, "https://www.magazineluiza.com.br/kindle/p/1")
    assert result.status == "ok"
    assert result.price == 1034.1
    assert result.currency == "BRL"
    assert result.image_url.endswith("kindle-large.jpg")
    assert result.price_source == "json:next-data"


def test_visible_brl_text_fallback():
    html = "<html><body><p>Por apenas R$ 42,50 à vista</p><img src='https://cdn.example.com/x-large.jpg'></body></html>"
    result = extract_html(html, "https://loja.example.com/x")
    assert result.price == 42.50
    assert result.currency == "BRL"
    assert result.image_url == "https://cdn.example.com/x-large.jpg"


def test_magalu_block_page_has_no_price():
    result = extract_html(
        _html("magalu_block.html"),
        "https://www.magazineluiza.com.br/kindle/p/238872100",
        status=403,
        final_url="https://www.magazineluiza.com.br/kindle/p/238872100",
    )
    assert result.status == "failed"
    assert result.price is None
    assert result.image_url is None
    assert result.message == "Essa loja bloqueia a busca automática; edite o preço manualmente"


def test_mercadolivre_verification_page():
    html = "<html><head><title>Mercado Libre</title></head><body>account-verification</body></html>"
    result = extract_html(
        html,
        "https://www.mercadolivre.com.br/p/MLB1",
        final_url="https://www.mercadolivre.com.br/gz/account-verification?go=1",
    )
    assert result.status == "failed"
    assert "Mercado Livre" in (result.message or "")


def test_akamai_mention_inside_a_script_is_not_a_block():
    html = "<html><head><script>akamai access denied</script><title>TV AOC</title></head><body><p>Sem preço visível</p></body></html>"
    result = extract_html(html, "https://www.kabum.com.br/produto/1071513/smart-tv", status=200)
    assert result.message == PARSE_MESSAGE
    assert (result.scrape_error or "").startswith("parse")


def test_kabum_catalog_prefers_discount_and_cleans_title():
    result = product_from_kabum_catalog(
        {
            "attributes": {
                "title": "Smart Tv 50u7066 - KaBuM!",
                "price": 2080,
                "price_with_discount": 1788.8,
                "images": ["https://images.kabum.com.br/xlarge.jpg"],
            }
        },
        "https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g",
    )
    assert result is not None
    assert result.price == 1788.8
    assert result.currency == "BRL"
    assert result.title == "Smart Tv 50u7066"
    assert result.image_url.endswith("xlarge.jpg")
    assert result.price_source == "kabum-api"


def test_kabum_api_is_used_before_the_html_page(monkeypatch):
    monkeypatch.setattr(
        "app.extractor.fetch_kabum_product",
        lambda url: (
            product_from_kabum_catalog(
                {"attributes": {"title": "Smart Tv", "price_with_discount": 1788.8, "images": ["https://images.kabum.com.br/a.jpg"]}},
                url,
            ),
            "ok kabum-api",
        ),
    )

    def explode(_url):
        raise AssertionError("html fetch")

    monkeypatch.setattr("app.extractor.fetch_html", explode)
    result = extract_product("https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g")
    assert result.price == 1788.8
    assert result.via == "kabum-api"


def test_slug_becomes_a_readable_fallback_name():
    assert fallback_product_name("https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g-dled").startswith("Smart Tv 50u7066 78g")


def test_private_urls_are_rejected():
    for url in (
        "http://127.0.0.1/secret",
        "http://localhost/x",
        "http://10.1.2.3/x",
        "http://192.168.0.8/x",
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            ensure_public_url(url)
