"""AI 解析规则生成管线：样例抓取 -> DOM 大纲 -> LLM -> 静态校验 -> 自动验收 -> 入库。

生成 ≠ 生效：规则必须通过自动验收（与通用引擎基线对比，或通用引擎失败时的
跨章节自洽检查）才会激活。全程频控，LLM 配置留空时功能整体关闭。
"""
import json
import os
import re
import time
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from shared import debug, error, info, warn
from services import site_rules
from recognition.rule_engine import (
    apply_rule,
    extract_chapter,
    validate_rule_chapter,
)

_BASE = os.environ.get('RULE_LLM_BASE_URL', '').rstrip('/')
_API_KEY = os.environ.get('RULE_LLM_API_KEY', '')
_MODEL = os.environ.get('RULE_LLM_MODEL', 'DeepSeek-V4-Flash')
_TIMEOUT = float(os.environ.get('RULE_LLM_TIMEOUT', '120'))
_AUTO_TRIGGER = os.environ.get('RULE_AUTO_TRIGGER', '0') == '1'
_DAILY_LIMIT = int(os.environ.get('RULE_DAILY_LIMIT', '10'))
_DOMAIN_COOLDOWN_SECONDS = 86400

_OUTLINE_MAX_CHARS = 30000
_ACTIVE_MIN_SCORE = 0.85
_STORED_MIN_SCORE = 0.6

PROMPT_SYSTEM = '''你是网页结构分析器。给定一个小说章节页面的 DOM 大纲（标签路径+属性+文本摘要），
请产出一份**结构化解析规则 JSON**，供后续程序用 CSS 选择器在该站点同类页面上抽取内容。

只允许声明式规则（CSS 选择器 / 正则 / 文本匹配），不允许任何代码。
严格只输出一个 JSON 对象，不要 markdown 代码块，不要解释。

规则 schema：
{
  "chapter": {
    "content_selector": "正文容器的 CSS 选择器（选最稳定、最贴近正文的那个）",
    "paragraph_selector": "正文段落的 CSS 选择器；若段落是文本节点用 <br> 分隔则填 \\"br-split\\"",
    "noise_selectors": ["需要从正文中剔除的干扰元素选择器，如推荐卡片、广告、\\"猜你喜欢\\"等"],
    "title_selector": "章节标题选择器",
    "min_paragraphs": 5
  },
  "navigation": {
    "next_chapter": {"selector": "下一章链接选择器", "text_hints": ["下一章"]},
    "prev_chapter": {"selector": "上一章链接选择器", "text_hints": ["上一章"]},
    "next_page": {"selector": "章节内分页的下一页选择器", "text_hints": ["下一页"]}
  },
  "toc": {
    "link_pattern": "目录页章节链接 href 的正则",
    "container_selector": "目录入口链接选择器"
  },
  "confidence": 0.0到1.0,
  "notes": "一句话说明你观察到的页面结构特点"
}

要求：
1. content_selector 必须避开推荐/广告区域；正文与干扰内容混在同一容器时用 noise_selectors 剔除。
2. 选择器优先用 id 和语义 class，避免依赖动态生成的路径序号。
3. 不确定的地方 confidence 打低分并在 notes 说明。'''


class RuleGenerationError(Exception):
    """生成失败（含原因），任务层直接作为错误信息展示。"""


def is_configured():
    return bool(_BASE and _API_KEY)


# ---------- DOM 大纲 ----------

def _dom_outline(html, max_chars=_OUTLINE_MAX_CHARS):
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup(['script', 'style', 'noscript', 'svg', 'link', 'meta']):
        tag.decompose()
    lines = []

    def walk(node, path):
        if sum(len(line) + 1 for line in lines) > max_chars:
            return
        if node.name is None:
            text = re.sub(r'\s+', ' ', node.string or '').strip()
            if text:
                lines.append(f'{path} TEXT: {text[:80]}')
            return
        cls = '.'.join(sorted(node.get('class') or []))
        ident = '#' + node['id'] if node.get('id') else ''
        here = f'{path}>{node.name}{("." + cls) if cls else ""}{ident}'
        if node.name in ('a', 'p', 'div', 'h1', 'h2', 'h3', 'dd', 'dt', 'li', 'span'):
            own = ''.join(node.find_all(string=True, recursive=False))
            own = re.sub(r'\s+', ' ', own).strip()
        else:
            own = ''
        attrs = f' href={node.get("href", "")[:60]}' if node.name == 'a' else ''
        if own:
            lines.append(f'{here}{attrs} TEXT: {own[:100]}')
        else:
            lines.append(f'{here}{attrs} [{len(node.find_all(True, recursive=False))} children]')
        for child in node.children:
            walk(child, here)

    walk(soup.body or soup, '')
    return '\n'.join(lines)


# ---------- LLM ----------

def _call_llm(outline):
    if not _BASE or not _API_KEY:
        raise RuleGenerationError('未配置 RULE_LLM_BASE_URL / RULE_LLM_API_KEY，规则生成功能关闭')
    messages = [
        {'role': 'system', 'content': PROMPT_SYSTEM},
        {'role': 'user', 'content': 'DOM 大纲：\n' + outline},
    ]
    last_error = None
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f'{_BASE}/chat/completions',
                json={'model': _MODEL, 'messages': messages,
                      'temperature': 0.2, 'max_tokens': 2000},
                headers={'Authorization': f'Bearer {_API_KEY}',
                         'Content-Type': 'application/json'},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content']
            match = re.search(r'\{[\s\S]*\}', content)
            if not match:
                raise ValueError('响应中不含 JSON 对象')
            return json.loads(match.group(0))
        except RuleGenerationError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == 1:
                messages = messages + [
                    {'role': 'assistant', 'content': '...'},
                    {'role': 'user', 'content': f'上一次输出解析失败（{exc}）。请严格只输出一个合法 JSON 对象。'},
                ]
    raise RuleGenerationError(f'LLM 调用/解析失败: {last_error}')


# ---------- 静态校验 ----------

_SELECTOR_MAX_LEN = 300


def _validate_selectors(rule):
    """返回问题列表；非空则拒绝入库。规则是数据，只放行可安全执行的选择器。"""
    problems = []
    chapter = rule.get('chapter') or {}
    if not isinstance(chapter, dict) or not chapter.get('content_selector'):
        problems.append('缺少 chapter.content_selector')
        return problems

    def check(selector, where):
        if not isinstance(selector, str) or not selector.strip():
            return
        if len(selector) > _SELECTOR_MAX_LEN:
            problems.append(f'{where} 选择器超长')
        if 'javascript:' in selector.lower():
            problems.append(f'{where} 含 javascript: 协议')
        soup = BeautifulSoup('<div id="probe"></div>', 'lxml')
        try:
            soup.select(selector)
        except Exception as exc:
            problems.append(f'{where} 选择器语法错误: {exc}')

    check(chapter.get('content_selector'), 'content_selector')
    if chapter.get('paragraph_selector') != 'br-split':
        check(chapter.get('paragraph_selector') or 'p', 'paragraph_selector')
    check(chapter.get('title_selector') or 'h1', 'title_selector')
    noise = chapter.get('noise_selectors') or []
    if not isinstance(noise, list):
        problems.append('noise_selectors 必须是数组')
    elif len(noise) > 10:
        problems.append('noise_selectors 超过 10 条')
    else:
        for sel in noise:
            check(sel, 'noise_selector')
    for key in ('next_chapter', 'prev_chapter', 'next_page'):
        check((rule.get('navigation') or {}).get(key, {}).get('selector'), key)
    check((rule.get('toc') or {}).get('container_selector'), 'toc.container_selector')
    return problems


# ---------- 验收 ----------

_BAD_TITLE = re.compile(r'404|not found|无法访问|错误', re.IGNORECASE)


def _score_with_baseline(crawler, html, rule, sample_url, fetcher):
    """通用引擎能产出基线时：逐项对比（最强验收）。返回 (score, baseline_info)。"""
    baseline_payload = crawler._general_run_logic(sample_url)
    baseline_paras = (baseline_payload or {}).get('content') or []
    baseline_title = (baseline_payload or {}).get('title') or ''
    from recognition import get_payload_issue
    if not baseline_paras or get_payload_issue(baseline_payload):
        return None, {'baseline': 'empty'}
    score = validate_rule_chapter(
        html, rule, sample_url, fetcher=fetcher,
        baseline_paras=baseline_paras, baseline_title=baseline_title,
        baseline_prev=(baseline_payload or {}).get('prev'),
        baseline_next=(baseline_payload or {}).get('next'),
    )
    return score, {'baseline': 'generic', 'baseline_paras': len(baseline_paras)}


def _score_cross_page(crawler, html, rule, sample_url, fetcher):
    """通用引擎失败时：独立质量 + 跨章节自洽（换一章用同一规则重放）。"""
    result = extract_chapter(html, rule, sample_url, fetcher=fetcher)
    payload = result['payload']
    trace = result['trace']
    if payload is None:
        return 0.0, {'mode': 'cross_page', 'reason': trace.get('reason') or trace.get('detail')}

    quality = 0.0
    title = (payload.get('title') or '').strip()
    if title and not _BAD_TITLE.search(title):
        quality += 0.2
    if sum(len(p) for p in payload['content']) >= 500:
        quality += 0.2
    if payload.get('next') or payload.get('prev'):
        quality += 0.1

    consistency = 0.0
    second_url = payload.get('next')
    if second_url and second_url != sample_url and site_rules.domain_of(second_url) == site_rules.domain_of(sample_url):
        try:
            second_html = fetcher(second_url)
        except Exception:
            second_html = None
        if second_html:
            second = apply_rule(second_html, rule, second_url)
            first_page_count = trace.get('paragraphs', 0)
            if (second is not None and second['paragraphs']
                    and second.get('title') and second['title'] != title
                    and second['paragraphs'] != payload['content']):
                consistency += 0.3
                ratio_ok = (
                    first_page_count
                    and 0.6 <= len(second['paragraphs']) / max(1, first_page_count) <= 1.4
                )
                if ratio_ok:
                    consistency += 0.2

    score = round(quality + consistency, 3)
    return score, {'mode': 'cross_page', 'quality': quality, 'consistency': consistency}


# ---------- 生成入口 ----------

def generate_rule(sample_url, crawler, username=None, callback=None, auto=False, **_ignored):
    """task_manager 任务入口：全程捕获异常，返回结果摘要。"""

    def report(progress, msg):
        if callback:
            try:
                callback(progress=progress, msg=msg)
            except Exception:
                pass

    try:
        domain = site_rules.domain_of(sample_url)
        if not domain:
            raise RuleGenerationError('URL 无法解析出域名')
        from shared import is_safe_url
        if urlsplit(sample_url).scheme not in ('http', 'https') or not is_safe_url(sample_url):
            raise RuleGenerationError('URL 未通过安全校验')
        if not auto and not _reserve_generation(domain, enforce_domain_cooldown=False):
            raise RuleGenerationError('今日生成次数已达上限（每域名冷却仅约束自动触发）')

        report(5, '抓取样例页')
        fetcher = lambda url: site_rules.fetch_rule_page(url, crawler, sample_url)
        html = fetcher(sample_url)
        if not html:
            raise RuleGenerationError('样例页抓取失败（源站无响应或被拦截）')

        report(20, '构建 DOM 大纲')
        outline = _dom_outline(html)

        report(40, '调用模型生成规则')
        rule = _call_llm(outline)

        problems = _validate_selectors(rule)
        if problems:
            raise RuleGenerationError('规则静态校验未通过: ' + '; '.join(problems[:3]))

        report(65, '自动验收：与通用引擎对比')
        score, acceptance = _score_with_baseline(crawler, html, rule, sample_url, fetcher)
        if score is None:
            report(70, '通用引擎无基线，改用跨章节自洽验收')
            score, acceptance = _score_cross_page(crawler, html, rule, sample_url, fetcher)

        if score < _STORED_MIN_SCORE:
            raise RuleGenerationError(
                f'自动验收未通过（score={score}），规则已丢弃: {acceptance}')

        status = 'active' if score >= _ACTIVE_MIN_SCORE else 'unverified'
        report(90, '入库')
        site_rules.upsert_rule(
            domain, rule, accept_score=score,
            confidence=rule.get('confidence'), sample_url=sample_url, status=status,
        )
        payload = extract_chapter(html, rule, sample_url, fetcher=fetcher)['payload']
        summary = {
            'status': status, 'domain': domain, 'score': score,
            'acceptance': acceptance,
            'paragraphs': len(payload['content']) if payload else 0,
            'title': (payload or {}).get('title', ''),
        }
        info("RuleGen", f"规则生成完成: {domain} score={score} status={status}")
        return summary
    except RuleGenerationError as exc:
        error("RuleGen", f"生成失败: {exc}")
        return {'status': 'error', 'message': str(exc)}
    except Exception as exc:
        error("RuleGen", f"生成异常: {type(exc).__name__}: {exc}")
        return {'status': 'error', 'message': f'{type(exc).__name__}: {exc}'}


# ---------- 自动触发（默认关闭） ----------


def _reserve_generation(domain, enforce_domain_cooldown):
    """跨 worker 共享的 SQLite 配额。

    全局每日上限对手动与自动一律生效（成本上限）；每域名 24h 冷却只约束
    自动触发——手动点击是明确意图（站点改版修复后立即重新生成），不受冷却。
    """
    import managers
    now = time.time()
    today = time.strftime('%Y-%m-%d')
    with closing(sqlite3.connect(managers.DB_PATH)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS rule_generation_attempts '
                     '(domain TEXT PRIMARY KEY, attempted_at REAL, day TEXT)')
        if enforce_domain_cooldown:
            row = conn.execute('SELECT attempted_at FROM rule_generation_attempts WHERE domain=?', (domain,)).fetchone()
            if row and now - row[0] < _DOMAIN_COOLDOWN_SECONDS:
                return False
        count = conn.execute('SELECT count(*) FROM rule_generation_attempts WHERE day=?', (today,)).fetchone()[0]
        if count >= _DAILY_LIMIT:
            return False
        conn.execute('INSERT OR REPLACE INTO rule_generation_attempts VALUES (?, ?, ?)', (domain, now, today))
    return True


def maybe_autogenerate(source_url, submit_fn):
    """读失败路径的自动生成钩子（RULE_AUTO_TRIGGER 开启时）。

    频控统一由 _reserve_generation 的 SQLite 配额执行：每域名 24h 冷却 +
    全局每日上限；这里只做"已有 active 规则就跳过"的快速判断。
    """
    if not _AUTO_TRIGGER:
        return None
    domain = site_rules.domain_of(source_url)
    if not domain or site_rules.get_active_rule(domain):
        return None
    if not _reserve_generation(domain, enforce_domain_cooldown=True):
        debug("RuleGen", f"自动生成被配额拦截: {domain}")
        return None

    warn("RuleGen", f"自动触发规则生成: {domain}")
    from spider_core import crawler_instance
    return submit_fn(generate_rule, source_url, crawler_instance, auto=True)
