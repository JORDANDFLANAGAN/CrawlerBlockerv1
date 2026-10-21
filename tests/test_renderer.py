import pytest

from tarpit.renderer import render_maze_page, render_robots_txt, render_root_page

HUMAN_COPY = "If you can read this, you're probably a human — or at least not a crappy bot."


def test_render_robots_txt_exact() -> None:
    assert render_robots_txt() == "User-agent: *\nDisallow: /maze/\n"


def test_render_root_page_contains_human_copy() -> None:
    output = render_root_page()
    # Human-copy must appear verbatim (possibly with HTML-escaped chars like ')
    # The apostrophe in "you're" gets escaped to &#x27; or similar
    assert "If you can read this" in output
    assert "probably a human" in output


def test_render_root_page_hidden_anchor() -> None:
    output = render_root_page()
    assert '<a style="display:none" href="/maze/root">' in output


def test_render_root_page_exactly_one_maze_anchor() -> None:
    output = render_root_page()
    count = output.count('href="/maze/')
    assert count == 1


def test_render_maze_page_contains_words_in_paragraph() -> None:
    output = render_maze_page("a8f3", ["ahab", "whale"], ["1111", "2222"])
    assert "<p>ahab whale</p>" in output


def test_render_maze_page_contains_anchors() -> None:
    output = render_maze_page("a8f3", ["ahab", "whale"], ["1111", "2222"])
    assert '<a style="display:none" href="/maze/1111">' in output
    assert '<a style="display:none" href="/maze/2222">' in output


def test_render_maze_page_anchor_count() -> None:
    output = render_maze_page("a8f3", ["hello"], ["1111", "2222", "3333"])
    assert output.count('style="display:none"') == 3


def test_render_maze_page_xss_in_id() -> None:
    output = render_maze_page("<x>", ["<script>"], ["<y>"])
    assert "<script>" not in output
    assert "<x>" not in output
    assert "<y>" not in output
    # < appears only as &lt;
    assert "&lt;" in output


def test_render_maze_page_html_escapes_all_values() -> None:
    output = render_maze_page("<id>", ["<word>"], ["<link>"])
    assert "<id>" not in output
    assert "<word>" not in output
    assert "<link>" not in output


def test_render_robots_txt_no_io() -> None:
    # Pure function — calling multiple times returns same result
    assert render_robots_txt() == render_robots_txt()


def test_render_root_page_no_io() -> None:
    assert render_root_page() == render_root_page()


def test_render_maze_page_no_io() -> None:
    r1 = render_maze_page("abc", ["hello", "world"], ["1234", "5678"])
    r2 = render_maze_page("abc", ["hello", "world"], ["1234", "5678"])
    assert r1 == r2
