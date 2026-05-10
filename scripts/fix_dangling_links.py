#!/usr/bin/env python3
"""Scan Obsidian vault wikilinks and create stub notes for missing targets."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import click


WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
INVALID_WINDOWS_CHARS = '<>:"/\\|?*'
NON_NOTE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "pdf",
    "mp3",
    "wav",
    "mp4",
    "avi",
    "mov",
    "csv",
    "xlsx",
    "zip",
}


def iter_markdown_files(vault_root: Path) -> list[Path]:
    return [
        path
        for path in vault_root.rglob("*.md")
        if ".obsidian" not in path.parts
    ]


def parse_wikilink_target(raw_target: str) -> str:
    # [[target|alias]] or [[target#heading|alias]]
    target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    return target


def is_concept_candidate_target(target: str) -> bool:
    lower = target.casefold()
    if not lower:
        return False
    if lower.startswith(("http://", "https://", "obsidian://")):
        return False
    # This fixer only backfills concept-like note links (flat names).
    if "/" in target or "\\" in target:
        return False
    if target.endswith("/"):
        return False
    leaf = target.strip()
    if "." in leaf:
        ext = leaf.rsplit(".", 1)[-1].casefold()
        if ext in NON_NOTE_EXTENSIONS:
            return False
    return True


def sanitize_filename(name: str) -> str:
    out = "".join("_" if ch in INVALID_WINDOWS_CHARS else ch for ch in name)
    out = out.strip().rstrip(".")
    return out or "untitled"


def _yaml_scalar(value: str) -> str:
    """H3: 将字符串转义为安全的 YAML 单行标量字符串。

    移除换行符，用双引号包裹，防止外部内容注入 YAML frontmatter。
    """
    safe = value.replace("\r", "").replace("\n", " ").replace('"', '\\"')
    return f'"{safe}"'


def find_missing_targets(vault_root: Path) -> tuple[list[str], set[str]]:
    markdown_files = iter_markdown_files(vault_root)
    existing_stems = {file.stem.casefold() for file in markdown_files}
    linked_targets: set[str] = set()

    for path in markdown_files:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for raw in WIKILINK_PATTERN.findall(content):
            target = parse_wikilink_target(raw)
            if target and is_concept_candidate_target(target):
                linked_targets.add(target)

    missing = sorted(
        target
        for target in linked_targets
        if target.casefold() not in existing_stems
    )
    return missing, linked_targets


def build_stub_content(title: str, original_target: str) -> str:
    today = dt.date.today().isoformat()
    # H3: 用 _yaml_scalar 转义，防止 wikilink 内容注入 YAML frontmatter
    return (
        "---\n"
        f"title: {_yaml_scalar(title)}\n"
        f"original_target: {_yaml_scalar(original_target)}\n"
        "type: concept_stub\n"
        "tags: [concept, stub]\n"
        f"created: {today}\n"
        "---\n\n"
        f"# {title.replace(chr(10), ' ')}\n\n"
        "## 状态\n\n"
        "占位页：该概念由章节双链引用，但尚未生成完整概念笔记。\n\n"
        "## 后续\n\n"
        "- 在下一轮概念抽取中补全定义、关键性质与相关概念。\n"
    )


def create_stubs(
    vault_root: Path,
    missing_targets: list[str],
    dry_run: bool,
    max_stubs: int = 500,  # L1: 限制最大创建数量，防止磁盘耗尽
) -> int:
    concepts_dir = vault_root / "concepts"
    if not dry_run:
        concepts_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    for target in missing_targets:
        if created >= max_stubs:  # L1
            click.echo(
                f"[warn] 已达到 --max-stubs 限制 ({max_stubs})，剩余占位页未创建。",
                err=True,
            )
            break
        file_name = sanitize_filename(target) + ".md"
        file_path = concepts_dir / file_name
        if file_path.exists():
            continue
        if dry_run:
            click.echo(f"[dry-run] would create: {file_path}")
            created += 1
            continue
        file_path.write_text(build_stub_content(target, target), encoding="utf-8")
        click.echo(f"created: {file_path}")
        created += 1
    return created


@click.command()
@click.argument(
    "vault_path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option("--dry-run", is_flag=True, help="Only print what would be created")
@click.option(
    "--max-stubs",
    default=500,
    show_default=True,
    type=click.IntRange(1, 10000),
    help="L1: 单次运行最多创建的占位页数量，防止磁盘耗尽。",
)
def main(vault_path: Path, dry_run: bool, max_stubs: int) -> None:
    """Fix dangling Obsidian wikilinks by creating concept stubs."""
    vault_root = vault_path.expanduser().resolve()

    missing_targets, linked_targets = find_missing_targets(vault_root)
    created_count = create_stubs(vault_root, missing_targets, dry_run, max_stubs=max_stubs)

    click.echo(
        "summary: "
        + str(
            {
                "vault": str(vault_root),
                "wikilink_targets": len(linked_targets),
                "missing_targets": len(missing_targets),
                "created_stubs": created_count,
                "dry_run": dry_run,
            }
        )
    )


if __name__ == "__main__":
    main()
