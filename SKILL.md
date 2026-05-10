---
name: book-to-obsidian
description: |
  将书籍（PDF/EPUB，支持扫描版 OCR）直接转换为 Obsidian 可用的 Markdown 文件。
  纯 Python 规则提取，无需 LLM 参与，速度快，无 token 消耗。
  当用户说: 把这本书转成 md / 提取书籍内容 / PDF 转 markdown / book to obsidian / 整理这本书时激活。
---

# Book to Obsidian Skill

将书籍（EPUB / PDF）直接转成 Obsidian Markdown，**无 LLM 参与**，纯 Python 规则提取。

---

## 触发条件

用户提到以下意图时激活：
- "把这本书转成 md / obsidian"
- "PDF 提取 / PDF to markdown / book to obsidian"
- "整理这本书 / 把书放进知识库"
- 提供 `.pdf` / `.epub` 文件路径并希望进入 Obsidian

---

## 一键执行

```powershell
# PowerShell（一条命令完成所有事）
cd <skill_dir>
uv run python scripts/book_split.py "<book_path>" --output-dir "<output_dir>" --filename-style both
```

输出目录结构：
```
<output_dir>/
├── chapters/        ← 每章一个 .md 文件，已含标题和图片引用
├── images/          ← 所有提取图片（jpg/png）
└── manifest.json    ← 元数据（书名、章节列表、图片数）
```

---

## 完整使用流程

### Step 0: 环境检查（首次使用时）

```powershell
cd <skill_dir>
if (!(Test-Path .venv)) { uv sync }
```

如果没有 `uv`：`winget install astral-sh.uv`

### Step 1: 探查书籍信息（可选）

```bash
uv run python scripts/book_info.py "<book_path>"
```

输出：格式、是否扫描版、推荐引擎等信息。

### Step 2: 提取并切分

```bash
uv run python scripts/book_split.py "<book_path>" \
    --output-dir "<output_dir>" \
    --filename-style both
```

**关键参数**：
- `--output-dir`：输出目录（自动创建）
- `--filename-style`：`title`（纯标题）/ `numbered`（序号前缀）/ `both`（`01 - 标题`，默认）
- `--strategy`：`auto`（默认，自动检测）/ `heading`（按标题切）/ `page_count`（按页数切）
- `--pages-per-chapter N`：`page_count` 策略时每章页数（默认 20）

### Step 3: 将输出复制到 Vault

将 `chapters/` 和 `images/` 整体复制到 Obsidian vault 目录即可。

```powershell
# 示例：复制到 vault 的 books/<书名>/ 下
$dest = "E:\obsidian_vault\books\<书名>"
Copy-Item -Recurse "<output_dir>\chapters" $dest
Copy-Item -Recurse "<output_dir>\images" $dest
```

Obsidian 中图片引用为 `![](../images/xxx.jpg)`，chapters 和 images 同级时自动解析。

---

## 提取能力说明

### EPUB

| 功能 | 说明 |
|------|------|
| 章节切分 | TOC + spine 精确切分 |
| 单文件 EPUB | 整本书在一个 HTML 时，按 H1 边界切章 |
| 标题 | `<h1>~<h6>` 直接转 `#~######` |
| 代码块 | `<pre><code>` 转 fenced 代码块 |
| 图片 | 提取到 images/，相对路径引用 |
| 内链 | 同章节 `[[#heading]]`，跨章节 `[[文件名#heading\|显示文字]]` |
| 表格 | markdownify 原生支持 |

### PDF（文字版）

| 功能 | 说明 |
|------|------|
| 章节切分 | 有书签（outline）时按书签切；无书签时按标题正则切 |
| 标题检测 | 字号比值：≥1.5×正文→H1，≥1.25×→H2，≥1.1×→H3 |
| 代码块 | 等宽字体比例 > 50% → fenced 代码块，含缩进保留 |
| 跨页合并 | 相邻 fenced 块（中间只有注释行）自动合并为一个代码块 |
| 图片 | 按 bbox y 坐标插入临近文本位置 |
| 表格 | PyMuPDF `find_tables()` 检测，转标准 markdown table |
| 页眉页脚 | 频率分析自动识别并过滤 |
| 代码块内伪标题 | 过滤 fenced 区域内的 `^# ...` 避免误切章 |

### 扫描版 PDF（OCR）

需要安装 Marker：`uv pip install marker-pdf`

OCR 路径自动触发，输出同文字版 PDF，但图片引用路径改写。

---

## 配套脚本

| 脚本 | 用途 |
|------|------|
| `scripts/book_info.py` | 探查书籍基本信息 |
| `scripts/book_split.py` | 主提取入口（EPUB/PDF→Markdown）|
| `scripts/book_extract.py` | 调试用：提取原始文本，不切分 |
| `scripts/fix_dangling_links.py` | 扫描 vault，补全空 wikilink 目标 |

---

## 关键限制

- **不生成摘要、概念笔记、MOC**：专注于忠实还原原书文本结构
- **图片位置近似**：PDF 图片按 y 坐标插入，可能略偏离原书位置
- **代码缩进**：PDF 代码缩进按字体 x 坐标推导，极少数情况可能丢失
- **扫描版质量**：取决于 OCR 引擎，中文手写体识别率较低
- 如果是 EPUB 或文字版 PDF，直接进入 Step 2

### Step 2: 调用 book_split 切分章节

> **M2 安全提示**：`<book_slug>` 必须先清洗，禁止将书名直接拼入 shell 命令。  
> 生成方式：`book_slug = re.sub(r'[^\w\-]', '_', book_title)[:50]`  
> 含空格、引号、分号、反斜杠等特殊字符的标题务必做替换，否则可能造成命令注入。

```bash
cd <skill_dir>; uv run python scripts/book_split.py "<book_path>" -o <work_dir>/<book_slug> --strategy auto
```

读取生成的 `<work_dir>/<book_slug>/manifest.json`，得到章节列表。

**审视章节切分质量**：
- 章节数量是否合理（一本书通常 5-30 章）
- 章节标题是否有意义
- 如果切分不理想，重新调用 `--strategy heading` 或 `--strategy page_count --pages-per-chapter N`

**图片同步（必做）**：如果 `manifest.image_count > 0`，在派发 worker 之前，将图片从 work 目录复制到 vault：

```bash
# vault_images_dir = <vault>/books/<book_title>/images/
mkdir -p "<vault>/books/<book_title>/images"
cp -r "<manifest.images_dir>/." "<vault>/books/<book_title>/images/"
```

将 `vault_images_dir = <vault>/books/<book_title>/images/` 记录下来，传给 worker（见下方参数说明）。  
**不要把 manifest.images_dir（work 目录）传给 worker**，那个目录在 vault 之外，`./images/` 路径会失效。

### Step 3: 并发派发 worker subagent

**这是核心步骤**。读取 manifest.json 中的 chapters 列表，对每一章：

使用 `Task` 工具派发一个 worker subagent，prompt 模板见 `prompts/chapter-worker.md`。

**并发策略**：
- 一次性并发派发所有章节的 Task 调用（在同一个 message 里）
- 不在 skill 内设置固定并发上限（不要人为限流）
- 如果运行时出现 429 / 并发拒绝，只重试失败批次并保留已完成结果
- 每个 worker 只负责一章，互不依赖

**worker 职责**（verbatim 模式）：
1. 读取 `chapters_dir/ch_NNN.md`（原始文本，可能含 `[IMAGE:...]` 占位符）
2. 进行：OCR 错误修复（仅修错，不改写）、结构标记转换、图片占位符替换、双链标记、frontmatter 添加
3. **不生成摘要，不添加 callout，不改写原文措辞**
4. 直接写入 Obsidian vault：`<vault>/books/<book_title>/<NN>_<title>.md`
5. 图片引用使用标准 markdown：`![caption](./images/filename.png)`
6. 返回该章节提取的概念列表 + 标签（summary 字段固定为空字符串）

**传给每个 worker 的额外参数**（在 prompt 里注明）：
- `images_dir`: **vault 侧的图片目录**，即上面图片同步步骤写的 `vault_images_dir`（`<vault>/books/<book_title>/images/`）；图片不存在时传 `null`

详细 worker 指令见 [prompts/chapter-worker.md](prompts/chapter-worker.md)。

### Step 4: 收集 worker 结果

所有 worker 完成后，你会得到一个汇总：
```
{
  "chapter_1": { "concepts": [...], "tags": [...], "summary": "..." },
  "chapter_2": { ... },
  ...
}
```

### Step 5: 概念笔记生成（并发派发）

收集所有章节提取的概念，**去重**后：
- 对每个核心概念派发一个 Task subagent，使用 [prompts/concept-worker.md](prompts/concept-worker.md)
- 写入 `<vault>/concepts/<concept>.md`

**优化**：只对出现在 ≥2 章的概念生成独立笔记，避免噪声。

### Step 5.5: 空链接回填（必做）

为避免出现“可点击但没有内容”的 wikilink，在 Step 5 后必须执行一次回填：

```bash
cd <skill_dir>; uv run python scripts/fix_dangling_links.py "<vault_path>"
```

该步骤会：
- 扫描 `<vault_path>` 下所有 markdown 文件中的 `[[...]]` 链接
- 检查目标笔记是否存在
- 将不存在的链接目标自动补齐为 `<vault>/concepts/<概念名>.md` 占位页

占位页仅作为“防空链”兜底，不替代 Step 5 的高质量概念笔记。

### Step 6: 生成 MOC

派发一个 Task subagent，使用 [prompts/moc-worker.md](prompts/moc-worker.md)：
- 输入：所有章节的 summary + concepts + tags
- 输出：`<vault>/books/<book_title>/<book_title> MOC.md`

### Step 7: 汇报

向用户报告：
- 处理章节数 / 失败数
- 生成的概念笔记数
- 回填的占位概念页数
- 总耗时
- vault 中的入口文件路径

---

## 容错策略

- 任何 worker 失败 → 记录失败章节，**不要**阻塞其他 worker
- 汇报时列出失败章节，建议用户重新派发该章节的 Task
- 如果 PDF 提取异常（损坏文件），立即停止并报错

---

## 性能基准

- 一本 300 页中文书 ≈ 20 章 ≈ 20 个并发 worker
- 单章处理 ≈ 30-90 秒
- 整书处理 ≈ 2-5 分钟（依赖平台并发能力和模型速度）
- token 消耗 ≈ 400K-800K / 本

---

## 配套资源

- [prompts/chapter-worker.md](prompts/chapter-worker.md) — 章节 worker 完整指令
- [prompts/concept-worker.md](prompts/concept-worker.md) — 概念笔记 worker 指令
- [prompts/moc-worker.md](prompts/moc-worker.md) — MOC 生成 worker 指令
- [scripts/fix_dangling_links.py](scripts/fix_dangling_links.py) — 扫描并回填空链接目标
- [templates/chapter.md](templates/chapter.md) — 章节笔记模板
- [templates/concept.md](templates/concept.md) — 概念笔记模板
- [templates/moc.md](templates/moc.md) — MOC 模板

---

## 关键原则

1. **严格用 Task 派发 worker**，不要自己一章一章处理（浪费上下文）
2. **并发优先**：一次 message 派发多个 Task，最大化吞吐
3. **不做人为并发上限**：除非平台硬限制，不主动降并发
4. **必须消灭空链接**：每次完成后运行空链接回填
5. **worker 独立**：worker 之间不共享状态，所有协调在主 agent 完成
6. **不要修改 vault 中其他文件**：只在 `<vault>/books/<book_title>/` 和 `<vault>/concepts/` 写入
7. **失败可重试**：保留 manifest.json 和 chapters/ 原始数据，失败后可单独重派
