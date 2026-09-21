"""Rich terminal styling for rtdm output.

This is a *presentation-only* layer. It is invoked from :mod:`rtdm.main`
**only when stdout is a TTY** — piped output, ``-x`` execution, and ``-c``
clipboard all use the raw model text, never these styled forms, so styling
can never corrupt a command that gets executed, copied, or piped onward.

Treatments:
  * command (query) mode -> a colored gutter bar ("┃ <command>"), bold;
    ``ERROR:`` responses render red.
  * ask / explain modes  -> a bordered panel with the text markdown-rendered
    (explain = gold, ask = blue).
"""

from __future__ import annotations

from rich.box import ROUNDED
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

EXPLAIN_COLOR = "#F2C94C"  # light gold
ASK_COLOR = "#4DA3FF"      # blue
CMD_COLOR = "bold cyan"


def render(task: str, output: str, console: Console | None = None) -> None:
    """Render ``output`` for ``task`` ('query' | 'ask' | 'explain') to a TTY."""
    console = console or Console()
    out = output.rstrip("\n")
    if not out:
        return
    if task == "query":
        _render_command(console, out)
    else:
        _render_prose(console, task, out)


def _render_command(console: Console, out: str) -> None:
    error = out.startswith("ERROR:")
    t = Text()
    t.append("┃ ", style="bold red" if error else CMD_COLOR)
    t.append(out, style="bold red" if error else "bold")
    console.print(t)


def _render_prose(console: Console, task: str, out: str) -> None:
    title = "rtdm · explain" if task == "explain" else "rtdm · ask"
    color = EXPLAIN_COLOR if task == "explain" else ASK_COLOR
    console.print(
        Panel(Markdown(out), title=title, title_align="left",
              border_style=color, box=ROUNDED, padding=(0, 1))
    )
