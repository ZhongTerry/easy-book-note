"""站点规则执行器：按已验收的声明式规则抽取章节内容。

规则是识别引擎的前置提示层：本模块保持纯函数（网络经注入的 fetcher），
产出与通用引擎同构的 payload；任何失败都返回 payload=None 交回上层，
由调用方决定落回通用逻辑。
"""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

# 下一页/下一章的文本提示（规则可覆盖 selector，判别语义固定）
_NEXT_PAGE_HINTS = ('下一页', '下页', '下頁')
_NEXT_CHAPTER_HINTS = ('下一章', '下章', '下一节', '下一節')
_PREV_CHAPTER_HINTS = ('上一章', '上章', '上一节', '上一節')
_MAX_PAGES = 8  # 与通用引擎的章节内分页上限对齐


def _clean_text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _chapter_token(url):
    """章节基准编号：/671/676855、/x/1/52994575.html、5882_2.html -> 676855/52994575/5882。

    分页链接（676855-2 / 5882_2）与正文链接（676855）共享该编号，
    这是区分"下一页"与"下一章"的关键。
    """
    path = str(url or '').split('?', 1)[0].split('#', 1)[0]
    m = re.search(r'/(\d+)(?:[-_]\d+)*(?:\.html?)?/?$', path)
    if m:
        return m.group(1)
    m = re.search(r'(\d+)(?:[-_]\d+)?\.html?$', path)
    return m.group(1) if m else ''


def _select_first(soup, selector):
    try:
        nodes = soup.select(selector)
    except Exception:
        return None
    return nodes[0] if nodes else None


def _extract_page(html, rule, base_url, trace):
    """单页抽取：返回页面级结果字典，未命中返回 None（原因写入 trace）。"""
    soup = BeautifulSoup(html, 'lxml')
    chapter_rule = rule.get('chapter') or {}

    selector = chapter_rule.get('content_selector') or ''
    container = _select_first(soup, selector) if selector else None
    if container is None:
        trace['reason'] = 'content_selector_missed'
        return None

    for noise in (chapter_rule.get('noise_selectors') or [])[:10]:
        try:
            for bad in container.select(noise):
                bad.decompose()
        except Exception:
            trace.setdefault('noise_errors', []).append(noise)

    p_sel = chapter_rule.get('paragraph_selector') or 'p'
    if p_sel == 'br-split':
        paras = [_clean_text(t) for t in container.get_text('\n').split('\n')]
    else:
        try:
            nodes = container.select(p_sel)
        except Exception:
            trace['reason'] = 'paragraph_selector_invalid'
            return None
        paras = [_clean_text(p.get_text()) for p in nodes]
    paras = [p for p in paras if p]
    trace['paragraphs'] = len(paras)
    if len(paras) < int(chapter_rule.get('min_paragraphs') or 5):
        trace['reason'] = 'below_min_paragraphs'
        return None

    title = ''
    title_sel = chapter_rule.get('title_selector')
    if title_sel:
        node = _select_first(soup, title_sel)
        if node is not None:
            title = _clean_text(node.get_text())

    # 导航判别：文本提示优先，href 是否含当前章节编号决定"下一页"还是"下一章"
    chapter_token = _chapter_token(base_url)
    nav_rule = rule.get('navigation') or {}
    found = {'prev': None, 'next': None, 'next_page': None}

    def hint_match(text, hints):
        text = _clean_text(text)
        return bool(text) and any(h in text for h in hints)

    for a in soup.find_all('a'):
        href = a.get('href') or ''
        if not href or href.startswith(('javascript:', '#', 'mailto:')):
            continue
        absolute = urljoin(base_url, href)
        if absolute == base_url:
            continue  # 指向自身的链接对导航没有意义
        text = _clean_text(a.get_text())
        same_chapter = bool(chapter_token) and chapter_token == _chapter_token(absolute)
        if urlsplit(absolute).netloc != urlsplit(base_url).netloc or urlsplit(absolute).scheme not in ('http', 'https'):
            continue
        if found['next_page'] is None and hint_match(text, _NEXT_PAGE_HINTS) and same_chapter:
            found['next_page'] = absolute
        elif found['next'] is None and hint_match(text, _NEXT_CHAPTER_HINTS) and not same_chapter:
            found['next'] = absolute
        elif found['prev'] is None and hint_match(text, _PREV_CHAPTER_HINTS) and not same_chapter:
            found['prev'] = absolute

    for rule_key, found_key, hints in (('next_page', 'next_page', _NEXT_PAGE_HINTS),
                                       ('next_chapter', 'next', _NEXT_CHAPTER_HINTS),
                                       ('prev_chapter', 'prev', _PREV_CHAPTER_HINTS)):
        if found[found_key] is not None:
            continue
        selector = (nav_rule.get(rule_key) or {}).get('selector')
        if not selector:
            continue
        node = _select_first(soup, selector)
        if node is None:
            continue
        href = node.get('href') or ''
        if not href or href.startswith(('javascript:', '#')):
            continue
        absolute = urljoin(base_url, href)
        same_chapter = bool(chapter_token) and chapter_token == _chapter_token(absolute)
        if urlsplit(absolute).netloc != urlsplit(base_url).netloc or urlsplit(absolute).scheme not in ('http', 'https'):
            continue
        if rule_key == 'next_page' and same_chapter:
            found[found_key] = absolute
        elif rule_key in ('next_chapter', 'prev_chapter') and not same_chapter:
            found[found_key] = absolute

    page = {
        'title': title,
        'paragraphs': paras,
        'prev': found['prev'],
        'next': found['next'],
        'next_page_url': found['next_page'],
        'toc_url': None,
    }

    toc_sel = (rule.get('toc') or {}).get('container_selector')
    if toc_sel:
        node = _select_first(soup, toc_sel)
        if node is not None and node.get('href'):
            page['toc_url'] = urljoin(base_url, node['href'])

    return page


def apply_rule(html, rule, base_url, trace=None):
    """单页规则抽取（不跟随分页），未命中返回 None，原因写入 trace。"""
    trace = trace if trace is not None else {}
    try:
        return _extract_page(html, rule, base_url, trace)
    except Exception as exc:  # 规则层数据不可信，任何异常都按未命中处理
        trace['reason'] = f'exception:{type(exc).__name__}'
        return None


def extract_chapter(html, rule, base_url, fetcher=None, max_pages=_MAX_PAGES):
    """完整章节抽取：单页规则 + 页内分页自动拼接。

    fetcher(url) -> html 由调用方注入（生产传 crawler 的取页方法，测试传
    文件加载器）；返回 None 或异常即停止翻页。
    返回 {'payload': dict|None, 'trace': dict}，payload 与通用引擎同构，
    rule_applied 留给调用方按 domain 盖章。
    """
    trace = {}
    first = apply_rule(html, rule, base_url, trace)
    if first is None:
        trace['stage'] = 'first_page'
        return {'payload': None, 'trace': trace}

    paragraphs = list(first['paragraphs'])
    current = first
    next_page_url = first['next_page_url']
    visited = {base_url}
    pages = 1

    while next_page_url and pages < max_pages:
        if next_page_url in visited:
            break
        visited.add(next_page_url)
        try:
            page_html = fetcher(next_page_url) if fetcher else None
        except Exception as exc:
            trace['pagination_error'] = f'{type(exc).__name__}'
            break
        if not page_html:
            break
        page = apply_rule(page_html, rule, next_page_url, trace)
        if page is None:
            trace['pagination_stopped_at'] = pages + 1
            break
        paragraphs.extend(page['paragraphs'])
        current = page
        next_page_url = page['next_page_url']
        pages += 1

    # A known remaining page means the chapter is incomplete: fall back rather
    # than caching a truncated chapter after a timeout, cycle or page limit.
    if next_page_url:
        trace['reason'] = 'incomplete_pagination'
        return {'payload': None, 'trace': trace}

    payload = {
        'title': current['title'] or first['title'],
        'content': paragraphs,
        # 上一章在首页，下一章/目录以末页为准（分页站点常只在末页给"下一章"）
        'prev': first['prev'],
        'next': current['next'] or first['next'],
        'toc_url': first['toc_url'] or current['toc_url'],
        'page_type': 'chapter',
    }
    trace['pages'] = pages
    trace['paragraphs'] = len(paragraphs)
    return {'payload': payload, 'trace': trace}


def validate_rule(html, rule, base_url, baseline_paras, baseline_title,
                  baseline_prev=None, baseline_next=None):
    """单页重放打分：与基线对比，0-1；基线为空返回 0（无从校验）。

    标题一致 0.2 / 段落数 ±10% 0.3 / 文本重合 0.4 / 导航结论一致 0.1。
    分页章节的完整验收用 validate_rule_chapter。
    """
    result = apply_rule(html, rule, base_url)
    if result is None:
        return 0.0
    return _score_against_baseline(result, baseline_paras, baseline_title,
                                   baseline_prev, baseline_next)


def validate_rule_chapter(html, rule, base_url, fetcher, baseline_paras,
                          baseline_title, baseline_prev=None, baseline_next=None):
    """完整章节验收：含分页合并，生成管线入库前的硬门槛。"""
    result = extract_chapter(html, rule, base_url, fetcher=fetcher)
    if result['payload'] is None:
        return 0.0
    return _score_against_baseline(result['payload'], baseline_paras, baseline_title,
                                   baseline_prev, baseline_next)


def _score_against_baseline(payload, baseline_paras, baseline_title,
                            baseline_prev, baseline_next):
    base = [_clean_text(p) for p in (baseline_paras or []) if _clean_text(p)]
    rule_paras = payload.get('content') or payload.get('paragraphs') or []
    if not base:
        return 0.0

    score = 0.0

    rule_title = _clean_text(payload.get('title'))
    base_title = _clean_text(baseline_title)
    if rule_title and rule_title == base_title:
        score += 0.2
    elif rule_title and (rule_title in base_title or base_title in rule_title):
        score += 0.1

    ratio = (len(rule_paras) / len(base)) if base else 0.0
    if 0.9 <= ratio <= 1.1:
        score += 0.3
    elif 0.7 <= ratio <= 1.3:
        score += 0.15

    if rule_paras:
        from difflib import SequenceMatcher
        base_set = set(base)
        exact = sum(1 for p in rule_paras if p in base_set) / len(rule_paras)
        sims = [
            max((SequenceMatcher(None, p[:120], b[:120]).ratio() for b in base), default=0.0)
            for p in rule_paras
        ]
        score += 0.3 * exact + 0.1 * (sum(sims) / len(sims))

    if payload.get('next') == baseline_next and payload.get('prev') == baseline_prev:
        score += 0.1

    return round(min(1.0, score), 3)
