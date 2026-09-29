from __future__ import annotations

from kernel.deps import detect_dependency_versions


def test_detect_dependency_versions_from_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "OpenAI>=1.40.0\npython_dotenv==1.0.1 # comment\nunversioned\n",
        encoding="utf-8",
    )

    versions = detect_dependency_versions(tmp_path)

    assert versions["openai"] == ">=1.40.0"
    assert versions["python-dotenv"] == "==1.0.1"
    assert "unversioned" not in versions


def test_detect_dependency_versions_from_pyproject_and_locks(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["Rich>=14", "pytest == 8.0"]
[project.optional-dependencies]
dev = ["ruff~=0.8"]
[tool.poetry.dependencies]
python = "^3.13"
click = "^8"
""",
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text('[[package]]\nname = "httpx"\nversion = "0.28.1"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "anyio"\nversion = "4.0.0"\n', encoding="utf-8")

    versions = detect_dependency_versions(tmp_path)

    assert versions["rich"] == ">=14"
    assert versions["pytest"] == "==8.0"
    assert versions["ruff"] == "~=0.8"
    assert versions["click"] == "^8"
    assert versions["httpx"] == "==0.28.1"
    assert versions["anyio"] == "==4.0.0"


def test_detect_dependency_versions_ignores_malformed_files(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[bad", encoding="utf-8")
    assert detect_dependency_versions(tmp_path) == {}
