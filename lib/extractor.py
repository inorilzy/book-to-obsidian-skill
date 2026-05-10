"""文本提取模块 - 支持多种书籍格式"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fitz  # pymupdf — only needed for type checking, not at runtime

logger = logging.getLogger(__name__)

# --- 路径安全工具 ---
_ALLOWED_IMG_EXTS = frozenset(
    {"png", "jpg", "jpeg", "jp2", "bmp", "tiff", "tif", "webp", "gif"}
)

# --- M1: 资源限制 --- 防止恶意/超大 PDF/EPUB 造成 OOM 或磁盘耗尽 ---
_MAX_PAGES = 5000   # 超过此页数拒绝处理
_MAX_IMAGES = 2000  # 单本书最多提取图片数量
_UNSAFE_CHARS = re.compile(r"[^\w\-. ()]")  # 只允许字母数字、连字符、点、空格、括号

# --- CJK 中英文自动补全空格（pangu 风格）---
_CJK_BLOCK = (
    r"[\u2e80-\u2eff\u2f00-\u2fdf\u3040-\u309f\u30a0-\u30ff"
    r"\u3100-\u312f\u3200-\u32ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\ufe30-\ufe4f]"
)
_CJK_AND_HALF = re.compile(rf"({_CJK_BLOCK})([A-Za-z0-9])")
_HALF_AND_CJK = re.compile(rf"([A-Za-z0-9])({_CJK_BLOCK})")


def _apply_cjk_autocorrect(text: str) -> str:
    """在 CJK 字符与半角英文 / 数字之间插入空格（仅处理普通文本行，跳过代码块）。

    例: ``'Python代码示例'`` → ``'Python 代码示例'``
        ``'第1章introduction'`` → ``'第 1 章 introduction'``
    """
    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue
        line = _CJK_AND_HALF.sub(r"\1 \2", line)
        line = _HALF_AND_CJK.sub(r"\1 \2", line)
        result.append(line)
    return "\n".join(result)


def _sanitize_anchor(text: str) -> str:
    """清理 Obsidian wikilink 锚点中不能用的字符。

    - `/` `\\` 在 wikilink 中被当作路径分隔符，替换为全角 `／`
    - `[` `]` `|` `#` `^` 会破坏 wikilink 语法，替换为安全字符
    - 锚点和正文 heading 必须用同一规则处理，否则跳转匹配失败
    """
    if not text:
        return text
    text = text.replace("/", "／").replace("\\", "＼")
    text = text.replace("[", "(").replace("]", ")")
    text = text.replace("|", "丨").replace("#", "＃")
    text = text.replace("^", "＾")
    return text


def _sanitize_image_filename(name: str, fallback_idx: int) -> str:
    """将来自 PDF/EPUB/Marker 的图片文件名净化为安全的本地文件名。

    - 仅保留最后的文件名分量（Path.name），防止目录遍历
    - 扩展名白名单过滤
    - 替换所有非安全字符为 '_'
    - 确保结果非空
    """
    # 1. 剥离路径前缀，只保留文件名部分
    basename = Path(name).name
    # 2. 分离主干与扩展名
    stem, _, suffix = basename.rpartition(".")
    ext = suffix.lower() if suffix else "png"
    if ext not in _ALLOWED_IMG_EXTS:
        ext = "png"
    # 3. 净化主干
    safe_stem = _UNSAFE_CHARS.sub("_", stem).strip("_. ") if stem else ""
    if not safe_stem:
        safe_stem = f"img_{fallback_idx:03d}"
    return f"{safe_stem}.{ext}"


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    # 页面中检测到的标题（如果有）
    detected_headings: list[str]


@dataclass(frozen=True)
class ExtractedImage:
    """从书籍中提取的图片"""
    filename: str      # 目标文件名，例如 "img_001.png"
    data: bytes        # 原始图片字节
    media_type: str    # 例如 "image/png"
    caption: str = ""  # 图片说明（alt text 或 figure caption）


@dataclass(frozen=True)
class NativeChapter:
    """书籍自带的章节结构（来自 EPUB TOC、PDF outline 等），优先级高于正则切分。"""
    chapter_number: int
    title: str
    content: str           # 已转好的 Markdown
    page_range: tuple[int, int]
    spine_files: tuple[str, ...] = ()  # 该章节包含的 EPUB spine xhtml file_name 列表


@dataclass(frozen=True)
class ExtractedBook:
    title: str
    source_path: str
    pages: list[ExtractedPage]
    metadata: dict[str, str]
    images: list[ExtractedImage] = field(default_factory=list)
    # 如果书籍格式自带可靠的章节结构（例如 EPUB 的 TOC），放在这里。
    # splitter 会优先使用，绕过基于正则的章节切分。
    native_chapters: list[NativeChapter] = field(default_factory=list)


class Extractor(ABC):
    """文本提取器基类"""

    @abstractmethod
    def extract(self, file_path: Path) -> ExtractedBook:
        ...

    @abstractmethod
    def supports(self, file_path: Path) -> bool:
        ...


def _render_md_table(rows: list[list[str | None]]) -> str:
    """把 pymupdf find_tables().extract() 的二维数据转成 markdown table。"""
    cleaned: list[list[str]] = []
    for row in rows:
        cleaned.append([
            (cell or "").replace("\n", " ").replace("|", "\\|").strip()
            for cell in row
        ])
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    if all(not c for c in cleaned[0]):
        cleaned = cleaned[1:]
    if not cleaned:
        return ""
    header = cleaned[0]
    body = cleaned[1:] if len(cleaned) > 1 else []
    sep = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


class PDFTextExtractor(Extractor):
    """从文字型 PDF 提取结构化 Markdown。

    流程（每页）：
    1. ``page.get_text("dict")`` 拿到所有文本 block（含 bbox、字号、字体）
    2. ``page.get_images(full=True)`` + ``page.get_image_bbox`` 拿到图片 bbox
    3. 全文统计字号 → 中位数当 body_size，按比例判 h1/h2/h3
    4. 按 y 坐标合并 (text_block, image_block) 序列，输出 Markdown
       同 block 内的多行用空格连接为段落；段落之间用空行分隔；
       图片输出 ``![](../images/img_NNN.ext)`` 占位
    5. 若 PDF outline 非空 → 按 outline 切分 NativeChapter，
       否则只填充 ``ExtractedPage``，由 splitter 走正则切分
    """

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def extract(self, file_path: Path) -> ExtractedBook:
        import fitz  # pymupdf

        extracted_images: list[ExtractedImage] = []
        seen_xrefs: dict[int, str] = {}  # xref → 已分配的 filename
        img_idx = 1
        # page_num(1-based) → markdown 文本
        page_markdown: dict[int, str] = {}
        # page_num → 检测到的所有标题文本（保留以兼容旧字段）
        page_headings: dict[int, list[str]] = {}

        with fitz.open(str(file_path)) as doc:
            if len(doc) > _MAX_PAGES:
                raise ValueError(
                    f"PDF 页数 ({len(doc)}) 超过上限 {_MAX_PAGES}。"
                    "如需处理超大书籍，请调整 extractor._MAX_PAGES。"
                )

            total_pages = len(doc)

            # --- pass 1: 收集字号样本 + 候选页眉/页脚文本 ---
            size_samples: list[float] = []
            sample_limit = min(total_pages, 30)
            # (page_num, text_normalized) — 在页面顶/底 60pt 内的短行
            header_counter: dict[str, int] = {}
            footer_counter: dict[str, int] = {}
            for page_num in range(sample_limit):
                page = doc[page_num]
                page_h = page.rect.height
                pdict = page.get_text("dict")
                for block in pdict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    bbox = block.get("bbox", [0, 0, 0, 0])
                    y_top, y_bot = bbox[1], bbox[3]
                    txt_parts: list[str] = []
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            sz = span.get("size", 0)
                            if sz > 0:
                                size_samples.append(sz)
                            t = span.get("text", "")
                            if t:
                                txt_parts.append(t)
                    block_text = "".join(txt_parts).strip()
                    if not block_text or len(block_text) > 60:
                        continue
                    # 归一化：去掉所有数字（页码会变）
                    norm = re.sub(r"\d+", "#", block_text)
                    if y_top < 60:
                        header_counter[norm] = header_counter.get(norm, 0) + 1
                    elif y_bot > page_h - 60:
                        footer_counter[norm] = footer_counter.get(norm, 0) + 1
            body_size = self._median(size_samples) if size_samples else 12.0

            # 命中阈值：在 30 页样本中至少出现 5 次的归一化文本判为页眉/页脚
            threshold = max(3, sample_limit // 4)
            header_blacklist = {
                k for k, v in header_counter.items() if v >= threshold
            }
            footer_blacklist = {
                k for k, v in footer_counter.items() if v >= threshold
            }

            # --- pass 2: 逐页生成 Markdown ---
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_h = page.rect.height
                pdict = page.get_text("dict")

                # 收集表格块（pymupdf 1.23+ 提供 find_tables）
                table_blocks: list[tuple[float, str]] = []
                table_rects: list[tuple[float, float, float, float]] = []
                try:
                    tables = page.find_tables()
                    for tbl in tables:
                        rows = tbl.extract()
                        if not rows or not any(any(c for c in r) for r in rows):
                            continue
                        bbox = tbl.bbox  # (x0, y0, x1, y1)
                        table_rects.append(tuple(bbox))
                        md_table = _render_md_table(rows)
                        if md_table:
                            table_blocks.append((bbox[1], md_table))
                except Exception:
                    pass

                def _in_table(bx_top: float, bx_bot: float) -> bool:
                    for (_x0, y0, _x1, y1) in table_rects:
                        if bx_top >= y0 - 2 and bx_bot <= y1 + 2:
                            return True
                    return False

                # 收集图片块
                image_blocks: list[tuple[float, str]] = []  # (y_top, markdown)
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    if xref in seen_xrefs:
                        # 同图片在多页重复 → 引用已存在 filename
                        try:
                            bbox = page.get_image_bbox(img_info)
                            image_blocks.append(
                                (bbox.y0, f"![](../images/{seen_xrefs[xref]})")
                            )
                        except Exception:
                            pass
                        continue
                    if len(extracted_images) >= _MAX_IMAGES:
                        break
                    try:
                        img_data = doc.extract_image(xref)
                    except Exception:
                        continue
                    data = img_data.get("image", b"")
                    raw_ext = (img_data.get("ext", "png") or "png").lower()
                    w = img_data.get("width", 0)
                    h = img_data.get("height", 0)
                    if len(data) < 500 or w < 50 or h < 50:
                        continue
                    ext = raw_ext if raw_ext in _ALLOWED_IMG_EXTS else "png"
                    filename = f"img_{img_idx:03d}.{ext}"
                    extracted_images.append(ExtractedImage(
                        filename=filename,
                        data=data,
                        media_type=f"image/{ext}",
                    ))
                    seen_xrefs[xref] = filename
                    img_idx += 1
                    try:
                        bbox = page.get_image_bbox(img_info)
                        y_top = bbox.y0
                    except Exception:
                        y_top = 0.0
                    image_blocks.append((y_top, f"![](../images/{filename})"))

                # 收集文本块
                text_blocks: list[tuple[float, str]] = []
                headings_on_page: list[str] = []
                for block in pdict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    bbox = block.get("bbox", [0, 0, 0, 0])
                    y_top = bbox[1]
                    y_bot = bbox[3]
                    # 跳过落在表格区域内的 block（避免与 markdown table 重复）
                    if _in_table(y_top, y_bot):
                        continue
                    # 页眉/页脚剔除
                    if y_top < 60 or y_bot > page_h - 60:
                        # 取 block 文本归一化后比对黑名单
                        snippet = "".join(
                            span.get("text", "")
                            for line in block.get("lines", [])
                            for span in line.get("spans", [])
                        ).strip()
                        if snippet and len(snippet) <= 60:
                            norm = re.sub(r"\d+", "#", snippet)
                            if y_top < 60 and norm in header_blacklist:
                                continue
                            if y_bot > page_h - 60 and norm in footer_blacklist:
                                continue
                            # 纯页码（如 "第 4 页"）也直接丢
                            if re.fullmatch(r"[第页\sPage\d.]+", snippet):
                                continue
                    rendered, heading = self._render_text_block(block, body_size)
                    if not rendered:
                        continue
                    text_blocks.append((y_top, rendered))
                    if heading:
                        headings_on_page.append(heading)

                # 合并相邻的代码块（PDF 经常把每行算一个 block）
                # 同时把短小的 javadoc 注释段落（如 `* xxx*/`）并入相邻代码
                text_blocks.sort(key=lambda x: x[0])
                merged_text_blocks: list[tuple[float, str]] = []

                def _is_fenced(s: str) -> bool:
                    return s.startswith("```") and s.endswith("```")

                def _looks_like_comment_line(s: str) -> bool:
                    # 形如 "* @author xxx" / "* xxx*/" / "//xxx" 的单行注释
                    if not s or "\n" in s or len(s) > 200:
                        return False
                    return bool(re.match(r"^\s*(\*|//|/\*)", s))

                for y, md in text_blocks:
                    if (
                        merged_text_blocks
                        and _is_fenced(merged_text_blocks[-1][1])
                        and _is_fenced(md)
                    ):
                        prev_y, prev_md = merged_text_blocks[-1]
                        prev_body = prev_md[3:-3].strip("\n")
                        cur_body = md[3:-3].strip("\n")
                        merged_text_blocks[-1] = (
                            prev_y, f"```\n{prev_body}\n{cur_body}\n```"
                        )
                    elif (
                        merged_text_blocks
                        and _is_fenced(merged_text_blocks[-1][1])
                        and _looks_like_comment_line(md)
                    ):
                        # 把孤立注释行追加到代码块
                        prev_y, prev_md = merged_text_blocks[-1]
                        prev_body = prev_md[3:-3].strip("\n")
                        merged_text_blocks[-1] = (
                            prev_y, f"```\n{prev_body}\n{md}\n```"
                        )
                    else:
                        merged_text_blocks.append((y, md))
                text_blocks = merged_text_blocks

                # 合并并按 y 坐标排序（图片插入到段落间）
                merged = sorted(text_blocks + image_blocks + table_blocks, key=lambda x: x[0])
                page_md = "\n\n".join(item[1] for item in merged).strip()
                page_markdown[page_num + 1] = page_md
                page_headings[page_num + 1] = headings_on_page

            # --- 构造 ExtractedPage 列表（兼容旧 splitter 路径） ---
            pages: list[ExtractedPage] = [
                ExtractedPage(
                    page_number=pn,
                    text=page_markdown.get(pn, ""),
                    detected_headings=page_headings.get(pn, []),
                )
                for pn in range(1, len(doc) + 1)
            ]

            # --- 构造 NativeChapter（基于 PDF outline） ---
            native_chapters: list[NativeChapter] = []
            try:
                toc = doc.get_toc(simple=True) or []
            except Exception:
                toc = []
            # 过滤 level > 1 的子条目，只用顶层章节切分
            top_toc = [(lvl, title, pg) for lvl, title, pg in toc if lvl == 1]
            if top_toc:
                total_pages = len(doc)
                for i, (_lvl, title, start_page) in enumerate(top_toc):
                    end_page = (
                        top_toc[i + 1][2] - 1 if i + 1 < len(top_toc) else total_pages
                    )
                    start_page = max(1, min(start_page, total_pages))
                    end_page = max(start_page, min(end_page, total_pages))
                    chunks = [
                        page_markdown.get(p, "")
                        for p in range(start_page, end_page + 1)
                    ]
                    body = "\n\n".join(c for c in chunks if c).strip()
                    if not body:
                        continue
                    body = self._merge_cross_page_code(body)
                    # 章节首部加 H1 标题
                    safe_title = _sanitize_anchor(title.strip())
                    content = f"# {safe_title}\n\n{body}"
                    native_chapters.append(NativeChapter(
                        chapter_number=len(native_chapters) + 1,
                        title=title.strip(),
                        content=content,
                        page_range=(start_page, end_page),
                    ))

            metadata = doc.metadata or {}
            title = metadata.get("title") or file_path.stem

        return ExtractedBook(
            title=title,
            source_path=str(file_path),
            pages=pages,
            metadata={k: str(v) for k, v in metadata.items() if v},
            images=extracted_images,
            native_chapters=native_chapters,
        )

    @staticmethod
    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2

    @staticmethod
    def _merge_cross_page_code(body: str) -> str:
        """章节级合并：把被空段或孤立注释行隔开的相邻 fenced code 块合并。

        markdown 段落以空行分隔；这里按段落数组扫描，遇到模式
        ``[code, comment_line, code]`` 或 ``[code, code]`` 时合并。
        """
        paragraphs = re.split(r"\n{2,}", body)

        def _is_fenced(p: str) -> bool:
            return p.startswith("```") and p.rstrip().endswith("```")

        def _looks_like_comment_line(p: str) -> bool:
            if not p or "\n" in p or len(p) > 200:
                return False
            return bool(re.match(r"^\s*(\*|//|/\*)", p))

        out: list[str] = []
        i = 0
        while i < len(paragraphs):
            cur = paragraphs[i]
            if _is_fenced(cur) and out and _is_fenced(out[-1]):
                prev_body = out[-1].strip()[3:-3].strip("\n")
                cur_body = cur.strip()[3:-3].strip("\n")
                out[-1] = f"```\n{prev_body}\n{cur_body}\n```"
            elif (
                _looks_like_comment_line(cur)
                and out
                and _is_fenced(out[-1])
                and i + 1 < len(paragraphs)
                and _is_fenced(paragraphs[i + 1])
            ):
                prev_body = out[-1].strip()[3:-3].strip("\n")
                next_body = paragraphs[i + 1].strip()[3:-3].strip("\n")
                out[-1] = f"```\n{prev_body}\n{cur}\n{next_body}\n```"
                i += 1  # 跳过下一个 fenced 块
            else:
                out.append(cur)
            i += 1
        return "\n\n".join(out)

    @staticmethod
    def _is_mono_font(font_name: str) -> bool:
        if not font_name:
            return False
        f = font_name.lower()
        return any(k in f for k in (
            "mono", "courier", "consola", "consolas", "code",
            "menlo", "inconsolata", "sourcecode", "fira", "hack",
        ))

    @classmethod
    def _render_text_block(cls, block: dict, body_size: float) -> tuple[str, str | None]:
        """把单个文本 block 渲染为 Markdown 段落 / 标题 / 代码块。

        - 取 block 内所有 span 的最大字号判 heading 等级
        - 若 block 内多数 span 用等宽字体 → 输出 ``` fenced code block，保留行
        - 普通段落：行连接（中文直连，英文加空格）
        """
        max_size = 0.0
        line_records: list[tuple[float, str, float]] = []  # (x0, text, size)
        mono_chars = 0
        total_chars = 0
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_str = "".join(span.get("text", "") for span in spans)
            stripped = line_str.rstrip()
            if not stripped.strip():
                continue
            line_x0 = line.get("bbox", [0])[0]
            # 取 line 内首个非空 span 的 size 作为该行字号
            first_size = next((s.get("size", 0) for s in spans if s.get("text", "").strip()), 0)
            line_records.append((line_x0, stripped, first_size))
            for span in spans:
                sz = span.get("size", 0)
                if sz > max_size:
                    max_size = sz
                t_len = len(span.get("text", ""))
                total_chars += t_len
                if cls._is_mono_font(span.get("font", "")):
                    mono_chars += t_len
        if not line_records:
            return "", None

        # 代码块判定：超半数字符是等宽字体 → fenced，按 x0 推导缩进
        if total_chars > 0 and mono_chars / total_chars > 0.5 and len(line_records) >= 1:
            min_x = min(r[0] for r in line_records)
            # 估算字符宽度：用首行字号 * 0.5 作为等宽字体的近似宽度
            char_w = max((line_records[0][2] or 10) * 0.5, 4.0)
            code_lines = []
            for x0, text, _sz in line_records:
                indent = max(0, int(round((x0 - min_x) / char_w)))
                code_lines.append(" " * indent + text)
            body = "\n".join(code_lines)
            return f"```\n{body}\n```", None

        line_texts = [r[1] for r in line_records]

        # 段落合并
        merged: list[str] = []
        for i, t in enumerate(line_texts):
            t = t.strip()
            if not t:
                continue
            if i == 0 or not merged:
                merged.append(t)
                continue
            prev = merged[-1]
            sep = " " if prev and prev[-1].isascii() and prev[-1].isalnum() else ""
            merged[-1] = prev + sep + t
        text = merged[0] if merged else ""
        if not text:
            return "", None

        # 标题判定（相对 body_size 比例）
        if body_size > 0 and len(text) < 120:
            ratio = max_size / body_size
            if ratio >= 1.5:
                return f"# {text}", text
            if ratio >= 1.25:
                return f"## {text}", text
            if ratio >= 1.1:
                return f"### {text}", text

        return text, None


class MarkerExtractor(Extractor):
    """使用 Marker 进行 OCR 提取（扫描版 PDF）"""

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def extract(self, file_path: Path) -> ExtractedBook:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
        except ImportError:
            raise ImportError(
                "需要安装 marker-pdf: pip install marker-pdf"
            )

        logger.info(f"使用 Marker OCR 处理: {file_path.name}")

        try:  # M3: 包裹 OCR 核心步骤，OOM/模型加载失败/损坏PDF 输出结构化错误
            model_dict = create_model_dict()
            converter = PdfConverter(artifact_dict=model_dict)
            result = converter(str(file_path))
        except Exception as exc:
            raise RuntimeError(
                f"Marker OCR 处理失败 ({file_path.name}): {exc}"
            ) from exc

        # Marker 返回的是完整 markdown
        full_text = result.markdown

        # 提取 Marker 识别出的图片（result.images 是 {filename: PIL.Image} 字典）
        # 同时建立 原始名 → 净化后名 的映射，用于把正文中的 ![](xxx) 路径改写
        extracted_images: list[ExtractedImage] = []
        name_remap: dict[str, str] = {}
        if hasattr(result, "images") and result.images:
            import io
            for _marker_img_idx, (img_name, pil_img) in enumerate(result.images.items(), 1):
                try:
                    buf = io.BytesIO()
                    # H1 修复：img_name 来自 Marker 库，经净化防目录遍历
                    safe_filename = _sanitize_image_filename(img_name, _marker_img_idx)
                    ext_upper = safe_filename.rsplit(".", 1)[-1].upper()
                    fmt = ext_upper if ext_upper in ("PNG", "JPEG", "WEBP") else "PNG"
                    pil_img.save(buf, format=fmt)
                    extracted_images.append(ExtractedImage(
                        filename=safe_filename,
                        data=buf.getvalue(),
                        media_type=f"image/{fmt.lower()}",
                    ))
                    name_remap[img_name] = safe_filename
                    name_remap[Path(img_name).name] = safe_filename
                except Exception as e:
                    logger.warning(f"无法导出 Marker 图片 {img_name}: {e}")

        # 重写 markdown 中的图片引用：![alt](orig_name) → ![alt](../images/safe_name)
        if name_remap:
            def _rewrite(match: re.Match) -> str:
                alt = match.group(1)
                src = match.group(2).strip()
                # 去掉可能的 angle bracket 和 query
                src_key = src.split("?")[0].strip("<>")
                base = Path(src_key).name
                safe = name_remap.get(src_key) or name_remap.get(base)
                if not safe:
                    return match.group(0)
                return f"![{alt}](../images/{safe})"

            full_text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _rewrite, full_text)

        pages = self._split_by_pages(full_text)

        return ExtractedBook(
            title=file_path.stem,
            source_path=str(file_path),
            pages=pages,
            metadata={"ocr_engine": "marker"},
            images=extracted_images,
        )

    def _split_by_pages(self, markdown_text: str) -> list[ExtractedPage]:
        """将 Marker 输出的 markdown 按段落分成逻辑页"""
        sections = markdown_text.split("\n\n")
        pages: list[ExtractedPage] = []
        current_text: list[str] = []
        current_headings: list[str] = []
        current_char_count = 0  # L6: 运行时累计计数，避免 O(n²) sum()
        page_num = 1

        for section in sections:
            current_text.append(section)
            current_char_count += len(section)  # L6: O(1) 更新
            # 检测标题行
            for line in section.split("\n"):
                stripped = line.strip()
                if stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip()
                    if heading:
                        current_headings.append(heading)

            # 每积累约 2000 字符算一页
            if current_char_count > 2000:  # L6: O(1) 判断
                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text="\n\n".join(current_text),
                        detected_headings=current_headings,
                    )
                )
                page_num += 1
                current_text = []
                current_headings = []
                current_char_count = 0  # L6: 重置计数器

        if current_text:
            pages.append(
                ExtractedPage(
                    page_number=page_num,
                    text="\n\n".join(current_text),
                    detected_headings=current_headings,
                )
            )

        return pages


class EPUBExtractor(Extractor):
    """从 EPUB 电子书提取文本，使用 TOC + spine 还原原书章节结构。"""

    def __init__(
        self,
        autocorrect: bool = False,
        download_remote_images: bool = False,
    ) -> None:
        """初始化 EPUB 提取器。

        Args:
            autocorrect: 是否在 CJK 字符与半角英文/数字之间自动补全空格。
            download_remote_images: 是否下载 EPUB 中嵌入的远程图片 URL。
                安全限制：拒绝私有/回环地址（防 SSRF）；限制最大 5 MB；超时 10 秒。
        """
        self.autocorrect = autocorrect
        self.download_remote_images = download_remote_images

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".epub"

    def extract(self, file_path: Path) -> ExtractedBook:
        import ebooklib
        from bs4 import BeautifulSoup
        from ebooklib import epub

        book = epub.read_epub(str(file_path))

        title = book.get_metadata("DC", "title")
        book_title = title[0][0] if title else file_path.stem

        author = book.get_metadata("DC", "creator")
        book_author = author[0][0] if author else "Unknown"

        # Step 1: 提取所有图片，建立原始路径 → 目标文件名的映射
        extracted_images, image_name_map = self._extract_epub_images(book)

        # Step 2a: 第一遍扫描 spine，建立 anchor → heading 映射，
        # 用于把 EPUB 的 xxx.xhtml#anchor 链接转成 Obsidian [[#标题]] 形式。
        spine_items = list(self._iter_spine_documents(book))
        anchor_map = self._build_anchor_map(spine_items)

        # Step 2b: 把每个 XHTML 文档转成 Markdown
        # extra_images 收集从远程 URL 下载的图片（download_remote_images=True 时使用）
        extra_images: list[ExtractedImage] = []
        spine_docs: list[tuple[str, str, list[str]]] = []  # (href, markdown, headings)
        for item in spine_items:
            href = item.file_name
            md, headings = self._html_item_to_markdown(
                item, image_name_map, anchor_map,
                extra_images if self.download_remote_images else None,
            )
            spine_docs.append((href, md, headings))

        # Step 3: 用 TOC 把 spine 切成章节；TOC 缺失或畸形则退化为"每个 spine 文档一章"
        try:
            toc_entries = list(self._flatten_toc(book.toc))
        except Exception as _toc_err:
            logger.warning("TOC 解析失败，将按 spine 顺序处理章节: %s", _toc_err)
            toc_entries = []

        # 检测"单文件 EPUB"：整本书在一个 HTML 文件中，TOC 用锚点定位
        if len(spine_items) == 1 and toc_entries:
            native_chapters = self._split_single_file_epub_chapters(
                spine_items[0], toc_entries, image_name_map, anchor_map
            )
        else:
            native_chapters = self._build_native_chapters(spine_docs, toc_entries)

        # Step 4: 同时构造 ExtractedPage，用于不依赖 native_chapters 的下游
        pages: list[ExtractedPage] = []
        for idx, (_, md, headings) in enumerate(spine_docs, 1):
            if not md.strip():
                continue
            pages.append(
                ExtractedPage(
                    page_number=idx,
                    text=md,
                    detected_headings=headings,
                )
            )

        all_images = extracted_images + extra_images

        # CJK 自动纠错：在中英文混排文本中补全空格
        if self.autocorrect:
            pages = [
                ExtractedPage(
                    page_number=p.page_number,
                    text=_apply_cjk_autocorrect(p.text),
                    detected_headings=p.detected_headings,
                )
                for p in pages
            ]
            native_chapters = [
                NativeChapter(
                    chapter_number=ch.chapter_number,
                    title=ch.title,
                    content=_apply_cjk_autocorrect(ch.content),
                    page_range=ch.page_range,
                    spine_files=ch.spine_files,
                )
                for ch in native_chapters
            ]

        return ExtractedBook(
            title=book_title,
            source_path=str(file_path),
            pages=pages,
            metadata={"author": book_author, "format": "epub"},
            images=all_images,
            native_chapters=native_chapters,
        )

    # ------------------------------------------------------------------ helpers

    def _split_single_file_epub_chapters(
        self,
        spine_item,
        toc_entries: list[tuple[int, str, str]],
        image_name_map: dict[str, str],
        anchor_map: dict[tuple[str, str], str],
    ) -> list[NativeChapter]:
        """单文件 EPUB：整本书在一个 HTML 中，按 H1 元素边界切章。

        先对整个 HTML 做图片/链接预处理，再按 H1 边界分段，每段转成 Markdown。
        """
        from bs4 import BeautifulSoup, NavigableString
        from markdownify import markdownify as md_convert

        raw = spine_item.get_content()
        soup = BeautifulSoup(raw, "html.parser")
        item_path = spine_item.file_name

        # === 与 _html_item_to_markdown 相同的预处理 ===
        for sel in ("nav", "header.headerlink", ".headerlink",
                    "div.related", "footer"):
            for node in soup.select(sel):
                node.decompose()

        # 过滤 EPUB 内嵌 HTML 目录（<p class="toc-level-*">）避免把目录列表塞入正文
        for node in soup.find_all("p", class_=lambda c: c and any("toc" in cls for cls in c)):
            node.decompose()

        import posixpath
        cur_dir = posixpath.dirname(item_path)
        amap = anchor_map or {}

        for a_tag in soup.find_all("a"):
            href = (a_tag.get("href") or "").strip()
            if not href:
                continue
            href_l = href.lower()
            if href_l.startswith(("http://", "https://", "mailto:", "ftp://")):
                continue
            if "#" in href:
                path_part, anchor = href.split("#", 1)
            else:
                path_part, anchor = href, ""
            if path_part:
                target_full = posixpath.normpath(posixpath.join(cur_dir, path_part))
            else:
                target_full = item_path
            link_text = a_tag.get_text(strip=True)
            heading_text = amap.get((target_full, anchor)) or amap.get((target_full, ""))
            is_same_doc = (target_full == item_path)
            if heading_text and is_same_doc:
                if link_text and link_text != heading_text:
                    a_tag.replace_with(f"[[#{heading_text}|{link_text}]]")
                else:
                    a_tag.replace_with(f"[[#{heading_text}]]")
            elif heading_text:
                payload = f"{target_full}|{anchor}|{heading_text}|{link_text}"
                token = payload.encode("utf-8").hex()
                a_tag.replace_with(f"{{{{XLINK|{token}}}}}")
            else:
                a_tag.replace_with(link_text or href)

        # 处理 <figure>/<figcaption>（同 _html_item_to_markdown 逻辑）
        for figure_tag in soup.find_all("figure"):
            cap_tag = figure_tag.find("figcaption")
            cap_text = cap_tag.get_text(strip=True) if cap_tag else ""
            img_in_fig = figure_tag.find("img")
            if img_in_fig and cap_text:
                existing_alt = (img_in_fig.get("alt") or "").strip()
                if not existing_alt or "/" in existing_alt or existing_alt.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
                ):
                    img_in_fig["alt"] = cap_text
            if cap_tag:
                cap_tag.decompose()
            figure_tag.unwrap()

        # 把 <img> 替换成标准 Markdown 引用，路径相对 chapters/ → ../images/
        # 注意：_split_single_file_epub_chapters 没有 extra_images 参数，
        # 远程图片一律保留原始外链（不下载）。
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "") or ""
            alt = (img_tag.get("alt") or img_tag.get("title") or "").strip()
            if alt and ("/" in alt or alt.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))):
                alt = ""
            # 远程 URL：保留原始外链
            if src.lower().startswith(("http://", "https://")):
                img_tag.replace_with(f"\n\n![{alt}]({src})\n\n")
                continue
            src_basename = Path(src).name if src else ""
            clean_name = (
                image_name_map.get(src_basename)
                or image_name_map.get(src)
                or src_basename
            )
            if clean_name:
                img_tag.replace_with(f"\n\n![{alt}](../images/{clean_name})\n\n")
            else:
                img_tag.decompose()

        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            original = tag.get_text()
            cleaned = _sanitize_anchor(original)
            if cleaned != original:
                tag.clear()
                tag.append(cleaned)

        # === 按 H1 边界切分 body 内容 ===
        body = soup.find("body") or soup
        chapters: list[NativeChapter] = []
        current_h1_title: str | None = None
        current_nodes: list = []

        def _flush_chapter(title: str, nodes: list) -> None:
            if not nodes:
                return
            section_html = "".join(str(n) for n in nodes)
            markdown = md_convert(
                section_html,
                heading_style="ATX",
                bullets="-",
                code_language="",
                strip=["script", "style"],
            )
            markdown = re.sub(r"^\s*xml\s+version=.*?$", "", markdown, flags=re.MULTILINE)
            markdown = re.sub(r"<\?xml[^>]*\?>", "", markdown)
            markdown = re.sub(r"<!DOCTYPE[^>]*>", "", markdown, flags=re.IGNORECASE)
            markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
            if not markdown:
                return
            chapters.append(NativeChapter(
                chapter_number=len(chapters) + 1,
                title=title,
                content=f"# {title}\n\n{markdown}",
                page_range=(len(chapters) + 1, len(chapters) + 1),
                spine_files=(item_path,),
            ))

        for child in body.children:
            if getattr(child, "name", None) == "h1":
                # 把上一章 flush
                if current_h1_title is not None:
                    _flush_chapter(current_h1_title, current_nodes)
                current_h1_title = child.get_text(strip=True)
                current_nodes = []  # H1 本身不收集，_flush_chapter 已通过 title 加标题
            else:
                if current_h1_title is None:
                    # 在第一个 H1 之前的内容：作为"前置内容"
                    if isinstance(child, NavigableString):
                        continue
                    # 有实质内容时单独成章
                    txt = child.get_text(strip=True) if hasattr(child, "get_text") else ""
                    if txt:
                        current_h1_title = txt[:40]
                        current_nodes = [child]
                else:
                    current_nodes.append(child)

        # flush 最后一章
        if current_h1_title is not None:
            _flush_chapter(current_h1_title, current_nodes)

        return chapters

    def _extract_epub_images(self, book) -> tuple[list[ExtractedImage], dict[str, str]]:
        import ebooklib

        extracted_images: list[ExtractedImage] = []
        image_name_map: dict[str, str] = {}

        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            if len(extracted_images) >= _MAX_IMAGES:
                logger.warning(
                    "EPUB 图片数 (%d+) 超过上限 %d，剩余图片将被跳过。",
                    len(extracted_images), _MAX_IMAGES,
                )
                break
            content = item.get_content()
            if len(content) < 500:
                continue
            basename = Path(item.file_name).name
            safe_name = _sanitize_image_filename(basename, len(extracted_images) + 1)
            media_type = item.media_type or "image/png"
            extracted_images.append(ExtractedImage(
                filename=safe_name,
                data=content,
                media_type=media_type,
            ))
            image_name_map[basename] = safe_name
            image_name_map[item.file_name] = safe_name
        return extracted_images, image_name_map

    def _iter_spine_documents(self, book):
        """按 spine 顺序产出 XHTML 文档 item；过滤掉 nav / cover。"""
        import ebooklib

        for entry in book.spine:
            idref = entry[0] if isinstance(entry, tuple) else entry
            item = book.get_item_with_id(idref)
            if item is None:
                continue
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            # 跳过 EPUB3 nav 文档
            props = getattr(item, "properties", None) or []
            if "nav" in props:
                continue
            yield item

    def _build_anchor_map(self, spine_items) -> dict[tuple[str, str], str]:
        """扫描所有 spine XHTML，建立 (basename, anchor_id) → heading_text 映射。

        anchor_id 为空字符串时，表示该 xhtml 文件本身（指向首个 heading）。
        """
        from bs4 import BeautifulSoup

    def _build_anchor_map(self, spine_items) -> dict[tuple[str, str], str]:
        """扫描所有 spine XHTML，建立 (file_name, anchor_id) → heading_text 映射。

        key 用完整 file_name（含路径），避免 EPUB 子目录下重名（如多章都有
        index.xhtml）时被覆盖。anchor_id 为空字符串表示该 xhtml 文件本身。
        """
        from bs4 import BeautifulSoup

        amap: dict[tuple[str, str], str] = {}
        for item in spine_items:
            key_path = item.file_name  # 完整路径作 key
            soup = BeautifulSoup(item.get_content(), "html.parser")

            first_heading = ""
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                txt = _sanitize_anchor(tag.get_text(strip=True))
                if txt:
                    first_heading = txt
                    break
            if first_heading:
                amap[(key_path, "")] = first_heading

            for el in soup.find_all(id=True):
                anchor = el.get("id") or ""
                if not anchor:
                    continue
                if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    txt = el.get_text(strip=True)
                else:
                    h = el.find(["h1", "h2", "h3", "h4", "h5", "h6"])
                    txt = h.get_text(strip=True) if h else el.get_text(strip=True)[:80]
                txt = _sanitize_anchor(txt)
                if txt:
                    amap[(key_path, anchor)] = txt
        return amap

    def _download_remote_image(
        self,
        url: str,
        image_name_map: dict[str, str],
        extra_images: list[ExtractedImage],
    ) -> str | None:
        """下载远程图片，加入 extra_images，返回安全文件名；失败返回 None。

        安全限制：拒绝私有/回环地址（防 SSRF）；限制最大 5 MB；超时 10 秒。
        """
        import ipaddress
        import urllib.parse
        import urllib.request

        # 已下载过直接复用
        if url in image_name_map:
            return image_name_map[url]

        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return None
            # SSRF 防御：拒绝私有 / 回环 IP
            host = parsed.hostname or ""
            try:
                addr = ipaddress.ip_address(host)
                if addr.is_private or addr.is_loopback:
                    logger.warning("跳过内网远程图片: %s", url)
                    return None
            except ValueError:
                pass  # 域名而非 IP，不需过滤

            basename = Path(parsed.path).name or "remote_img"
            safe_name = _sanitize_image_filename(basename, len(image_name_map) + 1)
            # 确保文件名不重复
            existing = set(image_name_map.values()) | {img.filename for img in extra_images}
            stem, _, ext = safe_name.rpartition(".")
            counter = 1
            while safe_name in existing:
                safe_name = f"{stem}_{counter:02d}.{ext}"
                counter += 1

            req = urllib.request.Request(url, headers={"User-Agent": "book-to-obsidian/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = resp.read(5 * 1024 * 1024)  # 最大 5 MB
            if len(data) < 500:
                return None

            media_type = f"image/{safe_name.rsplit('.', 1)[-1]}"
            extra_images.append(ExtractedImage(filename=safe_name, data=data, media_type=media_type))
            image_name_map[url] = safe_name
            return safe_name
        except Exception as exc:
            logger.debug("远程图片下载失败 %s: %s", url, exc)
            return None

    def _html_item_to_markdown(
        self,
        item,
        image_name_map: dict[str, str],
        anchor_map: dict[tuple[str, str], str] | None = None,
        extra_images: list[ExtractedImage] | None = None,
    ) -> tuple[str, list[str]]:
        """单个 XHTML item → (markdown, headings)。"""
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md

        soup = BeautifulSoup(item.get_content(), "html.parser")

        # 移除 EPUB 导航/页眉等噪声节点
        for sel in ("nav", "header.headerlink", ".headerlink",
                    "div.related", "footer"):
            for node in soup.select(sel):
                node.decompose()

        # 提取同文档内的脚注定义，转成 Markdown footnote 语法 [^id]: text
        # 支持两种 EPUB 脚注模式：
        #   A. <aside epub:type="footnote" id="fnX">...</aside>
        #   B. <ol class="footnotes"><li id="fnX">...</li></ol>（Pandoc/calibre 生成）
        footnote_defs: dict[str, str] = {}

        # Pattern A: <aside epub:type="footnote">
        for aside in soup.find_all("aside"):
            epub_type = (aside.get("epub:type") or "").lower()
            if "footnote" in epub_type:
                fn_id = (aside.get("id") or "").strip()
                if fn_id:
                    # 去掉脚注里的回跳链接（↩ ↑ 类符号）
                    for back in aside.find_all("a"):
                        back_text = back.get_text(strip=True)
                        if back_text in ("↩", "↑", "←", "↵", "⬆"):
                            back.decompose()
                    fn_text = " ".join(aside.get_text(separator=" ").split()).rstrip("↩↑←↵⬆").strip()
                    if fn_text:
                        footnote_defs[fn_id] = fn_text
                    aside.decompose()

        # Pattern B: <section epub:type="footnotes"> 或 <ol class="footnotes">
        for container in soup.find_all(["ol", "ul", "section", "div"]):
            epub_type = (container.get("epub:type") or "").lower()
            cls = " ".join(container.get("class") or []).lower()
            if "footnote" in epub_type or "footnote" in cls:
                for li in container.find_all("li"):
                    fn_id = (li.get("id") or "").strip()
                    if fn_id:
                        for back in li.find_all("a", class_=re.compile(r"reverse|backlink|back", re.I)):
                            back.decompose()
                        fn_text = " ".join(li.get_text(separator=" ").split()).rstrip("↩↑←↵⬆").strip()
                        if fn_text:
                            footnote_defs[fn_id] = fn_text
                container.decompose()

        # 处理 EPUB 内部 <a> 链接：
        # - 外链 → 保留
        # - 同 xhtml 内 #anchor → [[#标题]]（同 .md 内可跳）
        # - 脚注引用 #fnX → [^fnX]（仅当 fnX 在 footnote_defs 中）
        # - 跨 xhtml → 输出占位符 {{XLINK|<b64>}}，由 splitter/book_split
        #   阶段二次替换为 [[章节文件名#标题|text]]，因为只有那时才知道目标章节文件名
        amap = anchor_map or {}
        import posixpath
        cur_dir = posixpath.dirname(item.file_name)
        cur_path = item.file_name

        for a_tag in soup.find_all("a"):
            href = (a_tag.get("href") or "").strip()
            if not href:
                continue
            href_l = href.lower()
            if href_l.startswith(("http://", "https://", "mailto:", "ftp://")):
                continue

            if "#" in href:
                path_part, anchor = href.split("#", 1)
            else:
                path_part, anchor = href, ""

            if path_part:
                target_full = posixpath.normpath(posixpath.join(cur_dir, path_part))
            else:
                target_full = cur_path

            # 脚注引用：同文档内 #fnX，且 fnX 已在 footnote_defs → [^fnX]
            if not path_part and anchor in footnote_defs:
                a_tag.replace_with(f"[^{anchor}]")
                continue

            link_text = a_tag.get_text(strip=True)
            heading_text = amap.get((target_full, anchor)) or amap.get((target_full, ""))
            is_same_doc = (target_full == cur_path)

            if heading_text and is_same_doc:
                if link_text and link_text != heading_text:
                    a_tag.replace_with(f"[[#{heading_text}|{link_text}]]")
                else:
                    a_tag.replace_with(f"[[#{heading_text}]]")
            elif heading_text:
                # 跨 xhtml：留占位符，等 splitter 阶段二次替换
                # 用 hex 编码：纯 [0-9a-f]，不会被 markdownify 当 markdown 字符转义
                payload = f"{target_full}|{anchor}|{heading_text}|{link_text}"
                token = payload.encode("utf-8").hex()
                a_tag.replace_with(f"{{{{XLINK|{token}}}}}")
            else:
                a_tag.replace_with(link_text or href)

        # 处理 <figure>/<figcaption>：把 figcaption 文本作为图片的 alt，再 unwrap figure
        for figure_tag in soup.find_all("figure"):
            cap_tag = figure_tag.find("figcaption")
            cap_text = cap_tag.get_text(strip=True) if cap_tag else ""
            img_in_fig = figure_tag.find("img")
            if img_in_fig and cap_text:
                existing_alt = (img_in_fig.get("alt") or "").strip()
                # 仅在 alt 为空或像文件路径时覆盖
                if not existing_alt or "/" in existing_alt or existing_alt.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
                ):
                    img_in_fig["alt"] = cap_text
            if cap_tag:
                cap_tag.decompose()  # 避免 figcaption 文字在 markdownify 里重复输出
            figure_tag.unwrap()  # 去掉 <figure> 包装，让内层 <img> 正常处理

        # 把 <img> 替换成标准 Markdown 引用，路径相对 chapters/ → ../images/
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "") or ""
            alt = (img_tag.get("alt") or img_tag.get("title") or "").strip()
            # 若 alt 看起来像文件路径，丢弃避免污染
            if alt and ("/" in alt or alt.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))):
                alt = ""
            # 远程 URL：保留原始链接（或按需下载）
            if src.lower().startswith(("http://", "https://")):
                if extra_images is not None:
                    safe = self._download_remote_image(src, image_name_map, extra_images)
                    if safe:
                        img_tag.replace_with(f"\n\n![{alt}](../images/{safe})\n\n")
                    else:
                        img_tag.replace_with(f"\n\n![{alt}]({src})\n\n")
                else:
                    # 保留原始外链，不尝试嵌入
                    img_tag.replace_with(f"\n\n![{alt}]({src})\n\n")
                continue
            src_basename = Path(src).name if src else ""
            clean_name = (
                image_name_map.get(src_basename)
                or image_name_map.get(src)
                or src_basename
            )
            if clean_name:
                img_tag.replace_with(f"\n\n![{alt}](../images/{clean_name})\n\n")
            else:
                img_tag.decompose()

        # 把 heading 文本中可能破坏 wikilink 锚点的字符同步替换，
        # 保证 [[#X]] 与正文 `# X` 完全一致。
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            original = tag.get_text()
            cleaned = _sanitize_anchor(original)
            if cleaned != original:
                tag.clear()
                tag.append(cleaned)

        headings = [
            tag.get_text(strip=True)
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            if tag.get_text(strip=True)
        ]

        markdown = md(
            str(soup),
            heading_style="ATX",        # 用 # 形式
            bullets="-",
            code_language="",
            strip=["script", "style"],
        )
        # 清理 XML/DOCTYPE 声明残片 + markdownify 留下的多余空行
        markdown = re.sub(r"^\s*xml\s+version=.*?\?\s*$", "", markdown, flags=re.MULTILINE)
        markdown = re.sub(r"<\?xml[^>]*\?>", "", markdown)
        markdown = re.sub(r"<!DOCTYPE[^>]*>", "", markdown, flags=re.IGNORECASE)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

        # 附加脚注定义到章节末尾
        if footnote_defs:
            fn_block = "\n".join(f"[^{fid}]: {ftxt}" for fid, ftxt in footnote_defs.items())
            markdown = markdown + "\n\n" + fn_block

        return markdown, headings

    @staticmethod
    def _flatten_toc(toc) -> list[tuple[int, str, str]]:
        """递归扁平化 ebooklib TOC，产出 (depth, title, href)。

        对畸形 TOC 条目做容错处理，逐条跳过无法解析的项。
        """
        out: list[tuple[int, str, str]] = []

        def walk(items, depth: int) -> None:
            if not items:
                return
            for entry in items:
                try:
                    if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[1], (list, tuple)):
                        section, children = entry
                        title = getattr(section, "title", "") or ""
                        href = getattr(section, "href", "") or ""
                        if title:
                            out.append((depth, title, href))
                        walk(children, depth + 1)
                    else:
                        title = getattr(entry, "title", "") or ""
                        href = getattr(entry, "href", "") or ""
                        if title:
                            out.append((depth, title, href))
                except Exception as _e:
                    logger.debug("跳过畸形 TOC 条目: %s (%s)", entry, _e)
                    continue

        walk(toc or [], 0)
        return out

    def _build_native_chapters(
        self,
        spine_docs: list[tuple[str, str, list[str]]],
        toc_entries: list[tuple[int, str, str]],
    ) -> list[NativeChapter]:
        """根据 TOC 顶层条目把 spine 切成章节。"""
        if not spine_docs:
            return []

        spine_hrefs = [href for href, _, _ in spine_docs]

        # 取 TOC 顶层条目（depth==0）。如果顶层只有 1 条，退化为取所有 depth<=1。
        top = [(t, h.split("#", 1)[0]) for d, t, h in toc_entries if d == 0 and h]
        if len(top) <= 1:
            top = [(t, h.split("#", 1)[0]) for d, t, h in toc_entries if d <= 1 and h]

        # 把每个 TOC 顶层条目映射到 spine 索引
        starts: list[tuple[str, int]] = []
        used_indices: set[int] = set()
        for title, href in top:
            for i, sh in enumerate(spine_hrefs):
                if i in used_indices:
                    continue
                if sh == href or sh.endswith("/" + href) or href.endswith(sh):
                    starts.append((title, i))
                    used_indices.add(i)
                    break

        # 退化策略：TOC 不可用时，每个 spine 文档当一章，标题取首个 heading
        if not starts:
            chapters: list[NativeChapter] = []
            for i, (href, md, headings) in enumerate(spine_docs, 1):
                if not md.strip():
                    continue
                chapters.append(NativeChapter(
                    chapter_number=len(chapters) + 1,
                    title=headings[0] if headings else f"章节 {len(chapters) + 1}",
                    content=md,
                    page_range=(i, i),
                    spine_files=(href,),
                ))
            return chapters

        # 按 spine 索引排序，确保章节按阅读顺序输出
        starts.sort(key=lambda x: x[1])

        chapters: list[NativeChapter] = []
        for idx, (title, start) in enumerate(starts):
            end = starts[idx + 1][1] if idx + 1 < len(starts) else len(spine_docs)
            slice_md = "\n\n".join(
                md for _, md, _ in spine_docs[start:end] if md.strip()
            )
            if not slice_md.strip():
                continue
            chapters.append(NativeChapter(
                chapter_number=len(chapters) + 1,
                title=title,
                content=slice_md,
                page_range=(start + 1, end),
                spine_files=tuple(href for href, _, _ in spine_docs[start:end]),
            ))
        return chapters


def get_extractor(file_path: Path, ocr_engine: str = "auto") -> Extractor:
    """根据文件类型和配置选择合适的提取器。

    .. deprecated::
        优先使用 :func:`auto_extract`，它会自动检测扫描版并选择最佳引擎。
        本函数保留供需要手动指定引擎的场景使用。

    Args:
        file_path: 书籍文件路径。
        ocr_engine: ``"auto"``（默认，自动检测）、``"marker"``（强制 OCR）、
            ``"pymupdf"``（强制文字提取）。
    """  # C3: 修改默认值 marker→auto，避免对所有 PDF 触发重型 OCR
    suffix = file_path.suffix.lower()

    if suffix == ".epub":
        return EPUBExtractor()
    elif suffix == ".pdf":
        if ocr_engine == "marker":
            return MarkerExtractor()
        if ocr_engine == "auto":
            return MarkerExtractor() if is_scanned_pdf(file_path) else PDFTextExtractor()
        return PDFTextExtractor()
    else:
        raise ValueError(f"不支持的文件格式: {suffix}，支持 .pdf / .epub")


def is_scanned_pdf(file_path: Path) -> bool:
    """检测 PDF 是否为扫描版（图片型）"""
    import fitz

    text_pages = 0
    with fitz.open(str(file_path)) as doc:  # C1: 使用上下文管理器避免句柄泄漏
        # 检查前 5 页
        check_count = min(5, len(doc))
        for i in range(check_count):
            text = doc[i].get_text("text").strip()
            if len(text) > 50:
                text_pages += 1

    # 如果超过一半的页面有文本，认为是文字版
    return text_pages < check_count / 2


def auto_extract(file_path: Path) -> ExtractedBook:
    """自动检测格式并选择最佳提取方式"""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    if path.suffix.lower() == ".epub":
        logger.info(f"检测到 EPUB 格式: {path.name}")
        return EPUBExtractor().extract(path)

    if path.suffix.lower() == ".pdf":
        if is_scanned_pdf(path):
            logger.info(f"检测到扫描版 PDF，使用 Marker OCR: {path.name}")
            return MarkerExtractor().extract(path)
        else:
            logger.info(f"检测到文字版 PDF: {path.name}")
            return PDFTextExtractor().extract(path)

    raise ValueError(f"不支持的格式: {path.suffix}")
