from typing import Any


def get_payload_issue(payload: dict[str, Any] | None) -> dict[str, str] | None:
    """Return a user-safe reason when a crawler payload must not be consumed."""
    if not isinstance(payload, dict):
        return {
            'code': 'FETCH_FAILED',
            'message': '未能获取源站内容，请稍后重试。',
        }

    recognition = payload.get('recognition')
    recognition = recognition if isinstance(recognition, dict) else {}
    page_type = payload.get('page_type') or recognition.get('page_type') or ''
    warnings = recognition.get('warnings') if isinstance(recognition.get('warnings'), list) else []
    if 'source_cooldown' in warnings:
        return {
            'code': 'SOURCE_COOLDOWN',
            'message': '该书源刚刚连续失败，已暂缓请求，请稍后重试或更换书源。',
        }
    if page_type == 'blocked':
        return {
            'code': 'SOURCE_CHALLENGE',
            'message': '源站要求完成验证，当前无法安全读取正文。',
        }

    content = payload.get('content')
    chapters = payload.get('chapters')
    has_content = isinstance(content, list) and any(str(line).strip() for line in content)
    has_chapters = isinstance(chapters, list) and bool(chapters)
    if page_type == 'chapter' and not has_content:
        return {
            'code': 'EMPTY_CHAPTER',
            'message': '未识别到可靠正文，请刷新或更换书源。',
        }
    if page_type == 'toc' and not has_chapters:
        return {
            'code': 'EMPTY_TOC',
            'message': '未识别到可靠目录，请刷新或更换书源。',
        }
    if page_type == 'unknown' and not (has_content or has_chapters):
        return {
            'code': 'UNRECOGNIZED_PAGE',
            'message': '页面类型无法确认，未保存本次结果。',
        }
    return None


def is_cacheable_payload(payload: dict[str, Any] | None) -> bool:
    return get_payload_issue(payload) is None
