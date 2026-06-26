"""
utils/logger.py

WHAT THIS FILE DOES:
A tiny wrapper around Python's built-in `logging` module that gives every
log line a consistent, readable format: timestamp, component name, level,
message.

WHY IT EXISTS:
In a multi-agent system, the #1 debugging need is "which agent said what,
and when, and was it a retry?". Using print() everywhere makes that
impossible to filter or replay. Using raw `logging.basicConfig` everywhere
is fine but means repeating config in every file. This file centralizes it
once.

DESIGN DECISION: We deliberately use the Python standard library's
`logging` module instead of a third-party logging framework. The brief
asks for full understandability with no black boxes — `logging` is already
in the standard library, well documented, and something most Python devs
have seen before.

HOW IT INTERACTS WITH OTHER COMPONENTS:
Every agent and the orchestrator calls `get_logger(__name__)` (or a
component name) at the top of the file and uses it instead of print().
"""

import logging
import sys


_CONFIGURED = False


def _configure_root_logger() -> None:
    """Set up the root logger exactly once, no matter how many times
    get_logger() is called across the codebase."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Public entry point. Usage: `log = get_logger("PlannerAgent")`."""
    _configure_root_logger()
    return logging.getLogger(name)
