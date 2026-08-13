"""Tests that the HTTP client library's request logging stays quieted.

The HTTP client logs full outbound request URLs (including query strings) at
INFO. /resolve forwards street/city to the Photon geocoder as query params, so
at INFO level those logs would leak PII. app/main.py quiets that logger to
WARNING by name — and a logger name is a string, so if the client library is
ever swapped for one that logs under a different name, the guard silently stops
applying and the leak reappears with nothing failing. These tests pin the guard
to the library actually in use.
"""

import logging

import httpx2 as httpx


def test_http_client_logger_is_quieted_to_warning():
    import app.main  # noqa: F401  — import applies the logging configuration

    assert logging.getLogger(httpx.__name__).level >= logging.WARNING


def test_request_url_with_query_params_is_not_logged_at_info(caplog):
    """A geocode-shaped request must not emit its query string into the logs."""
    import app.main  # noqa: F401  — import applies the logging configuration

    def handler(request):
        return httpx.Response(200, json={"features": []})

    with caplog.at_level(logging.INFO):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            client.get("http://photon/api", params={"q": "Rue Secrete, Bruxelles"})

    # Assert on a token that survives URL encoding: the logged line renders the
    # query as "q=Rue+Secrete%2C+Bruxelles", so asserting on the unencoded
    # "Rue Secrete" would pass even when the address IS being logged.
    assert "Secrete" not in caplog.text
