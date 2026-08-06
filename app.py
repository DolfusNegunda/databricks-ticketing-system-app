"""Entry point for both Databricks Apps and local development.

Databricks Apps runs ``python app.py`` (see app.yaml) and expects the process to
listen on ``DATABRICKS_APP_PORT``. Waitress is used as the WSGI server because
it is production-grade and runs unchanged on Windows and Linux, so local dev and
the deployed app take the same code path.
"""

from __future__ import annotations

import logging

from support_app import config, create_app

app = create_app()
log = logging.getLogger("nexus-support")


def main() -> None:
    log.info(
        "Starting %s on port %s (debug=%s)", config.BRAND_NAME, config.PORT, config.DEBUG
    )
    if config.DEBUG:
        app.run(host="0.0.0.0", port=config.PORT, debug=True)
        return

    from waitress import serve

    serve(
        app,
        host="0.0.0.0",
        port=config.PORT,
        threads=8,
        ident=config.PGAPPNAME,
        channel_timeout=120,
    )


if __name__ == "__main__":
    main()
