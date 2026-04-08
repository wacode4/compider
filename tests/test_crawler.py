from app.crawler import extract_links, extract_metadata, normalize_url


def test_extract_links():
    html = '''<html><body>
        <a href="/about">About</a>
        <a href="https://example.com/contact">Contact</a>
        <a href="https://external.com/foo">External</a>
        <a href="mailto:test@test.com">Email</a>
        <a href="#section">Anchor</a>
    </body></html>'''
    links = extract_links(html, "https://example.com")
    assert "https://example.com/about" in links
    assert "https://example.com/contact" in links
    assert "https://external.com/foo" not in links
    assert len([l for l in links if "mailto" in l]) == 0
    assert len([l for l in links if "#" in l]) == 0


def test_extract_metadata():
    html = '''<html><head>
        <title>My Page</title>
        <meta name="description" content="Page description here">
    </head><body></body></html>'''
    meta = extract_metadata(html)
    assert meta["title"] == "My Page"
    assert meta["description"] == "Page description here"


def test_extract_metadata_missing():
    html = "<html><body>No head</body></html>"
    meta = extract_metadata(html)
    assert meta["title"] == ""
    assert meta["description"] == ""


def test_normalize_url():
    assert normalize_url("https://example.com/path#frag") == "https://example.com/path"
    assert normalize_url("https://example.com/path?a=1") == "https://example.com/path?a=1"
    assert normalize_url("https://example.com/path/") == "https://example.com/path"
