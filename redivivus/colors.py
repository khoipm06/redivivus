"""
ANSI terminal styling and color utilities for Redivivus.
Supports NO_COLOR environment variable and non-TTY autodetection.
"""

import os
import re
import sys
from typing import Optional


# ANSI escape sequences
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

BOLD_RED = "\033[1;31m"
BOLD_GREEN = "\033[1;32m"
BOLD_YELLOW = "\033[1;33m"
BOLD_BLUE = "\033[1;34m"
BOLD_CYAN = "\033[1;36m"
BOLD_WHITE = "\033[1;37m"


_ANSI_REGEX = re.compile(r"\x1b\[[0-9;]*m")


def colors_enabled() -> bool:
    """Checks if color output should be enabled.
    
    Disabled if NO_COLOR is set, TERM is 'dumb', or stdout is not a TTY.
    """
    if "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != "":
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def strip_ansi(text: str) -> str:
    """Removes all ANSI escape codes from string."""
    return _ANSI_REGEX.sub("", str(text))


def visible_len(text: str) -> int:
    """Returns the visual length of text, ignoring ANSI escape sequences."""
    return len(strip_ansi(text))


def colorize(text: str, color_code: str) -> str:
    """Wraps text in color code if colors are enabled."""
    if not colors_enabled():
        return str(text)
    return f"{color_code}{text}{RESET}"


def green(text: str) -> str:
    return colorize(text, GREEN)


def red(text: str) -> str:
    return colorize(text, RED)


def yellow(text: str) -> str:
    return colorize(text, YELLOW)


def blue(text: str) -> str:
    return colorize(text, BLUE)


def cyan(text: str) -> str:
    return colorize(text, CYAN)


def bold(text: str) -> str:
    return colorize(text, BOLD)


def dim(text: str) -> str:
    return colorize(text, DIM)


def bold_green(text: str) -> str:
    return colorize(text, BOLD_GREEN)


def bold_red(text: str) -> str:
    return colorize(text, BOLD_RED)


def bold_yellow(text: str) -> str:
    return colorize(text, BOLD_YELLOW)


def bold_cyan(text: str) -> str:
    return colorize(text, BOLD_CYAN)


def bold_white(text: str) -> str:
    return colorize(text, BOLD_WHITE)


def format_status_badge(val: str) -> str:
    """Applies contextual color styling to common status keywords."""
    s = str(val).strip()
    s_upper = s.upper()
    if s_upper in ("PASS", "YES", "FOUND", "OK", "SUCCESS", "ACTIVE", "*"):
        return green(s)
    elif s_upper in ("FAIL", "NO", "MISSING", "ERROR", "FAILURE"):
        return red(s)
    elif s_upper in ("WARNING", "WARN", "(NONE)", "(N/A)", "UNMOUNTED"):
        return yellow(s)
    return s
