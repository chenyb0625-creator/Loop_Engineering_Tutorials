"""Convert the original Loop Engineering DOCX chapters to GitHub Markdown.

The source documents use one-cell tables for code blocks and two-cell tables
for callouts. This converter restores those semantics instead of producing a
literal, hard-to-review Word-to-Markdown dump.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


CHAPTERS = {
    1: ("01-minimal-autonomous-loop.md", "不接入 AI，先跑通最小自治闭环"),
    2: ("02-python-project-and-git-baseline.md", "建立真实 Python 项目与 Git 基线"),
    3: ("03-deterministic-verifier.md", "构建确定性验证器与证据门"),
    4: ("04-bounded-controller.md", "外层控制器与有界调度"),
    5: ("05-codex-cli-integration.md", "接入 Codex CLI"),
    6: ("06-stagnation-detection.md", "停滞检测与失败签名"),
    7: ("07-protected-paths-and-diff-policy.md", "受保护路径与 Diff 策略"),
    8: ("08-state-log-and-recovery.md", "状态日志与可恢复执行"),
    9: ("09-independent-reviewer.md", "独立只读审查代理与双门终态"),
    10: ("10-context-engineering.md", "上下文工程与任务包"),
    11: ("11-git-worktree-and-parallel-agents.md", "Git Worktree 与并行代理"),
    12: ("12-failure-modes.md", "常见失败模式与反例"),
    13: ("13-scientific-evaluation.md", "如何科学评估 Loop Engineering"),
    14: ("14-research-evidence-governance.md", "科研任务迁移与证据治理"),
    15: ("15-seven-day-capstone.md", "七天训练路线与毕业项目"),
}

IMAGE_ALT_TEXT = {
    "success false done": "常规成功率与 false-DONE 对比图",
    "p90 wall time": "系统 P90 运行时长对比图",
    "evidence ladder": "科研证据阶梯",
    "research loop": "科研证据治理闭环",
    "seven day roadmap": "七天训练路线图",
    "capstone architecture": "毕业项目架构",
    "maturity staircase": "Loop Engineering 成熟度阶梯",
}


@dataclass
class ImageAsset:
    relative_path: str
    alt: str


def iter_blocks(document: DocumentObject) -> Iterable[Paragraph | Table]:
    """Yield paragraphs and tables in their original document order."""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def chapter_number(path: Path) -> int:
    match = re.search(r"第(\d{2})章", path.name)
    if not match:
        raise ValueError(f"无法从文件名识别章节号：{path.name}")
    return int(match.group(1))


def paragraph_style(paragraph: Paragraph) -> str:
    return paragraph.style.name if paragraph.style else ""


def plain_paragraph_text(paragraph: Paragraph) -> str:
    return paragraph.text.strip()


def escape_inline_code(text: str) -> str:
    if "`" not in text:
        return f"`{text}`"
    return f"`` {text} ``"


def normalize_inline_literals(text: str) -> str:
    text = re.sub(
        r"(?<![`A-Za-z0-9_.-])([A-Za-z0-9_.-]+\*+[A-Za-z0-9_.*-]+)(?![`A-Za-z0-9_.-])",
        r"`\1`",
        text,
    )
    text = text.replace("对 **、", "对 `**`、")
    return text


def rich_paragraph_text(paragraph: Paragraph) -> str:
    """Preserve lightweight run emphasis without reproducing Word styling."""
    pieces: list[str] = []
    for run in paragraph.runs:
        text = run.text
        if not text:
            continue
        properties = run._element.rPr
        font_name = run.font.name or ""
        if properties is not None:
            fonts = properties.find(qn("w:rFonts"))
            if fonts is not None:
                font_name = (
                    fonts.get(qn("w:ascii"))
                    or fonts.get(qn("w:eastAsia"))
                    or font_name
                )
        is_code = font_name.lower() in {"consolas", "courier new", "cascadia code"}
        if is_code and "\n" not in text and text.strip():
            rendered = escape_inline_code(text)
        elif run.bold and text.strip():
            rendered = f"**{text}**"
        elif run.italic and text.strip():
            rendered = f"*{text}*"
        else:
            rendered = text
        pieces.append(rendered)

    text = "".join(pieces).strip() or plain_paragraph_text(paragraph)
    text = re.sub(r"^\s*[•●]\s*", "- ", text)
    text = re.sub(r"^\s*[☐□]\s*", "- [ ] ", text)
    text = re.sub(r"^\s*[☑✓]\s*", "- [x] ", text)
    text = normalize_inline_literals(text)
    text = text.replace("每章一个独立 DOCX", "每章一份独立 Markdown")
    return text


def looks_like_code(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if "\n" in stripped:
        return True
    patterns = (
        r"^(python|py|git|codex|pytest|ruff|pip|mkdir|cd|code|notepad)\b",
        r"^(Get-|Set-|New-|Remove-|Copy-|Move-|Select-|Write-)",
        r"^\$[A-Za-z_][A-Za-z0-9_]*",
        r"^[\{\[]",
        r"^(from|import|def|class|if|for|while|try|with|return|print)\b",
        r"^[A-Za-z_][A-Za-z0-9_./-]*\s*=",
        r"^[A-Za-z0-9_.-]+/",
        r"^\.?[A-Za-z0-9_-]+\.(py|json|toml|md|yml|yaml|txt)\b",
    )
    return any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in patterns)


def detect_language(text: str) -> str:
    stripped = text.strip()
    if re.search(r"(^|\n)\s*(from\s+\S+\s+import|import\s+\S+|def\s+\w+\(|class\s+\w+)", stripped):
        return "python"
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            if re.search(r"^\[[\w.-]+\]\s*$", stripped, flags=re.MULTILINE):
                return "toml"
    if re.search(r"^\[[\w.-]+\]\s*$", stripped, flags=re.MULTILINE):
        return "toml"
    if re.search(
        r"(^|\n)\s*(\$|Get-|Set-|New-|Remove-|Copy-|Move-|Select-|Write-|"
        r"python\b|py\b|git\b|codex\b|pytest\b|ruff\b|pip\b|mkdir\b|cd\b|"
        r"code\b|notepad\b)",
        stripped,
        flags=re.IGNORECASE,
    ):
        return "powershell"
    if re.search(r"(^|\n)\s*(name:|on:|jobs:|steps:|services:)", stripped):
        return "yaml"
    if re.search(r"(^|\n)\s*(#\s|##\s|```|\[[^\]]+\]\([^)]+\))", stripped):
        return "markdown"
    if re.search(r"[┌┐└┘├┤┬┴┼│─]|^[.A-Za-z0-9_-]+/$", stripped, flags=re.MULTILINE):
        return "text"
    if re.search(r"(^|\n)\s*(SELECT|INSERT|UPDATE|CREATE TABLE)\b", stripped, flags=re.IGNORECASE):
        return "sql"
    return "text"


def fenced_code(text: str) -> str:
    language = detect_language(text)
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def quote_block(text: str) -> str:
    lines = text.strip().splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def clean_cell_text(cell) -> str:
    parts = [plain_paragraph_text(paragraph) for paragraph in cell.paragraphs]
    return "\n".join(part for part in parts if part).strip()


def escape_table_cell(text: str) -> str:
    text = normalize_inline_literals(text)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return "<br>".join(line.strip() for line in text.splitlines() if line.strip())


def render_table(table: Table) -> str:
    rows: list[list[str]] = []
    seen_merged: set[object] = set()
    for row in table.rows:
        rendered_row: list[str] = []
        for cell in row.cells:
            cell_key = cell._tc
            if cell_key in seen_merged:
                rendered_row.append("")
            else:
                rendered_row.append(clean_cell_text(cell))
                seen_merged.add(cell_key)
        rows.append(rendered_row)

    column_count = max((len(row) for row in rows), default=0)
    rows = [row + [""] * (column_count - len(row)) for row in rows]
    non_empty = [cell for row in rows for cell in row if cell.strip()]

    if len(rows) == 1 and column_count <= 2:
        text = "\n".join(non_empty).strip()
        return fenced_code(text) if looks_like_code(text) else quote_block(text)

    if column_count == 1:
        return "\n".join(f"- {text}" for text in non_empty)

    header = rows[0]
    if not any(cell.strip() for cell in header):
        header = [f"字段 {index + 1}" for index in range(column_count)]
    body = rows[1:] or [[""] * column_count]
    output = [
        "| " + " | ".join(escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    output.extend(
        "| " + " | ".join(escape_table_cell(cell) for cell in row) + " |"
        for row in body
    )
    return "\n".join(output)


def image_assets(
    element,
    document: DocumentObject,
    asset_dir: Path,
    chapter: int,
    cache: dict[str, ImageAsset],
) -> list[ImageAsset]:
    assets: list[ImageAsset] = []
    for blip in element.findall(".//" + qn("a:blip")):
        relation_id = blip.get(qn("r:embed"))
        if not relation_id:
            continue
        if relation_id in cache:
            assets.append(cache[relation_id])
            continue
        part = document.part.related_parts[relation_id]
        extension = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/svg+xml": ".svg",
            "image/x-emf": ".emf",
        }.get(part.content_type, Path(part.partname).suffix or ".bin")
        digest = hashlib.sha256(part.blob).hexdigest()[:10]
        filename = f"figure-{len(cache) + 1:02d}-{digest}{extension}"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / filename).write_bytes(part.blob)

        doc_properties = element.findall(".//" + qn("wp:docPr"))
        alt = ""
        if doc_properties:
            alt = (
                doc_properties[0].get("descr")
                or doc_properties[0].get("title")
                or doc_properties[0].get("name")
                or ""
            )
        if not alt or alt.lower().startswith(("picture ", "image ")):
            picture_properties = element.findall(".//" + qn("pic:cNvPr"))
            if picture_properties:
                picture_name = picture_properties[0].get("name") or ""
                alt = Path(picture_name).stem.replace("_", " ").strip()
                alt = IMAGE_ALT_TEXT.get(alt.lower(), alt)
        if not alt or alt.lower().startswith(("picture ", "image ")):
            alt = f"第 {chapter:02d} 章插图 {len(cache) + 1}"
        asset = ImageAsset(
            relative_path=f"../assets/ch{chapter:02d}/{filename}",
            alt=alt,
        )
        cache[relation_id] = asset
        assets.append(asset)
    return assets


def navigation(chapter: int) -> str:
    links = ["[返回课程主页](../../README.md)"]
    if chapter > 1:
        previous_file, _ = CHAPTERS[chapter - 1]
        links.append(f"[← 上一章](./{previous_file})")
    if chapter < len(CHAPTERS):
        next_file, _ = CHAPTERS[chapter + 1]
        links.append(f"[下一章 →](./{next_file})")
    return " · ".join(links)


def convert_document(source: Path, output_root: Path, asset_root: Path) -> Path:
    number = chapter_number(source)
    output_name, title = CHAPTERS[number]
    output_path = output_root / output_name
    document = Document(source)
    image_cache: dict[str, ImageAsset] = {}
    lines = [
        f"# 第 {number:02d} 章：{title}",
        "",
        navigation(number),
        "",
    ]

    reached_content = False
    skipping_manual_toc = False

    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            style = paragraph_style(block)
            text = rich_paragraph_text(block)
            level_match = re.fullmatch(r"Heading\s+(\d+)", style, flags=re.IGNORECASE)

            if not reached_content:
                if level_match:
                    reached_content = True
                else:
                    continue

            if level_match:
                source_level = int(level_match.group(1))
                plain = plain_paragraph_text(block)
                if plain == "本章目录":
                    skipping_manual_toc = True
                    continue
                if skipping_manual_toc and source_level == 1:
                    skipping_manual_toc = False
                if skipping_manual_toc:
                    continue
                if source_level >= 3 and re.match(r"^\d+[.．、]\s*", plain):
                    # Word used Heading 3 for standalone self-test questions.
                    # In Markdown they are direct children of the H2 test section.
                    markdown_level = 3
                else:
                    markdown_level = min(source_level + 1, 6)
                lines.extend([f"{'#' * markdown_level} {plain}", ""])
                continue

            if skipping_manual_toc:
                continue

            assets = image_assets(
                block._element,
                document,
                asset_root / f"ch{number:02d}",
                number,
                image_cache,
            )
            if assets:
                for asset in assets:
                    lines.extend([f"![{asset.alt}]({asset.relative_path})", ""])
            if not text:
                continue

            if style == "Caption":
                lines.extend([f"*{text}*", ""])
            else:
                lines.extend([text, ""])
        else:
            if reached_content and not skipping_manual_toc:
                assets = image_assets(
                    block._element,
                    document,
                    asset_root / f"ch{number:02d}",
                    number,
                    image_cache,
                )
                for asset in assets:
                    lines.extend([f"![{asset.alt}]({asset.relative_path})", ""])
                rendered = render_table(block)
                if rendered.strip():
                    lines.extend([rendered, ""])

    lines.extend(["---", "", navigation(number), ""])
    output_root.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="包含章节 DOCX 的目录")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/chapters"),
        help="Markdown 输出目录",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path("docs/assets"),
        help="图片资源输出目录",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="转换前清空输出目录（仅清理指定的 output/assets）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.clean:
        workspace = Path.cwd().resolve()
        for generated_file in args.output.glob("[0-9][0-9]-*.md"):
            if workspace in generated_file.resolve().parents:
                generated_file.unlink()
        for generated_asset_dir in args.assets.glob("ch[0-9][0-9]"):
            if (
                generated_asset_dir.is_dir()
                and workspace in generated_asset_dir.resolve().parents
            ):
                shutil.rmtree(generated_asset_dir)
    files = sorted(args.source.glob("*.docx"), key=chapter_number)
    if len(files) != len(CHAPTERS):
        raise SystemExit(f"预期 15 个章节文件，实际找到 {len(files)} 个")
    for source in files:
        output = convert_document(source, args.output, args.assets)
        print(f"{source.name} -> {output}")


if __name__ == "__main__":
    main()
