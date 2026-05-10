# book-to-obsidian

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/packaging-uv-261230?logo=python&logoColor=white)](https://github.com/astral-sh/uv)
[![Obsidian](https://img.shields.io/badge/for-Obsidian-7C3AED?logo=obsidian&logoColor=white)](https://obsidian.md/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/inorilzy/book-to-obsidian-skill/pulls)

将书籍（EPUB / PDF，支持扫描版 OCR）直接转换为 Obsidian 可用的 Markdown 文件。

**纯 Python 规则提取，无 LLM 参与**，速度快（秒级），无 token 消耗。

---

## 安装

```powershell
# 1. 安装 uv（如果还没有）
winget install astral-sh.uv

# 2. 克隆仓库
git clone https://github.com/inorilzy/book-to-obsidian-skill.git
cd book-to-obsidian-skill

# 3. 同步依赖（自动创建 .venv + Python 3.12）
uv sync
```

可选：安装扫描版 OCR 引擎（体积大，按需安装）：
```bash
uv pip install marker-pdf
```

---

## 快速使用

```powershell
uv run python scripts/book_split.py "D:\books\test.epub" --output-dir ./work/test
```

输出结构：
```
work/test/
├── chapters/        ← 每章一个 .md（已含标题、图片引用、wikilinks）
├── images/          ← 提取的所有图片
└── manifest.json    ← 书名、章节列表、图片数
```

将 `chapters/` 和 `images/` 复制进 Obsidian vault 即可。图片引用为 `![](../images/xxx.jpg)`，chapters 和 images 同级时自动解析。

---

## 命令参考

```bash
# 探查书籍信息
uv run python scripts/book_info.py "D:\books\test.pdf"

# 提取切分（主命令）
uv run python scripts/book_split.py "D:\books\test.pdf" \
    --output-dir ./work/test \
    --filename-style both       # 01 - 标题.md（默认）
    # --filename-style title    # 纯标题.md
    # --filename-style numbered # ch_001.md
    # --strategy heading        # 按标题切（PDF 默认 auto）
    # --pages-per-chapter 20    # page_count 策略时每章页数

# 扫描 vault，补全空 wikilink 目标
uv run python scripts/fix_dangling_links.py "E:\obsidian_vault\machine_learning"
```

---

## 提取能力

### EPUB

| 功能 | 说明 |
|------|------|
| 章节切分 | TOC + spine 精确切分 |
| 单文件 EPUB | 按 H1 边界切章（过滤内嵌 HTML 目录） |
| 标题 | `<h1>~<h6>` → `#~######` |
| 代码块 | `<pre><code>` → fenced 代码块 |
| 图片 | 提取到 images/，`![](../images/xxx.jpg)` |
| 内链 | 同章节 `[[#heading]]`，跨章节 `[[文件名#heading\|显示文字]]` |
| 表格 | markdownify 原生支持 |

### PDF（文字版）

| 功能 | 说明 |
|------|------|
| 章节切分 | 有书签→按书签；无书签→按标题正则（跳过代码块内伪标题） |
| 标题检测 | 字号比值：≥1.5×正文→H1，≥1.25×→H2，≥1.1×→H3 |
| 代码块 | 等宽字体比例 > 50% → fenced，保留缩进 |
| 跨页代码合并 | 相邻 fenced 块（中间仅注释行）自动合并 |
| 图片 | 按 bbox y 坐标插入临近位置 |
| 表格 | `find_tables()` 检测，转 markdown table |
| 页眉页脚 | 频率分析自动过滤 |

### 扫描版 PDF（OCR）

需安装 Marker（`uv pip install marker-pdf`），OCR 路径自动触发。

---

## 目录结构

```
book-to-obsidian/
├── SKILL.md              ← Claude Code / Copilot skill 入口
├── README.md             ← 本文件
├── pyproject.toml        ← uv 项目配置
│
├── scripts/              ← CLI 入口
│   ├── book_info.py      ← 探查书籍信息
│   ├── book_split.py     ← 主提取入口
│   ├── book_extract.py   ← 调试用：原始文本提取
│   └── fix_dangling_links.py ← vault 空链接补全
│
└── lib/                  ← 核心库
    ├── extractor.py      ← PDF/EPUB 提取引擎
    └── splitter.py       ← 章节切分逻辑
```
