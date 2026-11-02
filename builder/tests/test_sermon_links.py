"""Each Sunday's page carries a hook that links to the sermon recording on sermons.fpccorinth.org.

The link is hidden in the HTML and revealed by the page's script once the
episodes API has a recording for that date, so a page published on Friday
needs no republish when the recording is posted on Monday.
"""
import re
from pathlib import Path

from builder import config
from builder.pipeline import render, week as weekmod

HOOK = re.compile(r'<p class="sermon-listen" data-sermon-date="(\d{4}-\d{2}-\d{2})" hidden>')


def test_template_renders_the_hook_with_the_sunday_date(store):
    html = render.render_scroll(weekmod.load_week("2026-11-01"), store)
    assert HOOK.search(html).group(1) == "2026-11-01"
    assert "sermons.fpccorinth.org/api/episodes.json?date=" in html


def test_every_published_page_with_a_sermon_has_the_hook():
    for page in sorted((config.REPO / "src/scroll").glob("????-??-??.html")):
        text = page.read_text()
        if 'class="sermon-text-ref"' not in text:
            continue  # midweek services carry no sermon notes
        m = HOOK.search(text)
        assert m and m.group(1) == page.stem, page.name
        assert text.count("sermons.fpccorinth.org/api/episodes.json") == 1, page.name


def test_archive_page_marks_each_week():
    text = (config.REPO / "src/archive.njk").read_text()
    assert '<li data-date="{{ item.date }}">' in text and "archive-listen" in text
