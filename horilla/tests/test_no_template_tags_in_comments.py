"""Django template tags must not hide inside CSS or JavaScript comments.

Django's lexer tokenises `{% ... %}` across the whole file. It has no idea what
CSS or JS comments are -- only `{% comment %}` and `{# #}` are template
comments. So a tag written inside a `/* ... */` block, as documentation
describing where the real tag lives, is parsed and executed like any other tag.

That shipped. `modern_filter_panel.html` carried

    /* The Group By accordion (appended in horilla_nav.html, right after
       {% include filter_body_template %}) is never a true DOM sibling ... */

inside a `<style>` block. `filter_body_template` is set as a class attribute by
the list views, so every page that includes the panel through the generic nav
was fine -- but the four templates that include it directly never set it, the
variable resolved empty, and `{% include %}` raised

    TemplateDoesNotExist: No template names provided

500ing /attendance/work-records/, the skill zone view and the attendance
monthly summary. It went out in 2.1.0, 2.1.1 and 2.1.2 before anyone caught it,
because nothing reads CSS comments looking for template tags. This test does.

Scoped to the tags that can actually raise. `{{ var }}` and `{% trans %}` in a
comment render harmlessly, and failing on those would need an allow-list that
would rot; `{% include %}`, `{% extends %}`, `{% url %}` and `{% ssi %}` all
resolve something that can be missing, which is what turns a comment into a
500.
"""

import re
import unittest
from pathlib import Path

# Directories that are not ours to police.
SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    "staticfiles",
    "venv",
    ".venv",
    "env",
}

# Tags that resolve something which can be absent, and therefore raise.
DANGEROUS_TAG = re.compile(r"{%\s*(include|extends|url|ssi)\b")

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# `{# #}` is single-line ONLY. Django's lexer is
#     ({%.*?%}|{{.*?}}|{#.*?#})
# compiled WITHOUT re.DOTALL, so `.` never crosses a newline and a `{# #}`
# spanning lines is not a comment at all -- it renders as literal text and any
# tag inside it is parsed and executed.
#
# Blanking multi-line `{# #}` here (which DOTALL would do) modelled Django
# wrongly in exactly that spot: a dangerous tag wrapped in a multi-line `{# #}`
# was reported inert while Django ran it. `[^\n]` keeps this matching the real
# lexer. `{% comment %}` genuinely does span lines, so it keeps DOTALL.
TEMPLATE_COMMENT = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#[^\n]*?#}", re.DOTALL
)

# An opening `{#` with no `#}` before the end of the line.
UNTERMINATED_HASH_COMMENT = re.compile(r"{#(?![^\n]*?#})")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _blank_out(match):
    """Replace a match with spaces, keeping newlines.

    Deleting the text instead would shift every line number after it, and the
    whole value of this test is naming the line to go and fix.
    """
    return re.sub(r"[^\n]", " ", match.group(0))


def _offences(text):
    """Yield (line_number, tag) for dangerous tags inside CSS/JS comments."""
    # Real template comments are not executed, so blank them first -- a tag
    # inside {% comment %} is genuinely inert and must not be reported.
    stripped = TEMPLATE_COMMENT.sub(_blank_out, text)

    for block in CSS_COMMENT.finditer(stripped):
        for tag in DANGEROUS_TAG.finditer(block.group(0)):
            offset = block.start() + tag.start()
            yield stripped.count("\n", 0, offset) + 1, tag.group(0).strip()

    for lineno, line in enumerate(stripped.splitlines(), start=1):
        if line.lstrip().startswith("//"):
            found = DANGEROUS_TAG.search(line)
            if found:
                yield lineno, found.group(0).strip()


def _templates():
    for path in REPO_ROOT.rglob("*.html"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


class TemplateTagsInCommentsTests(unittest.TestCase):
    def test_no_dangerous_tag_is_hidden_in_a_comment(self):
        offences = []
        for path in _templates():
            text = path.read_text(encoding="utf-8", errors="replace")
            # Cheap reject: most templates have no CSS/JS comment at all.
            if "/*" not in text and "//" not in text:
                continue
            for lineno, tag in _offences(text):
                offences.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} contains {tag} ...%}}"
                )

        self.assertEqual(
            offences,
            [],
            "Django parses these tags even though they sit in a CSS/JS comment, "
            "so they run on every render and raise when what they resolve is "
            "missing. Reword the comment so it does not contain template "
            "syntax (or wrap it in {% verbatim %}):\n  " + "\n  ".join(offences),
        )

    def test_detects_a_planted_offence(self):
        """The check must fail on the exact shape that shipped."""
        planted = (
            "<style>\n"
            ".x {\n"
            "    /* appended in horilla_nav.html, right after\n"
            "       {% include filter_body_template %}) is never a sibling */\n"
            "    margin-top: 4px;\n"
            "}\n"
            "</style>\n"
        )
        self.assertEqual(list(_offences(planted)), [(4, "{% include")])

    def test_ignores_tags_in_real_template_comments(self):
        """{% comment %} and {# #} are not executed, so they are not offences."""
        inert = (
            "{% comment %}\n"
            "/* {% include 'a.html' %} */\n"
            "{% endcomment %}\n"
            "{# /* {% url 'x' %} */ #}\n"
        )
        self.assertEqual(list(_offences(inert)), [])

    def test_ignores_harmless_tags(self):
        """{{ var }} and {% trans %} in a comment cannot raise."""
        harmless = "<style>/* {{ user.name }} and {% trans 'hi' %} */</style>"
        self.assertEqual(list(_offences(harmless)), [])

    def test_a_multi_line_hash_comment_is_not_treated_as_inert(self):
        """Wrapping a dangerous tag in a multi-line {# #} must not hide it.

        Django does not read that as a comment, so the tag runs. The blanking
        step used to model it as inert, which silently disarmed this test for
        exactly the shape that shipped.
        """
        wrapped = (
            "{# a note that runs on past\n"
            "<style>\n/* {% include filter_body_template %} */</style>\n"
            "   the end of its first line #}\n"
        )
        self.assertNotEqual(list(_offences(wrapped)), [])


class MultiLineHashCommentTests(unittest.TestCase):
    """`{# ... #}` must open and close on one line.

    Django's lexer is compiled without re.DOTALL, so a `{# #}` spanning lines
    is not tokenised as a comment: it renders as literal text. Nine report
    templates shipped that way in 2.1.5, printing an internal note about CSS
    duplication onto all seven explorer pages, the standard report letterhead
    and -- worst -- into the generated PDFs that get circulated outside the
    company. Use `{% comment %}` for anything spanning lines.
    """

    def test_no_template_has_a_multi_line_hash_comment(self):
        offences = []
        for path in _templates():
            text = path.read_text(encoding="utf-8", errors="replace")
            if "{#" not in text:
                continue
            for match in UNTERMINATED_HASH_COMMENT.finditer(text):
                lineno = text.count("\n", 0, match.start()) + 1
                offences.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

        self.assertEqual(
            offences,
            [],
            "`{# #}` does not span lines -- Django renders these to the page "
            "as literal text. Use {% comment %} ... {% endcomment %}:\n  "
            + "\n  ".join(offences),
        )

    def test_detects_a_planted_multi_line_comment(self):
        planted = "{# first line\n   second line #}\n"
        self.assertEqual(len(UNTERMINATED_HASH_COMMENT.findall(planted)), 1)

    def test_allows_a_single_line_comment(self):
        self.assertEqual(UNTERMINATED_HASH_COMMENT.findall("{# fine #}\n"), [])

    def test_allows_several_single_line_comments(self):
        text = "{# one #}\n{# two #}\n"
        self.assertEqual(UNTERMINATED_HASH_COMMENT.findall(text), [])
