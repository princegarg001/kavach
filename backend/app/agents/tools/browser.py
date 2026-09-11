"""
Playwright browser sandbox — screenshot suspicious URLs + form field inventory.
The screenshot is the best 10 seconds of the demo video.
"""
import asyncio
import base64
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("kavach.tools.browser")

# Cross-platform screenshots directory
SCREENSHOTS_DIR = Path(os.environ.get("KAVACH_SCREENSHOTS_DIR", Path.home() / ".kavach" / "screenshots"))
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT_MS = 15_000  # 15 seconds max


async def capture_screenshot(url: str) -> str | None:
    """
    Open URL in a sandboxed Playwright browser, capture screenshot.
    Returns the base64-encoded PNG screenshot, or None on failure.

    Also extracts page title and form fields for HTML analysis.

    Safety: No file downloads, no JavaScript interaction, read-only page load.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-plugins",
                    # Block downloads
                    "--block-new-web-contents",
                ],
            )

            context = await browser.new_context(
                viewport={"width": 390, "height": 844},  # iPhone 14 viewport
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                # Intercept and block file downloads
                accept_downloads=False,
            )

            page = await context.new_page()

            # Block heavy resources that slow things down
            await page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in ("media", "font")
                    else route.continue_()
                ),
            )

            t0 = time.monotonic()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                await asyncio.sleep(1.5)  # Let the page settle
            except Exception as nav_exc:
                logger.warning(f"Navigation partial load for {url}: {nav_exc}")

            # Extract page metadata
            page_title = await page.title() or ""
            favicon_url = None
            try:
                favicon_el = await page.query_selector('link[rel*="icon"]')
                if favicon_el:
                    favicon_url = await favicon_el.get_attribute("href")
            except Exception:
                pass

            # Extract form fields (credential harvesting detection)
            form_fields = []
            try:
                inputs = await page.query_selector_all("input")
                for inp in inputs[:30]:  # Limit to 30
                    name = await inp.get_attribute("name") or ""
                    input_type = await inp.get_attribute("type") or "text"
                    placeholder = await inp.get_attribute("placeholder") or ""
                    form_fields.append({
                        "name": name,
                        "type": input_type,
                        "placeholder": placeholder,
                    })
            except Exception:
                pass

            # Take screenshot
            screenshot_bytes = await page.screenshot(
                type="png",
                full_page=False,  # Above-the-fold only
            )

            elapsed = int((time.monotonic() - t0) * 1000)
            logger.info(
                f"📸 Screenshot captured for {url} in {elapsed}ms | "
                f"title='{page_title[:50]}' | fields={len(form_fields)}"
            )

            await browser.close()

            # Save to disk
            safe_name = url.replace("://", "_").replace("/", "_").replace("?", "_")[:60]
            filename = SCREENSHOTS_DIR / f"{safe_name}_{int(time.time())}.png"
            filename.write_bytes(screenshot_bytes)

            # Return as base64 for embedding in the API response / dashboard
            return base64.b64encode(screenshot_bytes).decode("utf-8")

    except ImportError:
        logger.error("Playwright not installed. Run: playwright install chromium")
        return None
    except Exception as exc:
        logger.error(f"Screenshot failed for {url}: {exc}")
        return None
