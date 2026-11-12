# -*- coding: utf-8 -*-
"""Feedback about Kvist itself: the model drafts a GitHub issue, the person reads it and decides.

Nothing leaves the machine on the model's say. The ``feedback`` action only writes the draft to
``<workspace>/.kvist/feedback/draft.json``; the person opens it with ``/feedback``, sees it as a letter
(to, subject, text) and picks send, keep or discard. Kept, it can be reworked with the model, which
overwrites the draft. Sending goes through ``gh issue create`` when the GitHub CLI is signed in, else
through the browser with the issue form already filled in.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote

from kernel.config.self_update import RELEASE_REPO
from kernel.config.version import kvist_version
from kernel.sandbox.redaction import redact_text
from kernel.session.durable_json import (
    JsonStorageError,
    delete_json_object,
    read_json_object,
    write_json_object,
)

ACTION = "feedback"
MAX_TITLE_CHARS = 120
# A prefilled issue form travels in the URL; browsers and GitHub refuse far beyond this.
MAX_BODY_CHARS = 6000
ISSUES_URL = f"https://github.com/{RELEASE_REPO}/issues"
GH_TIMEOUT_SECONDS = 60


class FeedbackError(Exception):
    """A feedback step the runtime refuses, with the reason for the person or the model."""


def _paths(workspace: Path) -> tuple[Path, Path]:
    state = Path(workspace) / ".kvist"
    return state / "feedback" / "draft.json", state


def save_draft(workspace: Path, title: str, body: str) -> dict[str, Any]:
    """Store the draft, scrubbed of credentials; a second call replaces the first."""
    draft = {"title": " ".join(redact_text(title).split()), "body": redact_text(body).strip()}
    path, boundary = _paths(workspace)
    try:
        boundary.mkdir(mode=0o700, exist_ok=True)
        write_json_object(path, draft, boundary=boundary)
    except (JsonStorageError, OSError) as error:
        raise FeedbackError(f"The draft could not be saved: {error}") from error
    return draft


def load_draft(workspace: Path) -> dict[str, Any] | None:
    path, boundary = _paths(workspace)
    if not path.exists():
        return None
    try:
        draft = read_json_object(path, boundary=boundary)
    except (JsonStorageError, OSError) as error:
        raise FeedbackError(f"The draft could not be read: {error}") from error
    return draft if draft.get("title") and draft.get("body") else None


def discard_draft(workspace: Path) -> None:
    path, boundary = _paths(workspace)
    try:
        delete_json_object(path, boundary=boundary)
    except (JsonStorageError, OSError) as error:
        raise FeedbackError(f"The draft could not be removed: {error}") from error


def compose(draft: dict[str, Any]) -> tuple[str, str]:
    """The title and body exactly as they are sent: the draft plus a footer with the build and the system."""
    footer = f"\n\n---\nKvist {kvist_version()} · {platform.system()}"
    return draft["title"], draft["body"] + footer


def preview(draft: dict[str, Any], russian: bool) -> str:
    """The draft as a letter, so the person sees what would go out."""
    title, body = compose(draft)
    to, subject = ("Кому", "Тема") if russian else ("To", "Subject")
    return f"**{to}:** {ISSUES_URL}\n**{subject}:** {title}\n\n{body}"


def issue_form_url(title: str, body: str) -> str:
    return f"{ISSUES_URL}/new?title={quote(title)}&body={quote(body)}"


def submit(draft: dict[str, Any]) -> str:
    """Send the draft as an issue; the reply says where it went."""
    title, body = compose(draft)
    url = _create_with_gh(title, body)
    if url:
        return f"gh: {url}"
    form = issue_form_url(title, body)
    if len(form) > 7500:
        raise FeedbackError(
            "The letter is too long for a prefilled form and the GitHub CLI is not signed in "
            "(gh auth login). The draft is kept."
        )
    webbrowser.open(form)
    return f"browser: {form}"


def _create_with_gh(title: str, body: str) -> str:
    """The new issue's URL, or "" when gh is missing or not signed in (the browser form takes over)."""
    gh = shutil.which("gh")
    if gh is None:
        return ""
    try:
        done = subprocess.run(
            [gh, "issue", "create", "--repo", RELEASE_REPO, "--title", title, "--body-file", "-"],
            input=body, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip().splitlines()[-1] if done.returncode == 0 and done.stdout.strip() else ""


def run_dialog(workspace: Path, ui: Any, russian: bool) -> tuple[bool, str]:
    """``/feedback``: show the draft, let the person send, keep or discard it. Returns (ok, message)."""
    try:
        draft = load_draft(workspace)
    except FeedbackError as error:
        return False, str(error)
    if draft is None:
        return False, (
            "Черновика нет. Попроси Kvist составить отзыв, потом открой /feedback."
            if russian else "There is no draft. Ask Kvist to write the feedback, then run /feedback."
        )
    ui.print_user_markdown(preview(draft, russian))
    options = [
        {"value": "send", "label": "Отправить" if russian else "Send",
         "description": "Создать issue в репозитории Kvist" if russian else "Open an issue in the Kvist repository"},
        {"value": "keep", "label": "Оставить" if russian else "Keep",
         "description": "Попроси Kvist доработать письмо, потом снова /feedback" if russian
         else "Ask Kvist to rework it, then run /feedback again"},
        {"value": "discard", "label": "Удалить" if russian else "Discard", "description": ""},
    ]
    choice = ui.choose_option("Отправить отзыв?" if russian else "Send this feedback?", options, current_value="keep")
    if choice == "discard":
        try:
            discard_draft(workspace)
        except FeedbackError as error:
            return False, str(error)
        return True, "Черновик удалён." if russian else "Draft discarded."
    if choice != "send":
        return True, "Черновик оставлен." if russian else "Draft kept."
    try:
        where = submit(draft)
    except FeedbackError as error:
        return False, str(error)
    via, _, target = where.partition(": ")
    if via == "gh":
        try:
            discard_draft(workspace)
        except FeedbackError as error:
            return False, str(error)
        return True, f"Отправлено: {target}" if russian else f"Sent: {target}"
    # The form is only opened: the draft stays until the person has pressed Submit and removes it themselves.
    return True, (
        f"Открыла форму issue в браузере; нажми Submit там (черновик остаётся, пока не удалишь его). Ссылка: {target}"
        if russian else
        f"Opened the issue form in your browser; press Submit there (the draft stays until you discard it). Link: {target}"
    )
