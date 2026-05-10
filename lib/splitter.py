"""章节切分模块 - 将提取的书籍内容按章节分割"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .extractor import ExtractedBook, ExtractedPage, PDFTextExtractor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chapter:
    chapter_number: int
    title: str
    content: str
    page_range: tuple[int, int]  # (start_page, end_page)


# 中文章节标题的常见模式
_CHAPTER_PATTERNS = [
    # 第X章 / 第X节
    re.compile(r"^[#\s]*第[一二三四五六七八九十百千\d]+[章节篇部][\s:：]*(.*)", re.MULTILINE),
    # Chapter X / CHAPTER X
    re.compile(r"^[#\s]*[Cc]hapter\s+(\d+)[.:\s]*(.*)", re.MULTILINE),
    # 数字开头 1. / 1、 / 1 标题
    re.compile(r"^[#\s]*(\d{1,3})[.、\s]+([^\n]{2,50})$", re.MULTILINE),
    # Markdown 标题 # / ##
    re.compile(r"^(#{1,2})\s+(.+)$", re.MULTILINE),
]


def split_chapters(book: ExtractedBook, strategy: str = "auto", **kwargs) -> list[Chapter]:
    """根据策略切分章节。

    若书籍自带可靠的章节结构（如 EPUB TOC），且 strategy 为 "auto" 或
    "native"，优先使用，避免基于正则的二次切分把目录页/导航页误识别成章节。
    """
    if strategy in ("auto", "native") and book.native_chapters:
        logger.info("使用原生章节结构（%d 章）", len(book.native_chapters))
        return [
            Chapter(
                chapter_number=nc.chapter_number,
                title=nc.title,
                content=nc.content,
                page_range=nc.page_range,
            )
            for nc in book.native_chapters
        ]

    if strategy == "auto":
        chapters = _auto_split(book, **kwargs)
    elif strategy == "heading":
        chapters = _heading_split(book, **kwargs)
    elif strategy == "page_count":
        pages_per = kwargs.get("pages_per_chapter", 15)
        chapters = _page_count_split(book, pages_per)
    else:
        raise ValueError(f"未知的切分策略: {strategy}")

    # 兜底：所有非 native 路径再做一次跨页代码块合并
    return [
        Chapter(
            chapter_number=ch.chapter_number,
            title=ch.title,
            content=PDFTextExtractor._merge_cross_page_code(ch.content),
            page_range=ch.page_range,
        )
        for ch in chapters
    ]


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """返回所有 ``` ... ``` 包围段的 [start,end) 区间，用于排除其中的伪标题。"""
    ranges: list[tuple[int, int]] = []
    fence_iter = list(re.finditer(r"^```", text, re.MULTILINE))
    for i in range(0, len(fence_iter) - 1, 2):
        ranges.append((fence_iter[i].start(), fence_iter[i + 1].end()))
    return ranges


def _filter_outside_fences(matches: list[re.Match], ranges: list[tuple[int, int]]) -> list[re.Match]:
    if not ranges:
        return matches
    out = []
    for m in matches:
        pos = m.start()
        if not any(s <= pos < e for s, e in ranges):
            out.append(m)
    return out


def _auto_split(book: ExtractedBook, **kwargs) -> list[Chapter]:
    """自动检测最佳切分方式"""
    full_text = "\n\n".join(p.text for p in book.pages)
    fence_ranges = _fenced_ranges(full_text)

    # 先尝试中英文明确的章节模式（第一章、Chapter X）
    for pattern in _CHAPTER_PATTERNS[:2]:
        matches = _filter_outside_fences(list(pattern.finditer(full_text)), fence_ranges)
        if len(matches) >= 3:
            logger.info(f"检测到 {len(matches)} 个章节标题，使用标题切分")
            return _split_by_matches(book, full_text, matches, pattern)

    # M2: 尝试 Markdown 标题模式，防止 EPUB/Markdown 书籍在无 "Chapter X" 时错误降级为按页切分
    md_pattern = _CHAPTER_PATTERNS[3]  # ^(#{1,2})\s+(.+)$
    md_matches = _filter_outside_fences(list(md_pattern.finditer(full_text)), fence_ranges)
    if len(md_matches) >= 3:
        logger.info(f"检测到 {len(md_matches)} 个 Markdown 标题，使用标题切分")
        return _split_by_matches(book, full_text, md_matches, md_pattern)

    # 尝试用 heading 检测
    all_headings = []
    for page in book.pages:
        all_headings.extend(page.detected_headings)

    if len(all_headings) >= 3:
        logger.info(f"使用检测到的 {len(all_headings)} 个标题切分")
        return _heading_split(book, **kwargs)

    # 兜底：按页数切分
    pages_per = kwargs.get("pages_per_chapter", 15)
    logger.info(f"未检测到明确章节结构，按每 {pages_per} 页切分")
    return _page_count_split(book, pages_per)


def _split_by_matches(
    book: ExtractedBook,
    full_text: str,
    matches: list[re.Match],
    pattern: re.Pattern,
) -> list[Chapter]:
    """按正则匹配结果切分"""
    chapters: list[Chapter] = []

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)

        # M5: 优先使用 group(2)（继标题文本），避免包含 "Chapter 1." 前缀
        if match.lastindex and match.lastindex >= 2:
            # 中文模式: group(1)=章节名; Chapter/数字模式: group(2)=纯标题
            title = match.group(match.lastindex).strip()
        elif match.lastindex == 1:
            title = match.group(1).strip().lstrip("#").strip()
        else:
            title = match.group(0).strip().lstrip("#").strip()
        content = full_text[start:end].strip()

        # 估算页码范围
        char_pos_ratio = start / max(len(full_text), 1)
        total_pages = len(book.pages)
        start_page = max(1, int(char_pos_ratio * total_pages))

        end_ratio = end / max(len(full_text), 1)
        end_page = min(total_pages, int(end_ratio * total_pages))

        chapters.append(
            Chapter(
                chapter_number=i + 1,
                title=title,
                content=content,
                page_range=(start_page, end_page),
            )
        )

    return chapters


def _heading_split(book: ExtractedBook, **kwargs) -> list[Chapter]:
    """使用检测到的标题进行切分"""
    min_chars = kwargs.get("min_chapter_chars", 500)
    chapters: list[Chapter] = []
    current_title = ""
    current_content: list[str] = []
    current_start_page = 1
    chapter_num = 0

    for page in book.pages:
        if page.detected_headings:
            # 遇到新标题，保存之前的章节
            if current_content and len("\n".join(current_content)) >= min_chars:
                chapter_num += 1
                chapters.append(
                    Chapter(
                        chapter_number=chapter_num,
                        title=current_title or f"章节 {chapter_num}",
                        content="\n\n".join(current_content),
                        page_range=(current_start_page, page.page_number - 1),
                    )
                )
                current_content = []
                current_start_page = page.page_number

            current_title = page.detected_headings[0]

        current_content.append(page.text)

    # 最后一个章节
    if current_content and book.pages:  # H1: 防御 book.pages 为空时 book.pages[-1] IndexError
        chapter_num += 1
        chapters.append(
            Chapter(
                chapter_number=chapter_num,
                title=current_title or f"章节 {chapter_num}",
                content="\n\n".join(current_content),
                page_range=(current_start_page, book.pages[-1].page_number),
            )
        )

    return chapters


def _page_count_split(book: ExtractedBook, pages_per_chapter: int) -> list[Chapter]:
    """按固定页数切分"""
    chapters: list[Chapter] = []
    chapter_num = 0

    for i in range(0, len(book.pages), pages_per_chapter):
        chunk = book.pages[i : i + pages_per_chapter]
        chapter_num += 1

        # 尝试从第一页提取标题
        title = f"章节 {chapter_num}"
        for page in chunk:
            if page.detected_headings:
                title = page.detected_headings[0]
                break

        content = "\n\n".join(p.text for p in chunk)
        chapters.append(
            Chapter(
                chapter_number=chapter_num,
                title=title,
                content=content,
                page_range=(chunk[0].page_number, chunk[-1].page_number),
            )
        )

    return chapters
