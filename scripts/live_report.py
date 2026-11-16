"""Fetch a few real product pages and print what the extractor found."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extractor import extract_product, store_name

CASES = [
    (
        "KaBuM!",
        "https://www.kabum.com.br/produto/953110/console-playstation-5-slim-sony-1tb-midia-fisica-cfi-2114a",
    ),
    (
        "Americanas",
        "https://www.americanas.com.br/echo-dot-5%C2%AA-geracao-smart-speaker-com-alexa-8387649/p",
    ),
    ("Amazon.com.br", "https://www.amazon.com.br/dp/B09B8XVSDP"),
    (
        "Magazine Luiza",
        "https://www.magazineluiza.com.br/kindle-paperwhite-12a-geracao-2024-amazon-7-16gb-300-ppi-wi-fi-a-prova-de-agua-preto-metalico/p/238872100/tb/k12g/",
    ),
    (
        "Mercado Livre",
        "https://www.mercadolivre.com.br/amazon-kindle-paperwhite-11th-genera-68-16-gb-sem-anuncios-cor-preta/p/MLB25909879",
    ),
]


def main() -> None:
    rows = []
    for label, url in CASES:
        result = extract_product(url)
        row = {
            "store": label,
            "detected_store": store_name(url),
            "status": result.status,
            "price": result.price,
            "currency": result.currency,
            "image": result.image_url,
            "price_source": result.price_source,
            "image_source": result.image_source,
            "http_status": result.http_status,
            "via": result.via,
            "message": result.message,
            "title": result.title,
            "url": url,
        }
        rows.append(row)
        print(
            f"{label}: status={result.status} price={result.price} {result.currency} "
            f"src={result.price_source}/{result.image_source} http={result.http_status} via={result.via}"
        )
        if result.message:
            print(f"  message: {result.message}")
        if result.image_url:
            print(f"  image: {result.image_url[:140]}")
    out = Path(__file__).resolve().parents[1] / "data" / "live_report.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
