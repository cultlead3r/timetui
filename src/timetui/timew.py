"""Interface to the `timew` command line: read (export) and mutating commands.

The argument builders (``args_*``) are pure functions returning the argv list
that would be passed to ``timew``. They are unit-tested without ever executing
``timew``, which keeps real data safe. ``execute`` / ``load_intervals`` are the
only functions that actually shell out.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from .models import Interval

TIMEW_BIN = "timew"
# Time Warrior database directory. ``None`` means "inherit the environment
# unchanged" (so a ``TIMEWARRIORDB`` already set in the shell still applies).
# Set once at startup by ``app.main`` from the ``--timew-dir`` flag / config.
TIMEW_DB: str | None = None


class TimewError(RuntimeError):
    """Raised when a `timew` invocation exits non-zero."""

    def __init__(self, args: Sequence[str], returncode: int, message: str) -> None:
        self.args = list(args)
        self.returncode = returncode
        self.message = message.strip()
        super().__init__(
            f"`timew {' '.join(args)}` failed ({returncode}): {self.message}"
        )


# --------------------------------------------------------------------------- #
# Datetime formatting for timew arguments
# --------------------------------------------------------------------------- #
def fmt_dt(dt: datetime) -> str:
    """Format a datetime as local ISO 8601 (naive) which timew reads as local time."""
    return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def ref(interval_id: int) -> str:
    """Time Warrior interval reference, e.g. 42 -> '@42'."""
    return f"@{interval_id}"


def refs(interval_ids: Sequence[int]) -> list[str]:
    """Multiple interval references, e.g. [1, 3] -> ['@1', '@3']."""
    return [ref(i) for i in interval_ids]


# --------------------------------------------------------------------------- #
# Pure argument builders (testable, never execute)
# --------------------------------------------------------------------------- #
def args_export() -> list[str]:
    return ["export"]


def args_annotate(interval_id: int, text: str) -> list[str]:
    return ["annotate", ref(interval_id), text]


def args_tag(interval_id: int, tags: Sequence[str]) -> list[str]:
    return ["tag", ref(interval_id), *tags]


def args_untag(interval_id: int, tags: Sequence[str]) -> list[str]:
    return ["untag", ref(interval_id), *tags]


def args_modify_start(interval_id: int, dt: datetime) -> list[str]:
    return ["modify", "start", ref(interval_id), fmt_dt(dt)]


def args_modify_end(interval_id: int, dt: datetime) -> list[str]:
    return ["modify", "end", ref(interval_id), fmt_dt(dt)]


def args_delete(interval_id: int) -> list[str]:
    return ["delete", ref(interval_id)]


# Multi-interval variants (timew accepts several @ids in one command, applied
# atomically so id renumbering between them is not a problem).
def args_tag_many(interval_ids: Sequence[int], tags: Sequence[str]) -> list[str]:
    return ["tag", *refs(interval_ids), *tags]


def args_untag_many(interval_ids: Sequence[int], tags: Sequence[str]) -> list[str]:
    return ["untag", *refs(interval_ids), *tags]


def args_delete_many(interval_ids: Sequence[int]) -> list[str]:
    return ["delete", *refs(interval_ids)]


def args_start(tags: Sequence[str]) -> list[str]:
    return ["start", *tags]


def args_stop() -> list[str]:
    return ["stop"]


def args_continue(interval_id: int) -> list[str]:
    return ["continue", ref(interval_id)]


def args_track(start: datetime, end: datetime, tags: Sequence[str]) -> list[str]:
    return ["track", fmt_dt(start), "-", fmt_dt(end), *tags]


def args_undo() -> list[str]:
    return ["undo"]


# --------------------------------------------------------------------------- #
# Database directory / subprocess environment (pure helpers)
# --------------------------------------------------------------------------- #
def resolve_timew_db(cli: str | None, config: str | None) -> str | None:
    """Pick the Time Warrior db dir: the CLI flag wins over the config file.

    Returns ``None`` when neither is set, which tells :func:`build_env` to leave
    the environment untouched so an inherited ``TIMEWARRIORDB`` (or timew's own
    default) still applies. Pure: trivially unit-tested.
    """
    if cli:
        return cli
    if config:
        return config
    return None


def build_env(db: str | None, base: Mapping[str, str]) -> dict[str, str] | None:
    """Build the subprocess environment for a ``timew`` call (pure: unit-tested).

    ``db`` is ``None`` -> return ``None`` so ``subprocess`` inherits ``base``
    unchanged (preserving any ``TIMEWARRIORDB`` already in the environment).
    Otherwise return a copy of ``base`` with ``TIMEWARRIORDB`` set to the
    user-expanded path, overriding any inherited value. ``base`` is never mutated.
    """
    if db is None:
        return None
    env = dict(base)
    env["TIMEWARRIORDB"] = str(Path(db).expanduser())
    return env


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def ensure_db_dir(db: str | None) -> None:
    """Create the configured Time Warrior db directory if it doesn't exist.

    Time Warrior prompts ``Create new config in DIR? (yes/no)`` the first time it
    sees a *missing* database directory. ``export`` runs with no piped stdin
    (``confirm=False``), so in the TUI that prompt blocks on the terminal and
    hangs (and non-interactively it just aborts) — either way a fresh ``--timew-dir``
    is unusable. Once the directory *exists* (even empty), timew initializes the db
    inside it silently, so we create it up front. Impure (touches the filesystem);
    ``db is None`` (inherit the environment) is a no-op. The path is expanded the
    same way as in :func:`build_env` so the created dir matches ``TIMEWARRIORDB``.
    """
    if db is None:
        return
    Path(db).expanduser().mkdir(parents=True, exist_ok=True)


def execute(args: Sequence[str], *, confirm: bool = True) -> str:
    """Run `timew <args>` and return stdout, raising TimewError on failure.

    ``confirm=True`` feeds "yes" on stdin so timew's destructive-action prompts
    never hang the (non-interactive) TUI; our own UI gates dangerous actions.
    The ``TIMEWARRIORDB`` directory comes from :data:`TIMEW_DB` (see
    :func:`build_env`); when unset the parent environment is inherited as-is.
    """
    proc = subprocess.run(
        [TIMEW_BIN, *args],
        capture_output=True,
        text=True,
        input="yes\n" if confirm else None,
        env=build_env(TIMEW_DB, os.environ),
    )
    if proc.returncode != 0:
        raise TimewError(args, proc.returncode, proc.stderr or proc.stdout)
    return proc.stdout


def load_intervals() -> list[Interval]:
    """Run `timew export` and parse it into Interval objects."""
    out = execute(args_export(), confirm=False).strip()
    data = json.loads(out) if out else []
    return [Interval.from_export(item) for item in data]
