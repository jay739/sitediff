"""Browser capture, the last resort.

Only needed by sites that expose their values neither in the served HTML, nor
as embedded state, nor through a data endpoint. Playwright is an optional extra
precisely because most sites are not in that category, and because a tool that
downloads a browser by default is a harder thing to get approved.

Nothing here is imported unless a profile actually asks for browser capture.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import CaptureError, UnsupportedCaptureError

DEFAULT_WAIT_UNTIL = "networkidle"
DEFAULT_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class BrowserOptions:
    wait_until: str = DEFAULT_WAIT_UNTIL
    wait_for_selector: str = ""
    wait_ms: int = 0
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    headers: dict[str, str] | None = None


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def render(url: str, options: BrowserOptions | None = None) -> str:
    """Load a URL, wait for it to settle, and return the resulting DOM.

    The wait condition is the whole game. Capture too early and every value
    reads as missing, which looks identical to catastrophic data loss. Prefer
    wait_for_selector over a fixed delay: a selector that only exists once the
    data has bound is a real signal, whereas a delay is a guess that fails on a
    slow day. Verify whichever you choose with 'compare --baseline' before
    trusting a large run.
    """
    if not available():
        raise UnsupportedCaptureError("browser")

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    opts = options or BrowserOptions()

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            context = browser.new_context(extra_http_headers=opts.headers or {})
            page = context.new_page()
            try:
                page.goto(url, wait_until=opts.wait_until, timeout=opts.timeout_ms)
                if opts.wait_for_selector:
                    page.wait_for_selector(opts.wait_for_selector, timeout=opts.timeout_ms)
                if opts.wait_ms:
                    page.wait_for_timeout(opts.wait_ms)
                return page.content()
            except PlaywrightTimeout as exc:
                waited = opts.wait_for_selector or opts.wait_until
                raise CaptureError(
                    f"Timed out rendering {url} after {opts.timeout_ms}ms "
                    f"waiting for {waited!r}.\n"
                    f"If the page renders but the condition never fires, set "
                    f"wait_for_selector to something that only appears once the "
                    f"data has bound. Capturing early reports every value as "
                    f"missing, which is indistinguishable from real data loss."
                ) from exc
            except PlaywrightError as exc:
                raise CaptureError(f"Browser failed to load {url}: {exc}") from exc
            finally:
                context.close()
        finally:
            browser.close()
