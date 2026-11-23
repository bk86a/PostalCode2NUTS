"""Docs pages load pinned CDN assets with Subresource Integrity."""

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import docs


def _assets(html: str) -> list[str]:
    return re.findall(r'<(?:script|link)[^>]+(?:src|href)="(https://cdn[^"]+)"[^>]*>', html)


def test_swagger_ui_assets_are_pinned_with_integrity(client):
    resp = client.get("/documentation")
    assert resp.status_code == 200
    html = resp.text
    for url, digest in (docs.SWAGGER_JS, docs.SWAGGER_CSS):
        assert f'"{url}" integrity="{digest}" crossorigin="anonymous"' in html
    assert all("@5/" not in u for u in _assets(html))  # no floating major tags


def test_redoc_assets_are_pinned_with_integrity_and_no_google_fonts(client):
    resp = client.get("/redoc")
    assert resp.status_code == 200
    url, digest = docs.REDOC_JS
    assert f'"{url}" integrity="{digest}" crossorigin="anonymous"' in resp.text
    assert "fonts.googleapis.com" not in resp.text


def test_docs_routes_not_in_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/documentation" not in paths and "/redoc" not in paths


def test_register_docs_on_custom_paths():
    app = FastAPI(docs_url=None, redoc_url=None)
    docs.register_docs(app, "/d", "/r")
    tc = TestClient(app)
    assert "swagger-ui-bundle.js" in tc.get("/d").text
    assert "redoc.standalone.js" in tc.get("/r").text
    assert tc.get("/docs").status_code == 404


def test_docs_schema_url_honours_root_path():
    app = FastAPI(docs_url=None, redoc_url=None)
    docs.register_docs(app, "/d", "/r")
    tc = TestClient(app, root_path="/pc2n")
    assert "url: '/pc2n/openapi.json'" in tc.get("/d").text
    assert 'spec-url="/pc2n/openapi.json"' in tc.get("/r").text
    plain = TestClient(app)
    assert "url: '/openapi.json'" in plain.get("/d").text
