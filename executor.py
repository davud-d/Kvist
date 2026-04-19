# -*- coding: utf-8 -*-
from __future__ import annotations

import fnmatch
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from protocol import AgentCommand
from terminal import TerminalExecutor, TerminalError


class ExecutorError(Exception):
    pass


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._text_parts: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False

    @property
    def title(self) -> str:
        return " ".join("".join(self._title_parts).split())

    @property
    def text(self) -> str:
        return "\n".join(part for part in (item.strip() for item in self._text_parts) if part)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = unescape(data)
        if self._in_title:
            self._title_parts.append(text)
        self._text_parts.append(text)


@dataclass(slots=True)
class ExecutionResult:
    ok: bool
    action_type: str
    data: dict[str, Any]


class SafeExecutor:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.max_read_chars = 20000
        self.max_output_chars = 12000
        self.max_search_results = 50
        self.max_file_size_bytes = 256 * 1024
        self.max_writable_file_bytes = 2 * 1024 * 1024

        self._terminal = TerminalExecutor(
            workspace=self.workspace,
            timeout=15.0,
            max_output_chars=self.max_output_chars,
        )

        self.ignored_dirs = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
        }

        self.allowed_web_domains = {
            "docs.python.org",
            "pypi.org",
            "packaging.python.org",
            "fastapi.tiangolo.com",
            "docs.pydantic.dev",
            "docs.sqlalchemy.org",
            "www.python-httpx.org",
            "requests.readthedocs.io",
            "aiohttp.readthedocs.io",
            "pandas.pydata.org",
            "numpy.org",
            "matplotlib.org",
            "docs.pytest.org",
            "flask.palletsprojects.com",
            "django.readthedocs.io",
            "docs.celeryq.dev",
            "docs.aiogram.dev",
            "docs.telethon.dev",
            "platform.openai.com",
            "openai.com",
            "github.com",
            "stackoverflow.com",
        }

        self.web_search_provider = (os.getenv("SEARCH_PROVIDER", "auto").strip().lower() or "auto")
        self.searxng_base_url = os.getenv("SEARXNG_BASE_URL", "").strip().rstrip("/")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.brave_api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()

        self.web_timeout = self._read_float_env("WEB_SEARCH_TIMEOUT", default=8.0, min_value=1.0, max_value=30.0)
        self.web_max_results = self._read_int_env("WEB_SEARCH_MAX_RESULTS", default=5, min_value=1, max_value=20)
        self.max_web_response_bytes = 500_000
        self.max_web_snippet_chars = 320
        self.max_fetch_chars_default = 12000
        self.max_fetch_chars_limit = 50000
        self.disallowed_fetch_suffixes = {
            ".zip",
            ".gz",
            ".bz2",
            ".xz",
            ".rar",
            ".7z",
            ".tar",
            ".tgz",
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".bin",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".mp3",
            ".mp4",
            ".avi",
        }

    def execute(self, command: AgentCommand) -> ExecutionResult:
        action_type = command.action.type
        args = command.action.args

        if action_type == "final_answer":
            return ExecutionResult(
                ok=True,
                action_type=action_type,
                data={"message": args["message"]},
            )

        if action_type == "read_file":
            return self._read_file(args["path"])

        if action_type == "read_multiple_files":
            return self._read_multiple_files(args["paths"])

        if action_type == "write_file":
            return self._write_file(args["path"], args["content"])

        if action_type == "append_file":
            return self._append_file(args["path"], args["content"])

        if action_type == "replace_in_file":
            return self._replace_in_file(
                path=args["path"],
                old_text=args["old_text"],
                new_text=args["new_text"],
                count=args.get("count", 1),
            )

        if action_type == "list_dir":
            return self._list_dir(args["path"])

        if action_type == "list_tree":
            return self._list_tree(
                path=args["path"],
                max_depth=args.get("max_depth", 5),
            )

        if action_type == "find_files":
            return self._find_files(
                pattern=args["pattern"],
                path=args.get("path", ""),
            )

        if action_type == "search_code":
            return self._search_code(
                query=args["query"],
                path=args.get("path", ""),
                max_results=args.get("max_results", 20),
            )

        if action_type == "git_status":
            return self._git_status()

        if action_type == "git_diff":
            return self._git_diff(
                path=args.get("path"),
                ref=args.get("ref"),
                staged=args.get("staged", False),
            )

        if action_type == "run_shell":
            return self._run_shell(
                args["command"],
                cwd=args.get("cwd"),
                timeout=args.get("timeout"),
            )

        if action_type == "web_search":
            return self._web_search(
                query=args["query"],
                top_k=args.get("top_k"),
                domain_filter=args.get("domain_filter"),
                freshness=args.get("freshness", "any"),
                max_results=args.get("max_results"),
            )

        if action_type == "fetch_url":
            return self._fetch_url(
                url=args["url"],
                max_chars=args.get("max_chars"),
                extract_mode=args.get("extract_mode", "text"),
            )

        raise ExecutorError(f"Неподдерживаемое действие: {action_type}")

    def _resolve_path(self, user_path: str) -> Path:
        if not isinstance(user_path, str):
            raise ExecutorError("Путь должен быть строкой")

        candidate = (self.workspace / user_path).resolve()

        try:
            candidate.relative_to(self.workspace)
        except ValueError as error:
            raise ExecutorError("Выход за пределы workspace запрещен") from error

        return candidate

    def _resolve_dir(self, user_path: str | None) -> Path:
        safe_path = user_path or ""
        target = self._resolve_path(safe_path)
        if not target.exists():
            raise ExecutorError(f"Путь не найден: {safe_path}")
        if not target.is_dir():
            raise ExecutorError(f"Это не директория: {safe_path}")
        return target

    def _relative_path(self, target: Path) -> str:
        return target.relative_to(self.workspace).as_posix()

    _ENV_PATTERN = re.compile(r"(^|[\\/])\.env(\.[a-zA-Z0-9_.-]*)?$|[a-zA-Z0-9_]*\.env$", re.IGNORECASE)

    def _check_env_file(self, path: str) -> None:
        """Блокирует любой доступ к .env файлам."""
        if self._ENV_PATTERN.search(path):
            raise ExecutorError(
                "⛔ НАРУШЕНИЕ ПРОТОКОЛА БЕЗОПАСНОСТИ: Попытка доступа к .env файлу запрещена. "
                "Файлы *.env содержат секреты и не могут быть прочитаны агентом. "
                "Если вам нужна информация о переменных окружения — спросите пользователя напрямую."
            )

    def _read_file(self, path: str) -> ExecutionResult:
        self._check_env_file(path)
        target = self._resolve_path(path)

        if not target.exists():
            raise ExecutorError(f"Файл не найден: {path}")

        if not target.is_file():
            raise ExecutorError(f"Это не файл: {path}")

        content, truncated = self._safe_read_text(target)

        return ExecutionResult(
            ok=True,
            action_type="read_file",
            data={
                "path": path,
                "content": content,
                "truncated": truncated,
            },
        )

    def _read_multiple_files(self, paths: list[str]) -> ExecutionResult:
        files: list[dict[str, Any]] = []
        for path in paths:
            self._check_env_file(path)
            target = self._resolve_path(path)
            if not target.exists() or not target.is_file():
                raise ExecutorError(f"Файл не найден: {path}")
            content, truncated = self._safe_read_text(target)
            files.append(
                {
                    "path": path,
                    "content": content,
                    "truncated": truncated,
                }
            )

        return ExecutionResult(
            ok=True,
            action_type="read_multiple_files",
            data={"files": files},
        )

    def _write_file(self, path: str, content: str) -> ExecutionResult:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return ExecutionResult(
            ok=True,
            action_type="write_file",
            data={
                "path": path,
                "bytes_written": len(content.encode("utf-8")),
            },
        )

    def _append_file(self, path: str, content: str) -> ExecutionResult:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as file:
            file.write(content)

        return ExecutionResult(
            ok=True,
            action_type="append_file",
            data={
                "path": path,
                "bytes_appended": len(content.encode("utf-8")),
            },
        )

    def _replace_in_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        count: int = 1,
    ) -> ExecutionResult:
        target = self._resolve_path(path)

        if not target.exists():
            raise ExecutorError(f"Файл не найден: {path}")

        if not target.is_file():
            raise ExecutorError(f"Это не файл: {path}")

        original_text = self._read_full_text(target)
        occurrences_found = original_text.count(old_text)
        if occurrences_found == 0:
            raise ExecutorError("Фрагмент old_text не найден в файле")

        occurrences_replaced = min(occurrences_found, count)
        updated_text = original_text.replace(old_text, new_text, count)
        target.write_text(updated_text, encoding="utf-8")

        return ExecutionResult(
            ok=True,
            action_type="replace_in_file",
            data={
                "path": path,
                "occurrences_found": occurrences_found,
                "occurrences_replaced": occurrences_replaced,
                "bytes_written": len(updated_text.encode("utf-8")),
            },
        )

    def _list_dir(self, path: str) -> ExecutionResult:
        target = self._resolve_path(path)

        if not target.exists():
            raise ExecutorError(f"Путь не найден: {path}")

        if not target.is_dir():
            raise ExecutorError(f"Это не директория: {path}")

        items = []
        for item in sorted(target.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            items.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                }
            )

        return ExecutionResult(
            ok=True,
            action_type="list_dir",
            data={
                "path": path,
                "items": items,
            },
        )

    def _list_tree(self, path: str, max_depth: int = 5) -> ExecutionResult:
        target = self._resolve_path(path)

        if not target.exists():
            raise ExecutorError(f"Путь не найден: {path}")

        if not target.is_dir():
            raise ExecutorError(f"Это не директория: {path}")

        max_depth = max(1, min(max_depth, 10))
        lines: list[str] = []
        max_entries = 500
        self._build_tree(target, "", lines, max_depth=max_depth, current_depth=0, counter=[0], limit=max_entries)
        truncated = len(lines) >= max_entries

        return ExecutionResult(
            ok=True,
            action_type="list_tree",
            data={
                "path": path,
                "tree": "\n".join(lines),
                "truncated": truncated,
            },
        )

    def _build_tree(
        self,
        directory: Path,
        prefix: str,
        lines: list[str],
        max_depth: int,
        current_depth: int,
        counter: list[int],
        limit: int,
    ) -> None:
        if current_depth >= max_depth or counter[0] >= limit:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            return

        entries = [e for e in entries if e.name not in self.ignored_dirs]

        for i, entry in enumerate(entries):
            if counter[0] >= limit:
                return
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                counter[0] += 1
                extension = "    " if is_last else "│   "
                self._build_tree(
                    entry, prefix + extension, lines,
                    max_depth, current_depth + 1, counter, limit,
                )
            elif entry.is_file():
                lines.append(f"{prefix}{connector}{entry.name}")
                counter[0] += 1

    def _find_files(self, pattern: str, path: str = "") -> ExecutionResult:
        base_dir = self._resolve_dir(path)
        matches: list[str] = []

        for file_path in self._iter_files(base_dir):
            relative = self._relative_path(file_path)
            if fnmatch.fnmatch(file_path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                matches.append(relative)
            if len(matches) >= 200:
                break

        return ExecutionResult(
            ok=True,
            action_type="find_files",
            data={
                "pattern": pattern,
                "path": path,
                "matches": matches,
            },
        )

    def _search_code(
        self,
        query: str,
        path: str = "",
        max_results: int = 20,
    ) -> ExecutionResult:
        base_dir = self._resolve_dir(path)
        query_folded = query.casefold()
        results: list[dict[str, Any]] = []
        safe_limit = max(1, min(max_results, self.max_search_results))

        for file_path in self._iter_files(base_dir):
            if self._ENV_PATTERN.search(file_path.name):
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                if query_folded in line.casefold():
                    results.append(
                        {
                            "path": self._relative_path(file_path),
                            "line": line_number,
                            "text": self._truncate_text(line.strip(), 300),
                        }
                    )
                if len(results) >= safe_limit:
                    break

            if len(results) >= safe_limit:
                break

        return ExecutionResult(
            ok=True,
            action_type="search_code",
            data={
                "query": query,
                "path": path,
                "results": results,
            },
        )

    def _git_status(self) -> ExecutionResult:
        try:
            result = self._terminal.run(["git", "status", "--short", "--branch"])
        except TerminalError as exc:
            raise ExecutorError(str(exc))
        return ExecutionResult(
            ok=result.returncode == 0,
            action_type="git_status",
            data={
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )

    def _git_diff(
        self,
        path: str | None = None,
        ref: str | None = None,
        staged: bool = False,
    ) -> ExecutionResult:
        command = ["git", "diff"]
        if staged:
            command.append("--staged")
        if ref:
            command.append(ref)
        if path:
            resolved = self._resolve_path(path)
            relative_path = self._relative_path(resolved)
            command.extend(["--", relative_path])

        try:
            result = self._terminal.run(command)
        except TerminalError as exc:
            raise ExecutorError(str(exc))
        return ExecutionResult(
            ok=result.returncode == 0,
            action_type="git_diff",
            data={
                "returncode": result.returncode,
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )

    def _run_shell(
        self,
        command: list[str],
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        # Блокируем shell-команды, которые могут читать .env файлы
        cmd_str = " ".join(command)
        if self._ENV_PATTERN.search(cmd_str):
            raise ExecutorError(
                "⛔ НАРУШЕНИЕ ПРОТОКОЛА БЕЗОПАСНОСТИ: Shell-команда пытается получить доступ к .env файлу. "
                "Чтение *.env файлов категорически запрещено."
            )
        try:
            result = self._terminal.run(command, cwd=cwd, timeout=timeout)
        except TerminalError as exc:
            raise ExecutorError(str(exc))

        return ExecutionResult(
            ok=result.returncode == 0,
            action_type="run_shell",
            data={
                "command": result.command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
            },
        )

    def _web_search(
        self,
        query: str,
        top_k: int | None = None,
        domain_filter: list[str] | None = None,
        freshness: str = "any",
        max_results: int | None = None,
    ) -> ExecutionResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ExecutorError("Пустой query для web_search")

        requested = top_k if top_k is not None else self.web_max_results
        if max_results is not None:
            requested = min(requested, max_results)
        limit = max(1, min(requested, self.web_max_results))

        normalized_freshness = freshness.strip().lower() if freshness else "any"
        if normalized_freshness not in {"any", "day", "week", "month"}:
            normalized_freshness = "any"

        normalized_domain_filter = self._normalize_domain_filter(domain_filter)
        provider_errors: list[str] = []

        for provider in self._provider_order():
            if not self._provider_available(provider):
                provider_errors.append(f"{provider}: не настроен")
                continue

            try:
                provider_results = self._search_with_provider(
                    provider=provider,
                    query=normalized_query,
                    top_k=limit,
                    freshness=normalized_freshness,
                )
            except ExecutorError as error:
                provider_errors.append(f"{provider}: {error}")
                continue

            normalized_results = self._normalize_search_results(
                provider_results,
                domain_filter=normalized_domain_filter,
                limit=limit,
            )

            if normalized_results:
                return ExecutionResult(
                    ok=True,
                    action_type="web_search",
                    data={
                        "provider": provider,
                        "query": normalized_query,
                        "results": normalized_results,
                    },
                )

            provider_errors.append(f"{provider}: нет подходящих результатов")

        if not provider_errors:
            raise ExecutorError("Не найден ни один доступный провайдер web_search")

        raise ExecutorError(
            "Не удалось выполнить web_search. " + " | ".join(provider_errors[-3:])
        )

    def _fetch_url(
        self,
        url: str,
        max_chars: int | None = None,
        extract_mode: str = "text",
    ) -> ExecutionResult:
        normalized_url = self._normalize_http_url(url)
        if not normalized_url:
            raise ExecutorError("Некорректный URL")

        parsed = urllib.parse.urlparse(normalized_url)
        host = (parsed.hostname or "").lower()
        if not self._is_allowed_domain(host):
            raise ExecutorError("URL не входит в whitelist доверенных доменов")

        path = parsed.path.lower()
        if any(path.endswith(suffix) for suffix in self.disallowed_fetch_suffixes):
            raise ExecutorError("Запрошенный URL указывает на бинарный или архивный файл")

        safe_max_chars = self.max_fetch_chars_default if max_chars is None else max_chars
        safe_max_chars = max(500, min(safe_max_chars, self.max_fetch_chars_limit))
        mode = extract_mode.lower().strip() if extract_mode else "text"
        if mode not in {"text", "markdown"}:
            mode = "text"

        _, headers, raw = self._http_request(
            url=normalized_url,
            method="GET",
            headers={"Accept": "text/html, text/plain, text/markdown, application/json;q=0.9"},
            timeout=self.web_timeout,
            max_bytes=self.max_web_response_bytes,
        )

        content_type = headers.get("Content-Type", "")
        disposition = headers.get("Content-Disposition", "").lower()
        if "attachment" in disposition:
            raise ExecutorError("Скачивание вложений запрещено")
        if not self._is_text_like_content_type(content_type):
            raise ExecutorError("Поддерживаются только text-подобные ответы")

        decoded = self._decode_body(raw, content_type)
        title = normalized_url
        content_text = decoded

        if self._is_html_content_type(content_type):
            extractor = _HTMLTextExtractor()
            extractor.feed(decoded)
            extracted_text = extractor.text.strip()
            if extracted_text:
                content_text = extracted_text
            if extractor.title:
                title = extractor.title

        normalized_text = self._normalize_web_text(content_text)
        if mode == "markdown":
            rendered = f"# {title}\n\n{normalized_text}" if title else normalized_text
        else:
            rendered = normalized_text

        truncated = len(rendered) > safe_max_chars
        output = rendered[:safe_max_chars] if truncated else rendered

        return ExecutionResult(
            ok=True,
            action_type="fetch_url",
            data={
                "url": normalized_url,
                "title": title,
                "content": output,
                "truncated": truncated,
            },
        )

    def _provider_order(self) -> list[str]:
        chain = ["searxng", "tavily", "brave"]
        selected = self.web_search_provider
        if selected in chain:
            return [selected] + [item for item in chain if item != selected]
        return chain

    def _provider_available(self, provider: str) -> bool:
        if provider == "searxng":
            parsed = urllib.parse.urlparse(self.searxng_base_url)
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        if provider == "tavily":
            return bool(self.tavily_api_key)
        if provider == "brave":
            return bool(self.brave_api_key)
        return False

    def _search_with_provider(
        self,
        provider: str,
        query: str,
        top_k: int,
        freshness: str,
    ) -> list[dict[str, str]]:
        if provider == "searxng":
            return self._search_searxng(query=query, top_k=top_k, freshness=freshness)
        if provider == "tavily":
            return self._search_tavily(query=query, top_k=top_k, freshness=freshness)
        if provider == "brave":
            return self._search_brave(query=query, top_k=top_k, freshness=freshness)
        raise ExecutorError(f"Неизвестный провайдер поиска: {provider}")

    def _search_searxng(self, query: str, top_k: int, freshness: str) -> list[dict[str, str]]:
        if not self.searxng_base_url:
            raise ExecutorError("SEARXNG_BASE_URL не задан")

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "safesearch": "1",
        }
        freshness_map = {
            "day": "day",
            "week": "week",
            "month": "month",
        }
        if freshness in freshness_map:
            params["time_range"] = freshness_map[freshness]

        endpoint = f"{self.searxng_base_url}/search?{urllib.parse.urlencode(params)}"
        payload = self._http_json_request(
            url=endpoint,
            method="GET",
            headers={"Accept": "application/json"},
            timeout=self.web_timeout,
        )

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ExecutorError("SearXNG вернул невалидный формат результатов")

        mapped: list[dict[str, str]] = []
        for item in raw_results[: max(top_k * 3, top_k)]:
            if not isinstance(item, dict):
                continue
            mapped.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": str(item.get("content", "")).strip(),
                }
            )
        return mapped

    def _search_tavily(self, query: str, top_k: int, freshness: str) -> list[dict[str, str]]:
        if not self.tavily_api_key:
            raise ExecutorError("TAVILY_API_KEY не задан")

        payload: dict[str, Any] = {
            "api_key": self.tavily_api_key,
            "query": query,
            "max_results": top_k,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        freshness_days = {
            "day": 1,
            "week": 7,
            "month": 30,
        }
        if freshness in freshness_days:
            payload["days"] = freshness_days[freshness]

        response = self._http_json_request(
            url="https://api.tavily.com/search",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=self.web_timeout,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise ExecutorError("Tavily вернул невалидный формат результатов")

        mapped: list[dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            mapped.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": str(item.get("content", "")).strip(),
                }
            )
        return mapped

    def _search_brave(self, query: str, top_k: int, freshness: str) -> list[dict[str, str]]:
        if not self.brave_api_key:
            raise ExecutorError("BRAVE_SEARCH_API_KEY не задан")

        params: dict[str, Any] = {
            "q": query,
            "count": top_k,
        }
        freshness_map = {
            "day": "pd",
            "week": "pw",
            "month": "pm",
        }
        if freshness in freshness_map:
            params["freshness"] = freshness_map[freshness]

        endpoint = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
        payload = self._http_json_request(
            url=endpoint,
            method="GET",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.brave_api_key,
            },
            timeout=self.web_timeout,
        )

        web_section = payload.get("web")
        if not isinstance(web_section, dict):
            raise ExecutorError("Brave вернул невалидный формат")

        raw_results = web_section.get("results")
        if not isinstance(raw_results, list):
            raise ExecutorError("Brave вернул невалидный список результатов")

        mapped: list[dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            mapped.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": str(item.get("description", "")).strip(),
                }
            )
        return mapped

    def _normalize_search_results(
        self,
        results: list[dict[str, str]],
        domain_filter: list[str] | None,
        limit: int,
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()

        for item in results:
            title = str(item.get("title", "")).strip()
            raw_url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()

            clean_url = self._normalize_http_url(raw_url)
            if not clean_url:
                continue

            parsed = urllib.parse.urlparse(clean_url)
            host = (parsed.hostname or "").lower()
            if not self._is_allowed_domain(host):
                continue

            if domain_filter and not self._matches_domain_filter(host, domain_filter):
                continue

            dedupe_key = clean_url.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            clean_title = self._normalize_web_text(title) or host
            clean_snippet = self._normalize_web_text(snippet)
            if clean_snippet:
                if len(clean_snippet) > self.max_web_snippet_chars:
                    clean_snippet = clean_snippet[: self.max_web_snippet_chars].rstrip() + "..."

            normalized.append(
                {
                    "title": clean_title,
                    "url": clean_url,
                    "snippet": clean_snippet,
                }
            )
            if len(normalized) >= limit:
                break

        return normalized

    def _normalize_domain_filter(self, domain_filter: list[str] | None) -> list[str] | None:
        if not domain_filter:
            return None
        normalized: list[str] = []
        for item in domain_filter:
            candidate = str(item).strip().lower()
            if not candidate:
                continue
            if "://" in candidate:
                parsed = urllib.parse.urlparse(candidate)
                candidate = parsed.hostname or ""
            else:
                candidate = candidate.split("/", 1)[0]
            if candidate.startswith("www."):
                candidate = candidate[4:]
            if candidate:
                normalized.append(candidate)
        return normalized or None

    def _matches_domain_filter(self, host: str, domains: list[str]) -> bool:
        for domain in domains:
            if host == domain or host.endswith(f".{domain}"):
                return True
        return False

    def _is_allowed_domain(self, host: str) -> bool:
        normalized_host = host.lower()
        for domain in self.allowed_web_domains:
            if normalized_host == domain or normalized_host.endswith(f".{domain}"):
                return True
        return False

    def _normalize_http_url(self, raw_url: str) -> str | None:
        try:
            parsed = urllib.parse.urlparse(raw_url.strip())
        except ValueError:
            return None

        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.netloc:
            return None

        clean_path = parsed.path or "/"
        normalized = urllib.parse.urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                clean_path,
                parsed.query,
                "",
            )
        )
        return normalized

    def _http_json_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        _, response_headers, raw = self._http_request(
            url=url,
            method=method,
            headers=headers,
            timeout=timeout,
            max_bytes=self.max_web_response_bytes,
            body=body,
        )

        content_type = response_headers.get("Content-Type", "")
        text = self._decode_body(raw, content_type)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ExecutorError("Провайдер вернул невалидный JSON") from error
        if not isinstance(parsed, dict):
            raise ExecutorError("Провайдер вернул JSON не в формате объекта")
        return parsed

    def _http_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        body: bytes | None = None,
    ) -> tuple[int, Any, bytes]:
        request_headers = {
            "User-Agent": "dagai-local-agent/1.0",
            "Accept-Encoding": "identity",
        }
        request_headers.update(headers)

        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_length_raw = response.headers.get("Content-Length")
                if content_length_raw:
                    try:
                        content_length = int(content_length_raw)
                    except ValueError:
                        content_length = 0
                    if content_length > max_bytes:
                        raise ExecutorError("Ответ сервера превышает допустимый размер")

                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ExecutorError("Ответ сервера превышает допустимый размер")

                return response.status, response.headers, raw
        except urllib.error.HTTPError as error:
            raise ExecutorError(f"HTTP {error.code} при запросе к {url}") from error
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            raise ExecutorError(f"Сетевая ошибка при запросе к {url}: {reason}") from error
        except TimeoutError as error:
            raise ExecutorError(f"Таймаут при запросе к {url}") from error

    def _is_text_like_content_type(self, content_type: str) -> bool:
        lowered = content_type.lower()
        if lowered.startswith("text/"):
            return True
        allowed = {
            "application/json",
            "application/xhtml+xml",
            "application/xml",
            "application/ld+json",
        }
        for item in allowed:
            if item in lowered:
                return True
        return False

    def _is_html_content_type(self, content_type: str) -> bool:
        lowered = content_type.lower()
        return "text/html" in lowered or "application/xhtml+xml" in lowered

    def _decode_body(self, raw: bytes, content_type: str) -> str:
        charset_match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type)
        encoding = charset_match.group(1) if charset_match else "utf-8"
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    def _normalize_web_text(self, text: str) -> str:
        compact = re.sub(r"\r\n?", "\n", text)
        compact = re.sub(r"\n{3,}", "\n\n", compact)
        compact = re.sub(r"[ \t]{2,}", " ", compact)
        return compact.strip()

    def _read_int_env(
        self,
        name: str,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(min_value, min(value, max_value))

    def _read_float_env(
        self,
        name: str,
        default: float,
        min_value: float,
        max_value: float,
    ) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return max(min_value, min(value, max_value))

    def _read_full_text(self, target: Path) -> str:
        """Читает файл целиком без truncation. Для операций записи/замены."""
        size = target.stat().st_size
        if size > self.max_writable_file_bytes:
            raise ExecutorError(
                f"Файл слишком большой для операции замены "
                f"({size} байт, лимит {self.max_writable_file_bytes})"
            )
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ExecutorError(f"Файл не является UTF-8 текстом: {target.name}") from error

    def _safe_read_text(self, target: Path) -> tuple[str, bool]:
        if target.stat().st_size > self.max_file_size_bytes:
            raw = target.read_text(encoding="utf-8", errors="replace")
            return self._truncate_text(raw, self.max_read_chars), True

        try:
            raw = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ExecutorError(f"Файл не является UTF-8 текстом: {target.name}") from error

        truncated = len(raw) > self.max_read_chars
        return self._truncate_text(raw, self.max_read_chars), truncated

    def _truncate_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n\n[TRUNCATED: показаны первые {limit} символов из {len(text)}]"

    def _iter_files(self, base_dir: Path):
        for root, dirnames, filenames in os.walk(base_dir):
            dirnames[:] = [name for name in dirnames if name not in self.ignored_dirs]
            root_path = Path(root)
            for filename in sorted(filenames):
                path = root_path / filename
                if path.is_file():
                    yield path
