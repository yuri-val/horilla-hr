"""A startup diagnostic must never be able to kill startup.

`horilla/config.py` printed `⚠️` in two warnings reached from
`horilla_ldap`'s AppConfig.ready(), so they ran during `django.setup()` for
every management command and for runserver. On a console that is not UTF-8 --
Windows defaults to cp1252 -- encoding that character raises
UnicodeEncodeError, and it propagated straight out of django.setup().

Reported from the field: a v1 -> v2 migration on Windows/Python 3.14 died with

    UnicodeEncodeError: 'charmap' codec can't encode characters in
    position 0-1: character maps to <undefined>

against a database predating the horilla_ldap app, so the table was genuinely
absent and the warning path was correct to fire. The identical warning is
harmless on a UTF-8 console: the terminal's encoding was the whole difference
between a clean migration and a hard failure.

Two guards, because either alone leaves the door open: the messages must encode
in a non-UTF-8 codec, and they must not be `print` -- logging absorbs handler
encoding errors, `print` propagates them.
"""

import pathlib
import re

from django.test import SimpleTestCase

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config.py"
# The encoding a Windows console uses by default, and the one that broke.
WINDOWS_CONSOLE_CODEC = "cp1252"


class StartupDiagnosticsTests(SimpleTestCase):
    def test_config_is_encodable_by_a_windows_console(self):
        source = CONFIG.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            if "logger." not in line and "print(" not in line:
                continue
            try:
                line.encode(WINDOWS_CONSOLE_CODEC)
            except UnicodeEncodeError:
                self.fail(
                    f"horilla/config.py:{lineno} carries a character that "
                    f"{WINDOWS_CONSOLE_CODEC} cannot encode. This file runs "
                    f"during django.setup(); a diagnostic that cannot be "
                    f"written crashes startup.\n    {line.strip()}"
                )

    def test_config_does_not_print_diagnostics(self):
        """
        `print` propagates encoding errors; `logging` hands them to
        Handler.handleError and carries on. In a module that executes during
        app initialisation only one of those is safe.
        """
        source = CONFIG.read_text(encoding="utf-8")
        offenders = [
            f"line {n}: {ln.strip()}"
            for n, ln in enumerate(source.splitlines(), start=1)
            if re.match(r"\s*print\(", ln)
        ]
        self.assertEqual(
            offenders,
            [],
            "horilla/config.py runs during django.setup(); use logger.* so an "
            "unwritable diagnostic cannot take the process down:\n  "
            + "\n  ".join(offenders),
        )
