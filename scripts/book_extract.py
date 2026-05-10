"""book-extract: 从书籍提取原始文本，输出到指定目录 + manifest.json。

用法:
    python scripts/book_extract.py <book_path> -o <output_dir> --mode by-chunk --chunk-size 8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click

from lib.extractor import auto_extract


@click.command()
@click.argument("book_path", type=click.Path(exists=True))
@click.option("-o", "--output-dir", required=True, type=click.Path())
@click.option(
    "--mode",
    type=click.Choice(["full", "by-page", "by-chunk"]),
    default="by-chunk",
)
@click.option("--chunk-size", default=8000, type=int)
def main(book_path: str, output_dir: str, mode: str, chunk_size: int) -> None:
    """输出结构:
    output_dir/
      manifest.json
      raw/
        chunk_001.md ...
    """
    path = Path(book_path)
    out = Path(output_dir)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    book = auto_extract(path)
    file_entries: list[dict[str, object]] = []

    if mode == "full":
        if not book.pages:  # C2: 防御空书籍导致 IndexError
            click.echo(json.dumps({"status": "error", "reason": "no pages extracted from file"}, ensure_ascii=False))
            return
        full_text = "\n\n".join(p.text for p in book.pages)
        target = raw_dir / "full.md"
        target.write_text(full_text, encoding="utf-8")
        file_entries.append({
            "filename": "full.md",
            "page_range": [book.pages[0].page_number, book.pages[-1].page_number],
            "char_count": len(full_text),
        })
    elif mode == "by-page":
        for page in book.pages:
            target = raw_dir / f"page_{page.page_number:04d}.md"
            target.write_text(page.text, encoding="utf-8")
            file_entries.append({
                "filename": target.name,
                "page_range": [page.page_number, page.page_number],
                "char_count": len(page.text),
                "detected_headings": page.detected_headings,
            })
    else:  # by-chunk
        chunks: list[tuple[int, int, list[str]]] = []
        current_text: list[str] = []
        current_start = book.pages[0].page_number if book.pages else 1
        current_end = current_start

        for page in book.pages:
            current_text.append(page.text)
            current_end = page.page_number

            if sum(len(t) for t in current_text) >= chunk_size:
                chunks.append((current_start, current_end, current_text))
                if page is not book.pages[-1]:
                    current_start = page.page_number + 1
                current_text = []

        if current_text:
            chunks.append((current_start, current_end, current_text))

        for idx, (start, end, texts) in enumerate(chunks, 1):
            content = "\n\n".join(texts)
            target = raw_dir / f"chunk_{idx:03d}.md"
            target.write_text(content, encoding="utf-8")
            file_entries.append({
                "filename": target.name,
                "page_range": [start, end],
                "char_count": len(content),
            })

    manifest = {
        "title": book.title,
        "source": path.name,  # M3: 只记录文件名，不暴露本机绝对路径
        "metadata": book.metadata,
        "page_count": len(book.pages),
        "mode": mode,
        "files": file_entries,
        "raw_dir": str(raw_dir),
    }

    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sys.stdout.write(json.dumps({
        "status": "ok",
        "manifest": str(manifest_path),
        "file_count": len(file_entries),
        "title": book.title,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
