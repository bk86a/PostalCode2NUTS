"""Swagger UI and ReDoc pages with pinned, integrity-checked CDN assets.

FastAPI's built-in docs pages load floating ``swagger-ui-dist@5`` / ``redoc@2``
bundles from a CDN with no Subresource Integrity, so a compromised or hijacked
CDN path would run arbitrary script on the docs origin. These routes pin exact
versions and add ``integrity`` + ``crossorigin`` so the browser refuses any
asset whose bytes differ.

To bump a version, update the URL and recompute its hash:
    curl -sL <url> | openssl dgst -sha384 -binary | base64 -w0
"""

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

SWAGGER_JS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.34.1/swagger-ui-bundle.js",
    "sha384-XFYN47VzPiDY6qPtxYKg0nqUeW/upo20xf1j2SUE7gT/i35FJB4cuS/71Iegzk8Z",
)
SWAGGER_CSS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.34.1/swagger-ui.css",
    "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF",
)
REDOC_JS = (
    "https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js",
    "sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5",
)


def _with_integrity(response: HTMLResponse, assets: tuple[tuple[str, str], ...]) -> HTMLResponse:
    html = response.body.decode()
    for url, digest in assets:
        attr = f'"{url}"'
        if html.count(attr) != 1:  # FastAPI changed its template: fail loudly, not silently
            raise RuntimeError(f"docs asset {url} not found exactly once in generated HTML")
        html = html.replace(attr, f'{attr} integrity="{digest}" crossorigin="anonymous"')
    return HTMLResponse(html)


def register_docs(app: FastAPI, docs_url: str, redoc_url: str) -> None:
    """Serve Swagger UI at *docs_url* and ReDoc at *redoc_url* (app built with both None)."""
    title = app.title

    def _openapi_url(request: Request) -> str:
        # Same as FastAPI's built-in handlers: honour root_path so the schema
        # loads when the app sits behind a prefix-stripping proxy or mount.
        return request.scope.get("root_path", "").rstrip("/") + app.openapi_url

    @app.get(docs_url, include_in_schema=False)
    def swagger_ui(request: Request) -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=_openapi_url(request),
            title=f"{title} - Swagger UI",
            swagger_js_url=SWAGGER_JS[0],
            swagger_css_url=SWAGGER_CSS[0],
        )
        return _with_integrity(page, (SWAGGER_JS, SWAGGER_CSS))

    @app.get(redoc_url, include_in_schema=False)
    def redoc(request: Request) -> HTMLResponse:
        # Google Fonts CSS is generated per user agent, so it cannot carry an
        # integrity hash; ReDoc falls back to system fonts without it.
        page = get_redoc_html(
            openapi_url=_openapi_url(request),
            title=f"{title} - ReDoc",
            redoc_js_url=REDOC_JS[0],
            with_google_fonts=False,
        )
        return _with_integrity(page, (REDOC_JS,))
