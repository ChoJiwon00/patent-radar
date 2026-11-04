import re

from patent_radar import website


def test_site_page_puts_title_and_link_preview_in_head():
    page = website.page_html()
    head, body = page.split("</head>", 1)
    assert "<title>LFP · 이차전지 특허 레이더 | Patent Radar</title>" in head
    assert '<meta property="og:image" content="https://chojiwon00.github.io/patent-radar/og.png">' in head
    assert 'name="description"' in head
    assert "<title>" not in body  # artifact 본문의 제목은 머리말로 옮겼다
    assert body.rstrip().endswith("</html>")


def test_three_pages_share_one_menu_and_footer():
    """사이트 첫 화면·청구항 숫자 지도·가이드는 같은 메뉴·바닥글을 쓴다. 다른 점은 지금 페이지 표시뿐."""
    pages = {"index": website.PAGE, "claims": website.DOCS / "claims.html", "guide": website.DOCS / "guide.html"}
    parts = {}
    for name, path in pages.items():
        html = path.read_text(encoding="utf-8")
        nav = re.search(r'<nav class="sitenav".*?</nav>', html, re.S).group(0)
        current = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', nav)
        assert len(current) == 1, name
        parts[name] = (
            nav.replace(' aria-current="page"', ""),
            re.search(r'<footer class="sitefoot">.*?</footer>', html, re.S).group(0),
            re.search(r"/\* 공통 메뉴·바닥글:.*?공통 메뉴·바닥글 끝 \*/", html, re.S).group(0),
            re.search(r"// 공통 메뉴:.*?\}\)\(\);", html, re.S).group(0),
        )
        parts[name + "-current"] = current[0]
    assert parts["index"] == parts["claims"] == parts["guide"]
    assert parts["index-current"] == website.SITE
    assert parts["claims-current"] == website.SITE + "claims.html"
    assert parts["guide-current"] == website.SITE + "guide.html"
