"""book-info: 查看书籍基本信息（不提取内容），输出 JSON 到 stdout。

用法:
    python scripts/book_info.py <book_path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本能 import 同级 lib/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click

from lib.extractor import is_scanned_pdf


@click.command()
@click.argument("book_path", type=click.Path(exists=True))
def main(book_path: str) -> None:
    path = Path(book_path)

    info: dict[str, object] = {
        "path": str(path),
        "filename": path.name,
        "format": path.suffix.lower().lstrip("."),
        "size_bytes": path.stat().st_size,
    }

    if path.suffix.lower() == ".pdf":
        info["is_scanned"] = is_scanned_pdf(path)
        info["recommended_engine"] = "marker" if info["is_scanned"] else "pymupdf"

    sys.stdout.write(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
