"""Validate Markdown structure, local links, and repository documentation."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".venv", "venv", "__pycache__"}
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+\S")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not IGNORED_PARTS.intersection(path.relative_to(ROOT).parts)
    )


def validate_fences(path: Path, lines: list[str]) -> list[str]:
    errors: list[str] = []
    open_fence: tuple[str, int, int] | None = None
    for line_number, line in enumerate(lines, start=1):
        match = FENCE_PATTERN.match(line)
        if not match:
            continue
        marker = match.group(1)
        if open_fence is None:
            open_fence = (marker[0], len(marker), line_number)
        elif marker[0] == open_fence[0] and len(marker) >= open_fence[1]:
            open_fence = None
    if open_fence:
        errors.append(
            f"{path.relative_to(ROOT)}:{open_fence[2]}: code fence is not closed"
        )
    return errors


def validate_headings(path: Path, lines: list[str]) -> list[str]:
    errors: list[str] = []
    previous_level = 0
    in_fence = False
    fence_char = ""
    fence_length = 0

    for line_number, line in enumerate(lines, start=1):
        fence = FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                in_fence = False
            continue
        if in_fence:
            continue

        heading = HEADING_PATTERN.match(line)
        if not heading:
            continue
        level = len(heading.group(1))
        if previous_level and level > previous_level + 1:
            errors.append(
                f"{path.relative_to(ROOT)}:{line_number}: "
                f"heading jumps from H{previous_level} to H{level}"
            )
        previous_level = level
    return errors


def validate_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for raw_target in LINK_PATTERN.findall(text):
        target = raw_target.strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = unquote(target.split("#", 1)[0])
        if not file_part:
            continue
        resolved = (path.parent / file_part).resolve()
        if resolved != ROOT and ROOT not in resolved.parents:
            errors.append(
                f"{path.relative_to(ROOT)}: local link escapes repository: {raw_target}"
            )
            continue
        if not resolved.exists():
            errors.append(
                f"{path.relative_to(ROOT)}: broken local link: {raw_target}"
            )
    return errors


def validate_file(path: Path) -> list[str]:
    data = path.read_bytes()
    errors: list[str] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path.relative_to(ROOT)}: not valid UTF-8: {exc}"]

    if not text.endswith("\n"):
        errors.append(f"{path.relative_to(ROOT)}: missing final newline")
    if "\ufffd" in text:
        errors.append(f"{path.relative_to(ROOT)}: contains replacement characters")

    lines = text.splitlines()
    errors.extend(validate_fences(path, lines))
    errors.extend(validate_headings(path, lines))
    errors.extend(validate_links(path, text))
    return errors


def validate_repository() -> list[str]:
    errors: list[str] = []
    files = markdown_files()
    if not files:
        return ["no Markdown files found"]

    for path in files:
        errors.extend(validate_file(path))

    chapter_files = sorted((ROOT / "docs" / "chapters").glob("[0-9][0-9]-*.md"))
    if len(chapter_files) != 15:
        errors.append(f"expected 15 reference chapters, found {len(chapter_files)}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chapter_index = (ROOT / "docs" / "chapters" / "README.md").read_text(
        encoding="utf-8"
    )
    for chapter in chapter_files:
        if chapter.name not in chapter_index:
            errors.append(
                f"docs/chapters/README.md does not link to {chapter.name}"
            )

    expected_course_lessons = {
        "00-quickstart.md",
        "01-mental-model.md",
        "02-acceptance-contract.md",
        "03-verifier.md",
        "04-controller.md",
        "05-policy-gates.md",
        "06-stagnation-and-recovery.md",
        "07-real-agent.md",
        "08-review-parallel-evaluation.md",
        "09-capstone-migration.md",
    }
    course_dir = ROOT / "docs" / "course"
    course_lessons = {path.name for path in course_dir.glob("[0-9][0-9]-*.md")}
    missing_lessons = sorted(expected_course_lessons - course_lessons)
    unexpected_lessons = sorted(course_lessons - expected_course_lessons)
    if missing_lessons:
        errors.append(f"missing course lessons: {', '.join(missing_lessons)}")
    if unexpected_lessons:
        errors.append(f"unexpected course lessons: {', '.join(unexpected_lessons)}")
    if "docs/course/README.md" not in readme:
        errors.append("README.md does not link to docs/course/README.md")

    course_index = (course_dir / "README.md").read_text(encoding="utf-8")
    for lesson in sorted(expected_course_lessons):
        if lesson not in course_index:
            errors.append(f"docs/course/README.md does not link to {lesson}")

    published_docx = [
        path
        for path in ROOT.rglob("*.docx")
        if "sources" not in path.relative_to(ROOT).parts
    ]
    for path in published_docx:
        errors.append(
            f"{path.relative_to(ROOT)}: DOCX must be moved under sources/word"
        )
    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        print("Documentation checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Documentation checks passed ({len(markdown_files())} Markdown files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
