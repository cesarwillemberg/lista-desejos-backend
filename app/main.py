"""HTTP API for the Quero wishlist."""

from __future__ import annotations

import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from app import db
from app.extractor import Extracted, extract_product, store_name
from app.fetch import ensure_public_url, fetch_image

_image_cache: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()
_image_lock = Lock()
_IMAGE_CACHE_MAX = 24


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    url: str


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    image_url: str | None = None
    price: float | None = None
    currency: str | None = None


def _web_dir() -> Path:
    configured = os.environ.get("QUERO_WEB_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "build" / "web"


def _totals(items: list[dict]) -> tuple[list[dict], int]:
    buckets: dict[str, float] = {}
    unpriced = 0
    for item in items:
        if item["price"] is None:
            unpriced += 1
            continue
        currency = item["currency"] or ""
        buckets[currency] = buckets.get(currency, 0.0) + float(item["price"])
    ordered = sorted(buckets.items(), key=lambda pair: (pair[0] != "BRL", pair[0] == "", pair[0]))
    totals = [{"currency": key or None, "amount": round(value, 2)} for key, value in ordered]
    return totals, unpriced


def _clean_name(name: str) -> str:
    text = " ".join((name or "").split())
    if not text:
        raise HTTPException(400, "Informe um nome.")
    if len(text) > 160:
        raise HTTPException(400, "O nome pode ter no máximo 160 caracteres.")
    return text


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().upper()
    if not text:
        return None
    if len(text) != 3 or not text.isalpha():
        raise HTTPException(400, "Use um código de moeda com 3 letras, como BRL.")
    return text


def _fields_from_extraction(extracted: Extracted, url: str) -> dict:
    return {
        "image_url": extracted.image_url,
        "price": extracted.price,
        "currency": extracted.currency,
        "store": store_name(url),
        "status": extracted.status,
        "message": extracted.message,
        "image_source": extracted.image_source,
        "price_source": extracted.price_source,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    web = _web_dir()
    if os.environ.get("QUERO_DISABLE_WEB") != "1" and (web / "index.html").is_file():
        _app.mount("/", StaticFiles(directory=str(web), html=True), name="web")
    yield


_PAGES_ORIGIN = "https://cesarwillemberg.github.io"


def _cors_origins() -> list[str]:
    configured = os.environ.get("CORS_ORIGINS", _PAGES_ORIGIN)
    origins: list[str] = []
    for part in configured.split(","):
        origin = part.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    if _PAGES_ORIGIN not in origins:
        origins.append(_PAGES_ORIGIN)
    return origins


app = FastAPI(title="Quero", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"ok": True, "database": db.engine_name()}


@app.get("/api/items")
def get_items():
    items = db.list_items()
    totals, unpriced = _totals(items)
    return {"items": items, "totals": totals, "unpriced_count": unpriced}


@app.post("/api/items", status_code=201)
def create_item(body: ItemCreate):
    name = _clean_name(body.name)
    url = (body.url or "").strip()
    if len(url) > 2000:
        raise HTTPException(400, "Esse link é longo demais.")
    ensure_public_url(url)
    extracted = extract_product(url)
    return db.insert_item({"name": name, "url": url, **_fields_from_extraction(extracted, url)})


@app.patch("/api/items/{item_id}")
def patch_item(item_id: str, body: ItemUpdate):
    current = db.get_item(item_id)
    if current is None:
        raise HTTPException(404, "Item não encontrado.")
    sent = body.model_fields_set
    changes: dict = {}
    if "name" in sent:
        changes["name"] = _clean_name(body.name or "")
    if "image_url" in sent:
        raw = (body.image_url or "").strip()
        if not raw:
            changes["image_url"] = None
            changes["image_source"] = "manual"
        else:
            if len(raw) > 2000:
                raise HTTPException(400, "A URL da imagem é longa demais.")
            ensure_public_url(raw)
            changes["image_url"] = raw
            changes["image_source"] = "manual"
    if "price" in sent:
        if body.price is None:
            changes["price"] = None
            changes["price_source"] = "manual"
        else:
            if body.price < 0 or body.price > 100_000_000:
                raise HTTPException(400, "Informe um preço válido.")
            changes["price"] = round(float(body.price), 2)
            changes["price_source"] = "manual"
    if "currency" in sent:
        changes["currency"] = _clean_currency(body.currency)
    if not changes:
        return current
    image = changes["image_url"] if "image_url" in changes else current["image_url"]
    price = changes["price"] if "price" in changes else current["price"]
    if image and price is not None:
        changes["status"] = "ok"
        changes["message"] = None
    elif image or price is not None:
        changes["status"] = "partial"
        if price is None:
            changes["message"] = "Preço informado manualmente ainda está vazio."
        else:
            changes["message"] = "Foto informada manualmente ainda está vazia."
    else:
        changes["status"] = "failed"
        changes["message"] = "Foto e preço não informados."
    updated = db.update_item(item_id, changes)
    if updated is None:
        raise HTTPException(404, "Item não encontrado.")
    return updated


@app.post("/api/items/{item_id}/refresh")
def refresh_item(item_id: str):
    current = db.get_item(item_id)
    if current is None:
        raise HTTPException(404, "Item não encontrado.")
    extracted = extract_product(current["url"])
    changes: dict = {"store": store_name(current["url"])}
    if extracted.image_url:
        changes["image_url"] = extracted.image_url
        changes["image_source"] = extracted.image_source
    if extracted.price is not None:
        changes["price"] = extracted.price
        changes["currency"] = extracted.currency
        changes["price_source"] = extracted.price_source
    image = changes.get("image_url", current["image_url"])
    price = changes.get("price", current["price"])
    if extracted.image_url is None and extracted.price is None:
        kept = "Mantivemos a foto e o preço que já estavam salvos."
        detail = extracted.message or "Não foi possível ler a página agora."
        changes["message"] = f"{detail} {kept}"
        changes["status"] = current["status"]
    elif image and price is not None:
        changes["status"] = "ok"
        changes["message"] = None
    elif image:
        changes["status"] = "partial"
        changes["message"] = "Atualizamos a foto, mas o preço continua indisponível."
    else:
        changes["status"] = "partial"
        changes["message"] = "Atualizamos o preço, mas a foto continua indisponível."
    updated = db.update_item(item_id, changes)
    if updated is None:
        raise HTTPException(404, "Item não encontrado.")
    return updated


@app.delete("/api/items/{item_id}", status_code=204)
def remove_item(item_id: str):
    if not db.delete_item(item_id):
        raise HTTPException(404, "Item não encontrado.")
    return Response(status_code=204)


@app.get("/api/items/{item_id}/image")
def item_image(item_id: str):
    item = db.get_item(item_id)
    if item is None or not item["image_url"]:
        raise HTTPException(404, "Imagem indisponível.")
    key = (item_id, item["image_url"])
    with _image_lock:
        cached = _image_cache.get(key)
        if cached:
            _image_cache.move_to_end(key)
            content, media = cached
            return Response(content, media_type=media, headers={"Cache-Control": "public, max-age=86400"})
    try:
        content, media = fetch_image(item["image_url"], referer=item["url"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(404, "Não foi possível carregar a imagem da loja.") from exc
    with _image_lock:
        _image_cache[key] = (content, media)
        _image_cache.move_to_end(key)
        while len(_image_cache) > _IMAGE_CACHE_MAX:
            _image_cache.popitem(last=False)
    return Response(content, media_type=media, headers={"Cache-Control": "public, max-age=86400"})
