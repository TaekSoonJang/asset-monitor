from asset_monitor.brokers.shinhan.collector import ShinhanCollector


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.default_timeout: int | None = None
        self.default_navigation_timeout: int | None = None

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.default_navigation_timeout = timeout


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages
        self.new_page_called = False

    def new_page(self) -> FakePage:
        self.new_page_called = True
        page = FakePage("about:blank")
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.contexts = [context]


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    def connect_over_cdp(self, cdp_url: str) -> FakeBrowser:
        return self.browser


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)


def test_open_session_reuses_existing_shinhan_page_without_opening_new_tab() -> None:
    shinhan_page = FakePage("https://shinhansec.com/siw/myasset/balance/540101/view.do")
    context = FakeContext([FakePage("https://example.com"), shinhan_page])
    collector = ShinhanCollector.__new__(ShinhanCollector)
    collector.account = type("Account", (), {"cdp_url": "http://127.0.0.1:9222"})()

    _, _, page, page_should_close = collector._open_session(FakePlaywright(FakeBrowser(context)))

    assert page is shinhan_page
    assert page_should_close is False
    assert context.new_page_called is False
    assert shinhan_page.default_timeout == 15000
    assert shinhan_page.default_navigation_timeout == 30000


def test_open_session_requires_existing_shinhan_page() -> None:
    context = FakeContext([FakePage("https://example.com")])
    collector = ShinhanCollector.__new__(ShinhanCollector)
    collector.account = type("Account", (), {"cdp_url": "http://127.0.0.1:9222"})()

    try:
        collector._open_session(FakePlaywright(FakeBrowser(context)))
    except RuntimeError as exc:
        assert "existing Shinhan tab" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    assert context.new_page_called is False
