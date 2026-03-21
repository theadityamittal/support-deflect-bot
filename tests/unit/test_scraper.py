"""Tests for web scraper."""

from unittest.mock import MagicMock, patch

import pytest

from rag.scraper import ScrapedPage, scrape_page, scrape_site

SAMPLE_HTML = """
<html>
<head><title>About Us</title></head>
<body>
  <nav>Navigation here</nav>
  <main>
    <h1>About Our Organization</h1>
    <p>We help communities through donations.</p>
    <img src="logo.png" alt="Organization logo showing community hands">
    <p>Founded in 2005, we have served 100,000 people.</p>
  </main>
  <footer>Footer content</footer>
</body>
</html>
"""


class TestScrapePage:
    @patch("rag.scraper.httpx")
    def test_extracts_text_content(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com/about"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com/about")
        assert "We help communities through donations" in page.text
        assert "Founded in 2005" in page.text

    @patch("rag.scraper.httpx")
    def test_extracts_alt_text_from_images(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com/about"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com/about")
        assert "Organization logo showing community hands" in page.text

    @patch("rag.scraper.httpx")
    def test_strips_nav_and_footer(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com/about"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com/about")
        assert "Navigation here" not in page.text
        assert "Footer content" not in page.text

    @patch("rag.scraper.httpx")
    def test_returns_title(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com/about"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com/about")
        assert page.title == "About Us"

    @patch("rag.scraper.httpx")
    def test_returns_url(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com/about"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com/about")
        assert page.url == "https://example.com/about"

    @patch("rag.scraper.httpx")
    def test_returns_raw_html(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_HTML
        mock_response.url = "https://example.com"
        mock_httpx.get.return_value = mock_response

        page = scrape_page("https://example.com")
        assert page.raw_html == SAMPLE_HTML

    @patch("rag.scraper.httpx")
    def test_http_error_raises(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = Exception("Not Found")
        mock_httpx.get.return_value = mock_response

        with pytest.raises(Exception, match="Not Found"):
            scrape_page("https://example.com/404")

    def test_scraped_page_is_frozen(self):
        page = ScrapedPage(
            url="https://example.com",
            title="Test",
            text="content",
            raw_html="<html>",
        )
        with pytest.raises(AttributeError):
            page.text = "modified"


class TestScrapeSite:
    """Tests for multi-page site crawling."""

    @patch("rag.scraper.httpx")
    def test_crawls_same_domain_links(self, mock_httpx):
        main_html = """
        <html><body>
            <p>Main page</p>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
        </body></html>
        """
        about_html = "<html><body><p>About us</p></body></html>"
        contact_html = "<html><body><p>Contact info</p></body></html>"

        responses = {
            "https://example.com": MagicMock(
                status_code=200, text=main_html, url="https://example.com"
            ),
            "https://example.com/about": MagicMock(
                status_code=200, text=about_html, url="https://example.com/about"
            ),
            "https://example.com/contact": MagicMock(
                status_code=200, text=contact_html, url="https://example.com/contact"
            ),
        }
        for r in responses.values():
            r.raise_for_status = MagicMock()

        mock_httpx.get.side_effect = lambda url, **kw: responses[url]

        pages = scrape_site("https://example.com", max_pages=10)
        assert len(pages) == 3

    @patch("rag.scraper.httpx")
    def test_respects_max_pages(self, mock_httpx):
        html = '<html><body><p>Page</p><a href="/next">Next</a></body></html>'
        mock_response = MagicMock(status_code=200, text=html, url="https://example.com")
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        pages = scrape_site("https://example.com", max_pages=1)
        assert len(pages) == 1

    @patch("rag.scraper.httpx")
    def test_skips_cross_domain_links(self, mock_httpx):
        html = """
        <html><body>
            <p>Page</p>
            <a href="https://other.com/page">External</a>
        </body></html>
        """
        mock_response = MagicMock(status_code=200, text=html, url="https://example.com")
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        pages = scrape_site("https://example.com", max_pages=10)
        assert len(pages) == 1  # Only the start page

    @patch("rag.scraper.httpx")
    def test_handles_page_failure_gracefully(self, mock_httpx):
        main_html = '<html><body><p>Main</p><a href="/broken">Broken</a></body></html>'
        mock_httpx.get.side_effect = [
            MagicMock(
                status_code=200,
                text=main_html,
                url="https://example.com",
                raise_for_status=MagicMock(),
            ),
            Exception("Connection refused"),
        ]

        pages = scrape_site("https://example.com", max_pages=10)
        assert len(pages) == 1  # Only main page, broken one skipped

    @patch("rag.scraper.httpx")
    def test_deduplicates_urls(self, mock_httpx):
        html = """
        <html><body>
            <p>Page</p>
            <a href="/">Home</a>
            <a href="/">Home again</a>
        </body></html>
        """
        mock_response = MagicMock(status_code=200, text=html, url="https://example.com")
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        pages = scrape_site("https://example.com", max_pages=10)
        assert len(pages) == 1  # Same URL not visited twice
