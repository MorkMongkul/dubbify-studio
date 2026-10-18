"""
The editor page that the desktop window loads from this backend. The window
(WebKit) keeps pages it has loaded; when they weren't marked to be checked
again, an updated app went on showing the previous version's page — and its
code — until the saved copy expired (0.2.3: old Voices tab, new voice data).
"""
import pytest

import app.main as main

needs_built_frontend = pytest.mark.skipif(main.FRONTEND_DIST is None, reason="frontend/dist not built")


@needs_built_frontend
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/?v=0.2.4", "/index.html", "/projects/p1/jobs/j1"])
async def test_the_editor_page_is_checked_again_on_every_load(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200 and "<html" in resp.text.lower()
    assert resp.headers.get("cache-control") == "no-cache"
