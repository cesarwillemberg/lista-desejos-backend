"""HTTP API for the Wishly wishlist."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from app import db
from app.auth import current_user, hash_password, issue_token, jwt_secret, verify_password
from app.extractor import extract_product, store_name
from app.fetch import ensure_public_url, fetch_image
from app.share_codes import new_short_code

_image_cache: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()
_image_lock = Lock()
_IMAGE_CACHE_MAX = 24


class AuthBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str
    password: str


class ListBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str


class ReserveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None


class CategoryBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str


class CategoryOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: list[str]


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    url: str
    category_id: str | None = None


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    image_url: str | None = None
    price: float | None = None
    currency: str | None = None
    category_id: str | None = None
    purchased: bool | None = None


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str
    role: str


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str


def _web_dir() -> Path:
    configured = os.environ.get("QUERO_WEB_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "build" / "web"


def _split_totals(items: list[dict]) -> tuple[list[dict], list[dict], int]:
    remaining = [item for item in items if not item.get("purchased")]
    bought = [item for item in items if item.get("purchased")]
    unpriced = sum(1 for item in remaining if item["price"] is None)
    return db.money_totals(remaining), db.money_totals(bought), unpriced


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 200:
        raise HTTPException(400, "Informe um e-mail válido.")
    return email


def _clean_password(value: str) -> str:
    if value is None or len(value) < 8:
        raise HTTPException(400, "A senha precisa ter pelo menos 8 caracteres.")
    if len(value) > 200:
        raise HTTPException(400, "A senha é longa demais.")
    return value


def _short_base(request: Request) -> str:
    configured = (os.environ.get("PUBLIC_SHORT_BASE") or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _web_base() -> str:
    return (os.environ.get("PUBLIC_WEB_BASE") or "https://cesarwillemberg.github.io/lista-desejos-frontend").rstrip("/")


def _owner_label(email: str | None) -> str:
    if not email or "@" not in email:
        return "alguém"
    name = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return name[:1].upper() + name[1:] if name else "alguém"


def _present_list(payload: dict, request: Request | None = None) -> dict:
    data = dict(payload)
    data.pop("items", None)
    data.pop("user_id", None)
    role = data.get("role") or "owner"
    data["role"] = role
    members = data.get("members") or []
    data["members"] = members
    data["collaborative"] = len(members) > 1
    editor_code = data.pop("invite_editor_code", None)
    viewer_code = data.pop("invite_viewer_code", None)
    data.pop("invite_role", None)
    code = data.get("short_code")
    token = data.get("share_token")
    public_id = code or token
    data["share_path"] = f"/l/{public_id}" if public_id else None
    data["short_url"] = f"{_short_base(request)}/s/{code}" if request is not None and code else None
    if role == "owner" and request is not None:
        base = _short_base(request)
        data["invite_editor_url"] = f"{base}/c/{editor_code}" if editor_code else None
        data["invite_viewer_url"] = f"{base}/c/{viewer_code}" if viewer_code else None
    else:
        data["share_token"] = None
        data["short_code"] = None
        data["short_url"] = None
        data["share_path"] = None
        data["invite_editor_url"] = None
        data["invite_viewer_url"] = None
    return data


def _fresh_invite_code() -> str:
    for _ in range(12):
        code = new_short_code()
        if not db.invite_code_taken(code) and not db.short_code_taken(code):
            return code
    raise HTTPException(500, "Não foi possível gerar um convite.")


def _fresh_short_code(list_id: str) -> str:
    for _ in range(12):
        code = new_short_code()
        if not db.short_code_taken(code, except_list_id=list_id):
            return code
    raise HTTPException(500, "Não foi possível gerar um código curto.")


def _present_item(
    item: dict,
    *,
    owner: bool,
    mine: bool,
    added_by_label: str | None = None,
    purchased_by_label: str | None = None,
) -> dict:
    hidden = {"reserved_token_hash", "added_by", "reserved_by", "purchased_by"}
    data = {key: value for key, value in item.items() if key not in hidden}
    if not owner and not mine:
        data["reserved_label"] = None
    data["reserved_by_me"] = mine
    data["added_by_label"] = added_by_label
    data["purchased_by_label"] = purchased_by_label
    return data


def _present_items(items: list[dict], *, member: bool, user_id: str | None, claims: dict[str, str] | None = None) -> list[dict]:
    ids = [item.get("added_by") for item in items if item.get("added_by")]
    ids.extend(item.get("purchased_by") for item in items if item.get("purchased_by"))
    labels = db.labels_for_users(ids)
    presented = []
    for item in items:
        mine = bool(user_id and item.get("reserved_by") == user_id)
        if not mine and claims is not None:
            mine = _claim_matches(item, claims.get(item["id"]))
        presented.append(
            _present_item(
                item,
                owner=member,
                mine=mine,
                added_by_label=labels.get(item.get("added_by")),
                purchased_by_label=labels.get(item.get("purchased_by")) if item.get("purchased") else None,
            )
        )
    return presented


def _claim_matches(item: dict, token: str | None) -> bool:
    stored = item.get("reserved_token_hash")
    if not token or not stored:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(digest, stored)


def _claims(header: str | None) -> dict[str, str]:
    found: dict[str, str] = {}
    if not header:
        return found
    for part in header.split(","):
        if ":" not in part:
            continue
        item_id, token = part.split(":", 1)
        item_id = item_id.strip()
        token = token.strip()
        if item_id and token:
            found[item_id] = token
    return found


def _auth_payload(user: dict) -> dict:
    return {"token": issue_token(user["id"], user["email"]), "user": user}


_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def _clean_invite_role(value: str) -> str:
    role = (value or "").strip().lower()
    if role not in {"editor", "viewer"}:
        raise HTTPException(400, "Escolha Pode editar ou Só pode ver.")
    return role


def _access(list_id: str, user: dict, minimum: str = "viewer") -> dict:
    found = db.get_list_for_member(list_id, user["id"])
    if found is None:
        raise HTTPException(404, "Lista não encontrada.")
    if _ROLE_RANK[found["role"]] < _ROLE_RANK[minimum]:
        raise HTTPException(403, "Você só pode ver esta lista.")
    return found


def _access_item(item_id: str, user: dict, minimum: str = "editor") -> dict:
    item = db.get_item(item_id)
    if item is None:
        raise HTTPException(404, "Item não encontrado.")
    _access(item["list_id"], user, minimum)
    return item


def _require_category(category_id: str, user: dict, minimum: str = "editor") -> dict:
    category = db.get_category(category_id)
    if category is None:
        raise HTTPException(404, "Categoria não encontrada.")
    _access(category["list_id"], user, minimum)
    return category


def _category_on_list(list_id: str, category_id: str | None) -> str | None:
    if category_id is None or not str(category_id).strip():
        return None
    category = db.get_category(category_id)
    if category is None or category["list_id"] != list_id:
        raise HTTPException(400, "Escolha uma categoria desta lista.")
    return category["id"]


def _require_item(item_id: str, user: dict) -> dict:
    return _access_item(item_id, user, "editor")


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
    jwt_secret()
    db.migrate()
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


app = FastAPI(title="Wishly", lifespan=lifespan)
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


@app.get("/.well-known/assetlinks.json")
def asset_links():
    package = (os.environ.get("ANDROID_PACKAGE") or "com.wishly.app").strip()
    raw = os.environ.get("ANDROID_SHA256_FINGERPRINTS") or ""
    fingerprints = [item.strip() for item in raw.split(",") if item.strip()]
    if not fingerprints:
        fingerprints = ["REPLACE_WITH_SHA256_FINGERPRINT"]
    return JSONResponse(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": package,
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        ]
    )


@app.get("/.well-known/apple-app-site-association")
def apple_app_site_association():
    app_id = (os.environ.get("IOS_APP_ID") or "TEAMID.com.wishly.app").strip()
    return JSONResponse(
        {
            "applinks": {
                "apps": [],
                "details": [{"appID": app_id, "paths": ["/s/*", "/c/*"]}],
            }
        }
    )


@app.get("/s/{code}")
def short_link(code: str, request: Request):
    found = db.get_list_by_share_token(code)
    if found is None:
        page = "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Link indisponível</title></head><body><p>Esse link não está mais disponível.</p></body></html>"
        return Response(page, status_code=404, media_type="text/html; charset=utf-8")
    public_id = found.get("short_code") or code
    target = f"{_web_base()}/#/l/{public_id}"
    owner = _owner_label((db.get_user(found["user_id"]) or {}).get("email"))
    title = found["name"]
    description = f"Lista de {owner} no Wishly"
    image = ""
    for item in found.get("items") or []:
        if item.get("image_url"):
            image = f"{_short_base(request)}/api/share/{public_id}/items/{item['id']}/image"
            break
    image_tag = f'<meta property="og:image" content="{html.escape(image, quote=True)}">' if image else ""
    page = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{html.escape(title)} — Wishly</title>
<meta property="og:site_name" content="Wishly">
<meta property="og:type" content="website">
<meta property="og:title" content="{html.escape(title, quote=True)}">
<meta property="og:description" content="{html.escape(description, quote=True)}">
<meta property="og:url" content="{html.escape(_short_base(request) + '/s/' + public_id, quote=True)}">
{image_tag}
<meta http-equiv="refresh" content="0;url={html.escape(target, quote=True)}">
<script>location.replace({json.dumps(target)});</script>
</head>
<body>
<p>Abrindo a lista… <a href="{html.escape(target, quote=True)}">Continuar</a></p>
</body>
</html>"""
    return Response(page, media_type="text/html; charset=utf-8")


@app.get("/c/{code}")
def invite_link(code: str, request: Request):
    found = db.get_invite(code)
    if found is None:
        page = "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Convite indisponível</title></head><body><p>Esse convite não está mais disponível.</p></body></html>"
        return Response(page, status_code=404, media_type="text/html; charset=utf-8")
    role = found["invite_role"]
    role_label = "pode editar" if role == "editor" else "só pode ver"
    target = f"{_web_base()}/#/c/{code}"
    owner = _owner_label((db.get_user(found["user_id"]) or {}).get("email"))
    title = found["name"]
    description = f"{owner} convidou você para esta lista. Você {role_label}."
    image = ""
    for item in found.get("items") or []:
        if item.get("image_url"):
            public_id = found.get("short_code") or found.get("share_token")
            if public_id:
                image = f"{_short_base(request)}/api/share/{public_id}/items/{item['id']}/image"
            break
    image_tag = f'<meta property="og:image" content="{html.escape(image, quote=True)}">' if image else ""
    page = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{html.escape(title)} — Wishly</title>
<meta property="og:site_name" content="Wishly">
<meta property="og:type" content="website">
<meta property="og:title" content="{html.escape(title, quote=True)}">
<meta property="og:description" content="{html.escape(description, quote=True)}">
<meta property="og:url" content="{html.escape(_short_base(request) + '/c/' + code, quote=True)}">
{image_tag}
<meta http-equiv="refresh" content="0;url={html.escape(target, quote=True)}">
<script>location.replace({json.dumps(target)});</script>
</head>
<body>
<p>Abrindo o convite… <a href="{html.escape(target, quote=True)}">Continuar</a></p>
</body>
</html>"""
    return Response(page, media_type="text/html; charset=utf-8")


@app.post("/api/auth/register", status_code=201)
def register(body: AuthBody):
    email = _clean_email(body.email)
    password = _clean_password(body.password)
    user = db.register_user(email, hash_password(password))
    return _auth_payload(user)


@app.post("/api/auth/login")
def login(body: AuthBody):
    email = _clean_email(body.email)
    password = _clean_password(body.password)
    found = db.get_user_by_email(email)
    if found is None or not verify_password(password, found.pop("password_hash")):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    return _auth_payload(found)


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/api/lists")
def get_lists(request: Request, user: dict = Depends(current_user)):
    return {"lists": [_present_list(item, request) for item in db.lists_for_user(user["id"])]}


@app.post("/api/lists", status_code=201)
def post_list(body: ListBody, request: Request, user: dict = Depends(current_user)):
    return _present_list(db.create_list(user["id"], _clean_name(body.name)), request)


@app.patch("/api/lists/{list_id}")
def patch_list(list_id: str, body: ListBody, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    updated = db.rename_list(list_id, user["id"], _clean_name(body.name))
    if updated is None:
        raise HTTPException(404, "Lista não encontrada.")
    return _present_list(updated, request)


@app.delete("/api/lists/{list_id}", status_code=204)
def remove_list(list_id: str, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    if not db.delete_list(list_id, user["id"]):
        raise HTTPException(404, "Lista não encontrada.")
    return Response(status_code=204)


def _new_share_token() -> str:
    return secrets.token_urlsafe(18)


def _save_share(list_id: str, user_id: str, token: str | None, code: str | None):
    try:
        updated = db.set_share(list_id, user_id, token, code)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Lista não encontrada.")
    return updated


@app.post("/api/lists/{list_id}/share")
def create_share(list_id: str, request: Request, user: dict = Depends(current_user)):
    current = _access(list_id, user, "owner")
    token = current["share_token"] or _new_share_token()
    code = current["short_code"] or _fresh_short_code(list_id)
    if token == current["share_token"] and code == current["short_code"]:
        return _present_list(current, request)
    updated = _save_share(list_id, user["id"], token, code)
    return _present_list(updated, request)


@app.post("/api/lists/{list_id}/share/regenerate")
def regenerate_share(list_id: str, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    updated = _save_share(list_id, user["id"], _new_share_token(), _fresh_short_code(list_id))
    return _present_list(updated, request)


@app.delete("/api/lists/{list_id}/share", status_code=200)
def revoke_share(list_id: str, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    updated = _save_share(list_id, user["id"], None, None)
    return _present_list(updated, request)


def _save_invite(list_id: str, user_id: str, role: str, code: str | None, request: Request):
    try:
        saved = db.set_invite_code(list_id, role, code)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not saved:
        raise HTTPException(404, "Lista não encontrada.")
    found = db.get_list_for_member(list_id, user_id)
    if found is None:
        raise HTTPException(404, "Lista não encontrada.")
    return _present_list(found, request)


@app.post("/api/lists/{list_id}/invites/{role}")
def create_invite(list_id: str, role: str, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    role = _clean_invite_role(role)
    current = db.get_list_for_member(list_id, user["id"])
    assert current is not None
    existing = current["invite_editor_code"] if role == "editor" else current["invite_viewer_code"]
    if existing:
        return _present_list(current, request)
    return _save_invite(list_id, user["id"], role, _fresh_invite_code(), request)


@app.post("/api/lists/{list_id}/invites/{role}/regenerate")
def regenerate_invite(list_id: str, role: str, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    return _save_invite(list_id, user["id"], _clean_invite_role(role), _fresh_invite_code(), request)


@app.delete("/api/lists/{list_id}/invites/{role}")
def disable_invite(list_id: str, role: str, request: Request, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    return _save_invite(list_id, user["id"], _clean_invite_role(role), None, request)


@app.get("/api/invites/{code}")
def preview_invite(code: str, user: dict = Depends(current_user)):
    found = db.get_invite(code)
    if found is None:
        raise HTTPException(404, "Esse convite não está mais disponível.")
    return {"list_id": found["id"], "name": found["name"], "role": found["invite_role"]}


@app.post("/api/invites/{code}/accept")
def accept_invite(code: str, request: Request, user: dict = Depends(current_user)):
    found = db.get_invite(code)
    if found is None:
        raise HTTPException(404, "Esse convite não está mais disponível.")
    if found["user_id"] != user["id"]:
        db.add_member(found["id"], user["id"], found["invite_role"])
    updated = db.get_list_for_member(found["id"], user["id"])
    if updated is None:
        raise HTTPException(404, "Lista não encontrada.")
    return _present_list(updated, request)


@app.get("/api/lists/{list_id}/members")
def get_members(list_id: str, user: dict = Depends(current_user)):
    _access(list_id, user, "viewer")
    return {"members": db.list_members(list_id)}


@app.post("/api/lists/{list_id}/members")
def invite_member(list_id: str, body: MemberBody, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    role = _clean_invite_role(body.role)
    email = _clean_email(body.email)
    account = db.get_user_by_email(email)
    if account is None:
        raise HTTPException(404, "Não há conta com esse e-mail. Envie o link de convite.")
    if account["id"] == user["id"]:
        raise HTTPException(400, "Você já é o dono desta lista.")
    db.add_member(list_id, account["id"], role)
    return {"members": db.list_members(list_id)}


@app.patch("/api/lists/{list_id}/members/{member_id}")
def change_member_role(list_id: str, member_id: str, body: RoleBody, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    role = _clean_invite_role(body.role)
    if not db.set_member_role(list_id, member_id, role):
        raise HTTPException(404, "Esse membro não pode mudar de papel.")
    return {"members": db.list_members(list_id)}


@app.delete("/api/lists/{list_id}/members/{member_id}")
def remove_member(list_id: str, member_id: str, user: dict = Depends(current_user)):
    _access(list_id, user, "owner")
    if member_id == user["id"]:
        raise HTTPException(400, "O dono não sai da lista por aqui. Exclua a lista se quiser encerrá-la.")
    if not db.remove_member(list_id, member_id):
        raise HTTPException(404, "Membro não encontrado.")
    return {"members": db.list_members(list_id)}


@app.post("/api/lists/{list_id}/leave", status_code=204)
def leave_list(list_id: str, user: dict = Depends(current_user)):
    found = _access(list_id, user, "viewer")
    if found["role"] == "owner":
        raise HTTPException(400, "O dono não pode sair da lista. Exclua a lista se quiser encerrá-la.")
    if not db.remove_member(list_id, user["id"]):
        raise HTTPException(404, "Lista não encontrada.")
    return Response(status_code=204)


@app.get("/api/lists/{list_id}/items")
def get_items(list_id: str, request: Request, user: dict = Depends(current_user)):
    found = _access(list_id, user, "viewer")
    items = _present_items(db.list_items(list_id), member=True, user_id=user["id"])
    totals, purchased_totals, unpriced = _split_totals(items)
    return {
        "list": _present_list(found, request),
        "categories": db.list_categories(list_id),
        "items": items,
        "totals": totals,
        "purchased_totals": purchased_totals,
        "unpriced_count": unpriced,
    }


@app.post("/api/lists/{list_id}/categories", status_code=201)
def post_category(list_id: str, body: CategoryBody, user: dict = Depends(current_user)):
    _access(list_id, user, "editor")
    return db.create_category(list_id, _clean_name(body.name))


@app.patch("/api/categories/{category_id}")
def patch_category(category_id: str, body: CategoryBody, user: dict = Depends(current_user)):
    _require_category(category_id, user)
    updated = db.rename_category(category_id, _clean_name(body.name))
    if updated is None:
        raise HTTPException(404, "Categoria não encontrada.")
    return updated


@app.delete("/api/categories/{category_id}", status_code=204)
def remove_category(category_id: str, user: dict = Depends(current_user)):
    _require_category(category_id, user)
    if not db.delete_category(category_id):
        raise HTTPException(404, "Categoria não encontrada.")
    return Response(status_code=204)


@app.post("/api/lists/{list_id}/categories/reorder")
def reorder_categories(list_id: str, body: CategoryOrder, user: dict = Depends(current_user)):
    _access(list_id, user, "editor")
    try:
        return {"categories": db.reorder_categories(list_id, body.ids)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/lists/{list_id}/items", status_code=201)
def create_item(list_id: str, body: ItemCreate, user: dict = Depends(current_user)):
    _access(list_id, user, "editor")
    name = _clean_name(body.name)
    url = (body.url or "").strip()
    if len(url) > 2000:
        raise HTTPException(400, "Esse link é longo demais.")
    ensure_public_url(url)
    extracted = extract_product(url)
    item = db.insert_item(
        list_id,
        {
            "name": name,
            "url": url,
            "category_id": _category_on_list(list_id, body.category_id),
            "added_by": user["id"],
            **_fields_from_extraction(extracted, url),
        },
    )
    return _present_items([item], member=True, user_id=user["id"])[0]


@app.patch("/api/items/{item_id}")
def patch_item(item_id: str, body: ItemUpdate, user: dict = Depends(current_user)):
    current = _require_item(item_id, user)
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
    if "category_id" in sent:
        changes["category_id"] = _category_on_list(current["list_id"], body.category_id)
    if "purchased" in sent:
        if body.purchased:
            changes["purchased"] = 1
            changes["purchased_at"] = datetime.now(timezone.utc).isoformat()
            changes["purchased_by"] = user["id"]
        else:
            changes["purchased"] = 0
            changes["purchased_at"] = None
            changes["purchased_by"] = None
    if not changes:
        return _present_items([current], member=True, user_id=user["id"])[0]
    content_changed = any(key in changes for key in ("image_url", "price", "currency"))
    image = changes["image_url"] if "image_url" in changes else current["image_url"]
    price = changes["price"] if "price" in changes else current["price"]
    if content_changed and image and price is not None:
        changes["status"] = "ok"
        changes["message"] = None
    elif content_changed and (image or price is not None):
        changes["status"] = "partial"
        if price is None:
            changes["message"] = "Preço informado manualmente ainda está vazio."
        else:
            changes["message"] = "Foto informada manualmente ainda está vazia."
    elif content_changed:
        changes["status"] = "failed"
        changes["message"] = "Foto e preço não informados."
    updated = db.update_item(item_id, changes)
    if updated is None:
        raise HTTPException(404, "Item não encontrado.")
    return _present_items([updated], member=True, user_id=user["id"])[0]


@app.post("/api/items/{item_id}/refresh")
def refresh_item(item_id: str, user: dict = Depends(current_user)):
    current = _require_item(item_id, user)
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
    return _present_items([updated], member=True, user_id=user["id"])[0]


@app.post("/api/items/{item_id}/reservation")
def member_reserve(item_id: str, body: ReserveBody, user: dict = Depends(current_user)):
    item = _access_item(item_id, user, "viewer")
    if item["reserved"]:
        raise HTTPException(409, "Alguém já marcou este item.")
    label = " ".join((body.name or "").split())[:80] or db.person_label(user["email"])
    updated = db.update_item(
        item_id,
        {
            "reserved_label": label,
            "reserved_by": user["id"],
            "reserved_token_hash": None,
            "reserved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if updated is None:
        raise HTTPException(404, "Item não encontrado.")
    return _present_items([updated], member=True, user_id=user["id"])[0]


@app.delete("/api/items/{item_id}/reservation", status_code=204)
def owner_clear_reservation(item_id: str, user: dict = Depends(current_user)):
    item = _access_item(item_id, user, "viewer")
    membership = db.get_list_for_member(item["list_id"], user["id"])
    if membership and membership["role"] == "viewer" and item.get("reserved_by") != user["id"]:
        raise HTTPException(403, "Só quem reservou pode desmarcar.")
    db.update_item(
        item["id"],
        {"reserved_label": None, "reserved_token_hash": None, "reserved_at": None, "reserved_by": None},
    )
    return Response(status_code=204)


@app.delete("/api/items/{item_id}", status_code=204)
def remove_item(item_id: str, user: dict = Depends(current_user)):
    _access_item(item_id, user, "editor")
    if not db.delete_item(item_id):
        raise HTTPException(404, "Item não encontrado.")
    return Response(status_code=204)


@app.get("/api/share/{token}")
def share_view(token: str, x_claims: str | None = Header(default=None)):
    found = db.get_list_by_share_token(token)
    if found is None:
        raise HTTPException(404, "Esse link não está mais disponível.")
    claims = _claims(x_claims)
    items = _present_items(found.pop("items"), member=False, user_id=None, claims=claims)
    totals, purchased_totals, unpriced = _split_totals(items)
    return {
        "list": {"name": found["name"], "share_path": found.get("share_path") or f"/l/{token}"},
        "categories": db.list_categories(found["id"]),
        "items": items,
        "totals": totals,
        "purchased_totals": purchased_totals,
        "unpriced_count": unpriced,
    }


@app.post("/api/share/{token}/items/{item_id}/reservation")
def reserve_item(token: str, item_id: str, body: ReserveBody):
    found = db.get_list_by_share_token(token)
    if found is None:
        raise HTTPException(404, "Esse link não está mais disponível.")
    item = next((entry for entry in found["items"] if entry["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "Item não encontrado.")
    if item["reserved"]:
        raise HTTPException(409, "Alguém já marcou este item.")
    label = " ".join((body.name or "").split())[:80] or None
    claim = secrets.token_urlsafe(24)
    digest = hashlib.sha256(claim.encode()).hexdigest()
    db.update_item(
        item_id,
        {
            "reserved_label": label,
            "reserved_token_hash": digest,
            "reserved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"claim_token": claim, "reserved_label": label}


@app.delete("/api/share/{token}/items/{item_id}/reservation", status_code=204)
def clear_reservation(token: str, item_id: str, x_claim_token: str | None = Header(default=None)):
    found = db.get_list_by_share_token(token)
    if found is None:
        raise HTTPException(404, "Esse link não está mais disponível.")
    item = next((entry for entry in found["items"] if entry["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "Item não encontrado.")
    if not _claim_matches(item, x_claim_token):
        raise HTTPException(403, "Só quem reservou pode desmarcar.")
    db.update_item(item_id, {"reserved_label": None, "reserved_token_hash": None, "reserved_at": None})
    return Response(status_code=204)


@app.get("/api/items/{item_id}/image")
def item_image(item_id: str, user: dict = Depends(current_user)):
    item = _access_item(item_id, user, "viewer")
    return _image_response(item)


@app.get("/api/share/{token}/items/{item_id}/image")
def shared_item_image(token: str, item_id: str):
    found = db.get_list_by_share_token(token)
    if found is None:
        raise HTTPException(404, "Esse link não está mais disponível.")
    item = next((entry for entry in found["items"] if entry["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "Imagem indisponível.")
    return _image_response(item)


def _image_response(item: dict):
    if not item.get("image_url"):
        raise HTTPException(404, "Imagem indisponível.")
    item_id = item["id"]
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
