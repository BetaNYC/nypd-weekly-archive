"""The What's changed page: WHATS-CHANGED.md is valid, the page is built from it, every page
links to it, and its markup keeps the accessibility rules of BetaNYC's What's changed standard
(one h1, an article per entry with a <time>, the filter fieldset and its live region)."""

import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCRIPT = ROOT / "scripts" / "whats_changed.py"
RENDER = [
    "render", "WHATS-CHANGED.md", "--format", "fragment", "--no-h1", "--site-prefix", "docs/",
    "--repo-url", "https://github.com/BetaNYC/nypd-weekly-archive", "--into", "docs/whats-changed.html",
]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          check=False)


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def tags(path: Path) -> list[tuple[str, dict[str, str | None]]]:
    parser = Tags()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.tags


def test_whats_changed_file_passes_check():
    result = run("check", "WHATS-CHANGED.md", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_page_is_built_from_the_file():
    result = run(*RENDER, "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_page_links_to_whats_changed_in_its_footer():
    pages = sorted(DOCS.glob("*.html"))
    assert len(pages) >= 10
    for page in pages:
        text = page.read_text(encoding="utf-8")
        footer = text[text.index('<footer class="site-footer">'):]
        assert 'href="whats-changed.html"' in footer, page.name


def test_page_structure():
    found = tags(DOCS / "whats-changed.html")
    names = [t for t, _ in found]
    assert names.count("h1") == 1
    for landmark in ("header", "nav", "main", "footer"):
        assert landmark in names, landmark
    assert any(a.get("class") == "skip" for t, a in found if t == "a")
    articles = [a for t, a in found if t == "article"]
    assert articles, "no entries rendered"
    assert all(a.get("aria-labelledby") for a in articles)
    assert names.count("time") >= len(articles)
    assert "fieldset" in names and "legend" in names
    assert any(a.get("role") == "status" and a.get("aria-live") == "polite" for _, a in found)
    current = [a for t, a in found if t == "a" and a.get("aria-current") == "page"]
    assert [a.get("href") for a in current] == ["whats-changed.html"]
