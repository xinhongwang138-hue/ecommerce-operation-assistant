"""把上传的文件变成纯文本。

只支持四种：`.txt` / `.md` / `.docx` / `.pdf`。

**不支持扫描件。** PDF 里的图片文字要靠 OCR，本项目接了图片识别但那是另一个链路；
把扫描件当"抽取失败"直接报出来，比抽出一堆乱码然后拿去打分诚实得多——
乱码打出来的分数是假的，而假的分数会被当成真的用。
"""

from __future__ import annotations

import io
import zipfile

# 超过这个体积直接拒绝。一份合同正文很少超过 2MB，超了通常是扫描件或图片塞进了 docx。
MAX_BYTES = 8 * 1024 * 1024

# 抽出来的文本短于这个长度就认为没抽到东西。空白模板、只有页眉的扫描件都会落到这里。
MIN_TEXT_CHARS = 40

SUPPORTED_SUFFIXES = (".txt", ".md", ".docx", ".pdf")


class UnsupportedFormat(Exception):
    """格式不支持，或文件本身不是有效的该格式。"""


class EmptyText(Exception):
    """文件能打开，但里面没有可用的文字。"""


def suffix_of(filename: str) -> str:
    lowered = (filename or "").strip().lower()
    for suffix in SUPPORTED_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    return ""


def extract(filename: str, data: bytes) -> str:
    """按后缀分发。不做内容嗅探——后缀和内容不一致时，按后缀报错更容易查。"""
    if not data:
        raise EmptyText("文件是空的")
    if len(data) > MAX_BYTES:
        raise UnsupportedFormat(f"文件超过 {MAX_BYTES // 1024 // 1024}MB，请只上传正文")

    suffix = suffix_of(filename)
    if not suffix:
        raise UnsupportedFormat("只支持 .txt / .md / .docx / .pdf")

    if suffix in (".txt", ".md"):
        text = _decode(data)
    elif suffix == ".docx":
        text = _from_docx(data)
    else:
        text = _from_pdf(data)

    cleaned = normalize(text)
    if len(cleaned) < MIN_TEXT_CHARS:
        raise EmptyText(
            "没有抽到可用的文字。如果是扫描件或图片版 PDF，需要先 OCR 再上传。"
        )
    return cleaned


def _decode(data: bytes) -> str:
    """中文合同的 txt 一半是 GBK。先 UTF-8，失败再 GB18030（GBK 的超集）。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise EmptyText("文本编码无法识别（试过 UTF-8 与 GB18030）")


def _from_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给一句能查的话
        raise UnsupportedFormat("服务端缺少 python-docx，无法解析 .docx") from exc

    try:
        document = Document(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise UnsupportedFormat("这个 .docx 打不开，可能实际是 .doc 或已损坏") from exc

    lines = [paragraph.text for paragraph in document.paragraphs]
    # 表格里的条款常常是正文的一部分（付款节点表、验收标准表），漏了就等于漏了条款
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedFormat("服务端缺少 pypdf，无法解析 .pdf") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf 的异常类型不稳定，统一按"打不开"处理
        raise UnsupportedFormat(f"这个 PDF 打不开：{exc}") from exc

    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # 单页抽不出来不该让整份合同失败
            pages.append(f"[第 {index} 页抽取失败]")
    return "\n".join(pages)


def normalize(text: str) -> str:
    """统一换行、压掉多余空行与行尾空格。

    只做这些。不去页眉页脚、不猜段落——任何"顺手清洗"都可能把条款原文改掉，
    而条款原文是要拿去做审批依据的。
    """
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line.strip():
            # 连续空行压成一个：既保留段落感，也不让条款切分被空行洪水干扰
            if out and out[-1] == "":
                continue
            out.append("")
            continue
        out.append(line)
    return "\n".join(out).strip()
