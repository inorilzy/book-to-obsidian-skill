"""book-split: 提取并按章节切分书籍，输出到 chapters/ + manifest.json。

用法:
    python scripts/book_split.py <book_path> -o <output_dir> --strategy auto
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click

from lib.extractor import auto_extract
from lib.splitter import split_chapters


# Windows/Obsidian 都不允许的字符 + 控制字符
_FS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FS_TRIM = re.compile(r"[\s.]+$")
_FS_RESERVED_WIN = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_XLINK_RE = re.compile(r"\{\{XLINK\|([0-9a-fA-F]+)\}\}")


def _safe_filename(title: str, fallback: str, max_len: int = 80) -> str:
    """把章节标题清理成合法文件名（不含扩展名）。"""
    name = _FS_INVALID.sub(" ", title or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = _FS_TRIM.sub("", name)
    if not name:
        return fallback
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    if name.upper() in _FS_RESERVED_WIN:
        name = f"_{name}"
    return name


def _resolve_xlinks(
    content: str,
    current_stem: str,
    spine_to_stem: dict[str, str],
) -> str:
    """把 extractor 留下的跨 xhtml 占位符替换为最终 wikilink。

    占位符格式：{{XLINK|<base64(target_full|anchor|heading|link_text)>}}
    """
    def _sub(m: re.Match) -> str:
        token = m.group(1)
        try:
            payload = bytes.fromhex(token).decode("utf-8")
        except Exception:
            return m.group(0)
        parts = payload.split("|", 3)
        if len(parts) != 4:
            return m.group(0)
        target_full, _anchor, heading, link_text = parts
        if not heading:
            return link_text or ""
        target_stem = spine_to_stem.get(target_full)
        # 找不到目标章节 → 至少保留可读文本，避免破链
        if not target_stem:
            return link_text or heading
        if target_stem == current_stem:
            base = f"[[#{heading}]]"
        else:
            base = f"[[{target_stem}#{heading}]]"
        if link_text and link_text != heading:
            base = base[:-2] + f"|{link_text}]]"
        return base

    return _XLINK_RE.sub(_sub, content)


@click.command()
@click.argument("book_path", type=click.Path(exists=True))
@click.option("-o", "--output-dir", required=True, type=click.Path())
@click.option(
    "--strategy",
    type=click.Choice(["auto", "heading", "page_count"]),
    default="auto",
)
@click.option("--pages-per-chapter", default=15, type=int)
@click.option("--min-chars", default=500, type=int)
@click.option(
    "--filename-style",
    type=click.Choice(["title", "numbered", "both"]),
    default="both",
    help="title=纯标题；numbered=ch_001 序号；both=01 - 标题（默认）",
)
def main(
    book_path: str,
    output_dir: str,
    strategy: str,
    pages_per_chapter: int,
    min_chars: int,
    filename_style: str,
) -> None:
    """输出结构:
    output_dir/
      manifest.json
      chapters/
        ch_001.md ...
    """
    path = Path(book_path)
    out = Path(output_dir)
    chapters_dir = out / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    book = auto_extract(path)
    chapters = split_chapters(
        book,
        strategy=strategy,
        pages_per_chapter=pages_per_chapter,
        min_chapter_chars=min_chars,
    )

    # 保存提取的图片到 images/ 目录
    images_dir = out / "images"
    saved_image_count = 0
    if book.images:
        images_dir.mkdir(parents=True, exist_ok=True)
        for img in book.images:
            dest = images_dir / img.filename
            # 如果同名文件已存在则跳过（避免覆盖）
            if not dest.exists():
                dest.write_bytes(img.data)
            saved_image_count += 1  # 统计所有图片（含已存在的）

    # 第一遍：决定每章的 filename stem
    stems: list[str] = []
    used_names: set[str] = set()
    for ch in chapters:
        seq = f"{ch.chapter_number:02d}"
        title_safe = _safe_filename(ch.title, fallback=f"chapter-{seq}")
        if filename_style == "numbered":
            stem = f"ch_{ch.chapter_number:03d}"
        elif filename_style == "title":
            stem = title_safe
        else:
            stem = f"{seq} - {title_safe}"
        candidate = stem
        suffix = 2
        while candidate in used_names:
            candidate = f"{stem} ({suffix})"
            suffix += 1
        used_names.add(candidate)
        stems.append(candidate)

    # 建立 EPUB spine 文件 → 章节 stem 映射，用于跨章 wikilink
    spine_to_stem: dict[str, str] = {}
    if book.native_chapters and len(book.native_chapters) == len(stems):
        for nc, stem in zip(book.native_chapters, stems):
            for sf in nc.spine_files:
                spine_to_stem[sf] = stem

    # 第二遍：替换 XLINK 占位符并写文件
    file_entries: list[dict[str, object]] = []
    for ch, stem in zip(chapters, stems):
        resolved_content = _resolve_xlinks(ch.content, stem, spine_to_stem)
        filename = f"{stem}.md"
        target = chapters_dir / filename
        target.write_text(resolved_content, encoding="utf-8")
        file_entries.append({
            "filename": filename,
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "page_range": list(ch.page_range),
            "char_count": len(resolved_content),
        })

    manifest = {
        "title": book.title,
        "source": path.name,  # M3: 只记录文件名，不暴露本机绝对路径
        "metadata": book.metadata,
        "page_count": len(book.pages),
        "chapter_count": len(chapters),
        "strategy": strategy,
        "chapters": file_entries,
        "chapters_dir": str(chapters_dir),
        "images_dir": str(images_dir) if book.images else None,
        "image_count": saved_image_count,
    }

    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sys.stdout.write(json.dumps({
        "status": "ok",
        "manifest": str(manifest_path),
        "chapter_count": len(chapters),
        "image_count": saved_image_count,
        "title": book.title,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
