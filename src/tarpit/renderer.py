import html
from collections.abc import Sequence

from tarpit.ids import BAIT_ID

HUMAN_COPY = "If you can read this, you're probably a human — or at least not a crappy bot."


def render_robots_txt() -> str:
    """Return exactly `User-agent: *\nDisallow: /maze/\n`."""
    return "User-agent: *\nDisallow: /maze/\n"


def render_root_page() -> str:
    """Return the HTML for GET /. Includes the fixed human-copy <p> and
    one hidden anchor to /maze/{BAIT_ID}. HTML-escapes BAIT_ID."""
    escaped_bait = html.escape(BAIT_ID, quote=True)
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head><title>Welcome</title></head>\n"
        "<body>\n"
        f"<p>{html.escape(HUMAN_COPY, quote=True)}</p>\n"
        f'<a style="display:none" href="/maze/{escaped_bait}">enter</a>\n'
        "</body>\n"
        "</html>\n"
    )


def render_maze_page(id_: str, words: Sequence[str], new_ids: Sequence[str]) -> str:
    """Return the HTML for GET /maze/{id}.

    - HTML-escapes `id_` and every element of `words` and `new_ids` before
      insertion.
    - Produces exactly one <p> containing `' '.join(words)`.
    - Produces one hidden <a style="display:none" href="/maze/{new_id}">
      per element in `new_ids`."""
    escaped_id = html.escape(id_, quote=True)
    escaped_words = " ".join(html.escape(w, quote=True) for w in words)

    anchors = "\n".join(
        f'<a style="display:none" href="/maze/{html.escape(new_id, quote=True)}">continue</a>'
        for new_id in new_ids
    )

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        f"<head><title>Page {escaped_id}</title></head>\n"
        "<body>\n"
        f"<p>{escaped_words}</p>\n"
        f"{anchors}\n"
        "</body>\n"
        "</html>\n"
    )
