from patent_radar import website


def test_site_page_puts_title_and_link_preview_in_head():
    page = website.page_html()
    head, body = page.split("</head>", 1)
    assert "<title>LFP · 이차전지 특허 레이더 | Patent Radar</title>" in head
    assert '<meta property="og:image" content="https://chojiwon00.github.io/patent-radar/og.png">' in head
    assert 'name="description"' in head
    assert "<title>" not in body  # artifact 본문의 제목은 머리말로 옮겼다
    assert body.rstrip().endswith("</html>")
