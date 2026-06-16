"""StructuralChunker —— 基于一套**已在实战中验证**的切分配方，做了通用化。

该配方的关键经验（实测高覆盖率、好用）：
  1. **先过滤噪声**：目录行（点线/页码 "- 79 -"）、OCR 占位（【图片文字整理 N】）。
     原注释：这是检索噪声的**头号来源**——TOC 行会冒充标题、抢占 top-k。
  2. **标题检测分节**：Part\\d+ / 第X节章 / 【…】 / "N分钟" / Markdown #。
  3. **大小上限累积**：缓冲段落，遇标题或超过 ~900 字就切，且**不跨标题**。
  4. **元数据标注**：kind(dialogue/template/theory/case_analysis/notes) + keywords，
     作为检索信号（写进 Chunk.meta）。
  5. 阅读顺序由 order 保留（建图时再连 NEXT）。

通用化：原配方把"已知标题短语/概念表/敏感词"硬编码进领域。这里只保留**结构性**
启发式，领域相关项（自定义标题正则、敏感词）通过参数注入，缺省不带领域知识。
"""
from __future__ import annotations

import re

from ..builder.interfaces import Chunker
from ..models import Chunk

# ---- 噪声过滤（通用） ----
_TOC_DOTS = re.compile(r"[.．·•・]{4,}")                 # 点线目录
_TOC_PAGE_TAIL = re.compile(r"\s-\s*\d+\s*-\s*$")        # 行尾 "- 79 -"
_TOC_PAGE_ONLY = re.compile(r"^-\s*\d+\s*-$")            # 纯 "- 12 -"
_OCR_PLACEHOLDER = re.compile(r"^【图片文字整理\s*\d+】$")

# ---- 标题检测（结构性、通用） ----
_HEADING_PATTERNS = [
    re.compile(r"^#{1,6}\s+\S"),                                   # Markdown
    re.compile(r"^Part\s*\d+", re.IGNORECASE),
    re.compile(r"^第[一二三四五六七八九十百零\d]+[章节回讲部篇]"),
    re.compile(r"^Chapter\s*\d+", re.IGNORECASE),
    re.compile(r"^\d+(\.\d+)*\s*[、.．]\s*\S"),                     # "1.2 标题" / "3、标题"
    re.compile(r"^【.+】$"),
    re.compile(r"^\d+\s*分钟"),
]


def _is_noise(line: str) -> bool:
    return bool(_TOC_DOTS.search(line) or _TOC_PAGE_TAIL.search(line)
               or _TOC_PAGE_ONLY.match(line) or _OCR_PLACEHOLDER.match(line))


def _detect_kind(text: str) -> str:
    if re.search(r"[男女][:：]|[FA][:：]", text):
        return "dialogue"
    if re.search(r"模板|话术", text):
        return "template"
    if re.search(r"【讲解】|讲解[:：]|案例", text):
        return "case_analysis"
    if re.search(r"规则|流程|阶段|框架|逻辑|原则|方法", text):
        return "theory"
    return "notes"


def _pack_sentences(line: str, max_chars: int) -> list[str]:
    """把超长单行按句末标点拆成句子，再贪心打包成 ≤max_chars 的片段。

    原配方只按行切，遇到无换行的长段落（常见于 OCR）会超限——这里补上。
    """
    if len(line) <= max_chars:
        return [line]
    sentences = re.findall(r"[^。！？!?]*[。！？!?]|[^。！？!?]+", line)
    pieces, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) > max_chars:
            pieces.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        pieces.append(cur)
    return pieces or [line]


def _keywords(text: str, limit: int = 18) -> list[str]:
    stop = {"这个", "那个", "就是", "因为", "所以", "但是", "然后", "如果",
            "一个", "什么", "知道", "感觉", "我们", "他们", "自己"}
    seen, out = set(), []
    for m in re.findall(r"[一-龥]{2,6}", text):
        if m in stop or m in seen:
            continue
        seen.add(m)
        out.append(m)
        if len(out) >= limit:
            break
    return out


class StructuralChunker(Chunker):
    def __init__(self, max_chars: int = 900, min_chars: int = 1,
                 extra_heading_patterns: list[str] | None = None,
                 is_heading=None):
        """
        Args:
            max_chars: 单 chunk 软上限（原配方用 900）。超过即切，且不跨标题。
            extra_heading_patterns: 领域自定义标题正则（如已知小节标题短语）。
            is_heading: 完全自定义的标题判定函数 (line)->bool，覆盖默认。
        """
        self._max = max_chars
        self._min = min_chars
        self._patterns = list(_HEADING_PATTERNS)
        for p in (extra_heading_patterns or []):
            self._patterns.append(re.compile(p))
        self._custom_is_heading = is_heading

    def _is_heading(self, line: str) -> bool:
        if self._custom_is_heading is not None:
            return bool(self._custom_is_heading(line))
        # 结构性正则
        if any(p.search(line) for p in self._patterns):
            return True
        # 启发式：很短、无句末标点的独立行，多半是小标题
        if len(line) <= 18 and not re.search(r"[。！？!?，,：；…]$", line) \
                and not re.search(r"[:：]", line):
            return True
        return False

    async def split(self, doc_text: str, doc_id: str) -> list[Chunk]:
        lines = [ln.strip() for ln in re.split(r"\r?\n", doc_text) if ln.strip()]

        chunks: list[Chunk] = []
        section_title = "文档开头"
        order = 0
        buffer: list[str] = []

        def flush() -> None:
            nonlocal order, buffer
            text = "\n".join(buffer).strip()
            if len(text) < self._min:
                buffer = []
                return
            full = f"{section_title}\n{text}"
            chunks.append(Chunk(
                id=f"chunk_{order:05d}", text=text, section=section_title,
                order=order, doc_id=doc_id,
                meta={"kind": _detect_kind(full),
                      "keywords": _keywords(full),
                      "char_length": len(text)},
            ))
            order += 1
            buffer = []

        for raw in lines:
            if _is_noise(raw):
                continue
            heading = self._is_heading(raw)
            # 非标题的超长单行先按句子拆开，保证不超过 max
            sub_lines = [raw] if heading else _pack_sentences(raw, self._max)
            for line in sub_lines:
                too_long = len("\n".join(buffer)) + len(line) > self._max
                if heading or too_long:
                    flush()
                if heading:
                    section_title = line.lstrip("#").strip()
                else:
                    buffer.append(line)
        flush()
        return chunks
