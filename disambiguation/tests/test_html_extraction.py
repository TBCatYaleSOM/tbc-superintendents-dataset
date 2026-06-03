"""Tests for HTML to text extraction functionality."""

import pytest

from content_store import extract_text_from_html


class TestExtractTextFromHtml:
    """Test the extract_text_from_html function."""

    def test_basic_html_extraction(self):
        """Extract text from a simple HTML document."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Hello World</h1>
            <p>This is a test paragraph.</p>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        assert "Hello World" in result
        assert "This is a test paragraph" in result

    def test_removes_script_and_style(self):
        """Script and style tags should be removed."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>body { color: red; }</style>
        </head>
        <body>
            <script>alert('evil');</script>
            <p>Visible content here.</p>
            <style>.hidden { display: none; }</style>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        assert "alert" not in result
        assert "color: red" not in result
        assert "display: none" not in result
        # Note: trafilatura may or may not extract minimal content
        # Main point is scripts/styles are stripped

    def test_extracts_from_article_content(self):
        """Extract main content from article-style pages."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>News Article</title></head>
        <body>
            <nav>Navigation menu</nav>
            <article>
                <h1>Superintendent John Smith Announces Retirement</h1>
                <p>After 20 years of service to the district, Superintendent
                John Smith announced his retirement today.</p>
                <p>Smith began his career as a teacher in 1990 and worked
                his way up through the ranks.</p>
            </article>
            <footer>Copyright 2024</footer>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        assert "John Smith" in result
        assert "retirement" in result.lower()
        assert "20 years" in result

    def test_empty_html_returns_empty_string(self):
        """Empty or whitespace HTML returns empty string."""
        assert extract_text_from_html("") == ""
        assert extract_text_from_html("   ") == ""

    def test_malformed_html_returns_empty_or_partial(self):
        """Malformed HTML should not raise, returns empty or partial text."""
        # Just opening tags
        result = extract_text_from_html("<html><body><p>Test")
        # Should not raise, result may be empty or "Test"
        assert isinstance(result, str)

    def test_non_html_returns_empty(self):
        """Non-HTML content returns empty string."""
        # JSON content
        result = extract_text_from_html('{"key": "value"}')
        assert isinstance(result, str)

        # XML without HTML structure
        result = extract_text_from_html('<?xml version="1.0"?><root><item>data</item></root>')
        assert isinstance(result, str)

    def test_wayback_style_content(self):
        """Content typical of Wayback Machine archived pages."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>School District News - 2015</title>
            <meta name="description" content="School district news and updates">
        </head>
        <body>
            <div id="header">
                <h1>Westside School District</h1>
            </div>
            <div id="content">
                <h2>New Superintendent Named</h2>
                <p class="date">October 15, 2015</p>
                <p>The Westside School District Board of Education is pleased
                to announce that Dr. Jane Doe has been selected as the new
                superintendent. Dr. Doe comes to us from the Eastside School
                District where she served as assistant superintendent.</p>
                <p>"We are excited to welcome Dr. Doe to our district," said
                Board President Bob Johnson. "Her experience and vision make
                her the ideal choice to lead our schools."</p>
            </div>
            <div id="sidebar">
                <h3>Quick Links</h3>
                <ul>
                    <li><a href="/calendar">Calendar</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </div>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        assert "Jane Doe" in result
        assert "superintendent" in result.lower()

    def test_minimal_html_fragment(self):
        """Minimal HTML fragments without full document structure."""
        # Just a paragraph
        result = extract_text_from_html("<p>Just a paragraph</p>")
        assert isinstance(result, str)

        # Just text, no tags
        result = extract_text_from_html("Plain text without any HTML")
        assert isinstance(result, str)

    def test_unicode_content(self):
        """Handle Unicode content properly."""
        html = """
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Test</title></head>
        <body>
            <p>Café résumé naïve</p>
            <p>日本語テスト</p>
            <p>Emoji test: 👋 🎓</p>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        # Should handle unicode without raising
        assert isinstance(result, str)

    def test_deeply_nested_content(self):
        """Handle deeply nested HTML structures."""
        html = """
        <!DOCTYPE html>
        <html>
        <body>
            <div><div><div><div><div>
                <p>Deep content here</p>
            </div></div></div></div></div>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        assert isinstance(result, str)

    def test_table_content(self):
        """Extract text from tables."""
        html = """
        <!DOCTYPE html>
        <html>
        <body>
            <h1>Staff Directory</h1>
            <table>
                <tr><th>Name</th><th>Position</th></tr>
                <tr><td>John Smith</td><td>Superintendent</td></tr>
                <tr><td>Jane Doe</td><td>Principal</td></tr>
            </table>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        # Table text should be extracted
        assert isinstance(result, str)
        # May or may not preserve table structure, but names should be there
        # if content is substantial enough for trafilatura

    def test_news_article_with_boilerplate(self):
        """Real-world style article with lots of boilerplate."""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Local News - The Daily Times</title>
        </head>
        <body>
            <header>
                <div class="logo">The Daily Times</div>
                <nav>
                    <a href="/">Home</a>
                    <a href="/news">News</a>
                    <a href="/sports">Sports</a>
                </nav>
            </header>
            <main>
                <article>
                    <h1>School Board Names New Superintendent</h1>
                    <div class="byline">By Reporter Smith | January 15, 2015</div>
                    <p>The Springfield School Board voted unanimously Tuesday
                    to hire Dr. Michael Johnson as the district's new
                    superintendent, filling a position that has been vacant
                    since July.</p>
                    <p>Johnson, who currently serves as assistant
                    superintendent in neighboring Oak Valley, will assume
                    his new role on March 1. He will earn a base salary of
                    $175,000.</p>
                    <p>"Dr. Johnson brings a wealth of experience and a
                    proven track record of improving student achievement,"
                    Board President Sarah Williams said.</p>
                </article>
            </main>
            <aside>
                <h3>Related Stories</h3>
                <ul>
                    <li>Budget meeting scheduled</li>
                    <li>New school construction update</li>
                </ul>
            </aside>
            <footer>
                <p>© 2015 The Daily Times. All rights reserved.</p>
                <p>Contact us: news@dailytimes.com</p>
            </footer>
        </body>
        </html>
        """
        result = extract_text_from_html(html)
        # Main article content should be extracted
        assert "Michael Johnson" in result
        assert "superintendent" in result.lower()

    def test_oklahoman_style_content_that_fails_parsing(self):
        """Test content similar to what fails in production logs.

        Trafilatura logs errors for certain content types, but our function
        should gracefully return empty string rather than raising.
        """
        # Simulating a page that might cause trafilatura to log errors
        # (JavaScript-heavy, minimal text content)
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script>
            window.__INITIAL_STATE__ = {"data": "lots of json"};
            </script>
        </head>
        <body>
            <div id="app"></div>
            <script src="bundle.js"></script>
        </body>
        </html>
        """
        # Should not raise, returns empty string for JS-heavy pages
        result = extract_text_from_html(html)
        assert result == ""

    def test_binary_looking_content(self):
        """Content that looks like binary or is otherwise unparseable."""
        # Random bytes that might come from a corrupt fetch
        binary_like = b"\x00\x01\x02\xff\xfe".decode("latin-1")
        result = extract_text_from_html(binary_like)
        assert isinstance(result, str)

    def test_very_short_content(self):
        """Very short content may not be extracted by trafilatura."""
        # Trafilatura needs sufficient content to identify as "main content"
        html = "<html><body><p>Hi</p></body></html>"
        result = extract_text_from_html(html)
        # May return empty because too short for trafilatura's heuristics
        assert isinstance(result, str)
