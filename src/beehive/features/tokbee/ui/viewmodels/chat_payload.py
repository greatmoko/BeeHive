"""发送附件落盘、读取和历史消息转换，保留多模态与文档兜底规则。"""
import logging
from pathlib import Path
from beehive.features.tokbee.core.file_reader import is_image, is_document, persist_attachment, read_image_as_base64, read_file_as_text

logger = logging.getLogger("tokbee")


def prepare_attachments(paths, sid, text, on_error):
    # 构建 API 消息内容（可能含多模态）
    api_parts: list[dict] = []
    doc_prefix = ""
    saved_attachments: list[dict] = []

    for fp in paths:
        try:
            stored = persist_attachment(fp, sid)
            if is_image(stored):
                b64, mime = read_image_as_base64(stored)
                api_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
                saved_attachments.append({
                    "path": stored,
                    "name": Path(fp).name,
                    "kind": "image",
                })
            elif is_document(stored):
                extracted = read_file_as_text(stored)
                doc_prefix += f"--- {Path(fp).name} ---\n{extracted}\n---\n\n"
                saved_attachments.append({
                    "path": stored,
                    "name": Path(fp).name,
                    "kind": "doc",
                })
        except Exception as exc:
            on_error(f"读取文件失败: {Path(fp).name}\n{exc}")
            logger.error("读取附件 %s 失败: %s", fp, exc)


    full_text = doc_prefix + text if doc_prefix else text

    has_images = any(p.get("type") == "image_url" for p in api_parts)
    if has_images:
        if full_text:
            api_parts.insert(0, {"type": "text", "text": full_text})
        api_user_content = api_parts
    else:
        api_user_content = full_text


    return api_user_content, saved_attachments


def history_to_api_messages(hist: list[dict]) -> list[dict]:
    api_messages: list[dict] = []
    for m in hist:
        atts = m.get("attachments") or []
        img_parts = []
        doc_parts = []
        for att in atts:
            path = att.get("path") if isinstance(att, dict) else str(att)
            if not path:
                continue
            if is_image(path) and Path(path).is_file():
                try:
                    b64, mime = read_image_as_base64(path)
                    img_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
                except Exception:
                    pass
            elif is_document(path) and Path(path).is_file():
                # 历史条目的文档文本发送时未落盘，这里按路径重新提取（A4）
                try:
                    txt = read_file_as_text(path)
                except Exception:
                    txt = ""
                if txt.strip():
                    doc_parts.append(txt)
        content = m.get("content") or ""
        if doc_parts:
            doc_text = "\n\n".join(doc_parts)
            content = (content + "\n\n" if content else "") + doc_text
        # 有附件但文本为空时给非空兜底，避免 content:"" 触发部分端点 400（A4）
        if not content.strip() and (img_parts or atts):
            content = "（附件）"
        if img_parts:
            parts = []
            if content.strip():
                parts.append({"type": "text", "text": content})
            parts.extend(img_parts)
            api_messages.append({"role": m["role"], "content": parts})
        else:
            api_messages.append({"role": m["role"], "content": content})
    return api_messages
