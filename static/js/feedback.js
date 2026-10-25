/* 阅读页一键质量反馈：提交 → 轮询生成任务 → 新规则生效后引导强制重读。 */
(function () {
    'use strict';
    let inFlight = false;

    async function submitFeedback(opts) {
        if (inFlight) return;
        const params = new URLSearchParams(window.location.search);
        const url = (opts && opts.url) || params.get('url');
        const key = (opts && opts.key) || params.get('key') || '';
        if (!url) { alert('未找到章节地址'); return; }
        if (!confirm('反馈此页的质量问题？系统将抓取本页并用 AI 重新生成该站的解析规则。')) return;
        inFlight = true;
        try {
            const resp = await fetch('/api/feedback', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url, key: key }),
            });
            const json = await resp.json();
            if (json.status !== 'success') {
                alert('提交失败: ' + (json.msg || resp.status));
                return;
            }
            for (let i = 0; i < 80; i++) {
                await new Promise((resolve) => setTimeout(resolve, 3000));
                const check = await (await fetch('/api/rules/task/' + json.task_id)).json();
                const task = check.task || {};
                if (task.status === 'completed') {
                    const result = task.result || {};
                    if (result.status === 'active') {
                        if (confirm('✅ 新解析规则已生效（得分 ' + result.score +
                            '，已清理旧缓存 ' + (result.cache_purged || 0) +
                            ' 条）。立即强制重读本页查看效果？')) {
                            const u = new URL(window.location.href);
                            u.searchParams.set('force', 'true');
                            window.location.href = u.toString();
                        }
                    } else {
                        alert('⚠️ ' + (result.message || ('规则未激活: score=' + result.score)));
                    }
                    return;
                }
                if (task.status === 'failed') {
                    alert('❌ 生成失败: ' + (task.error || '未知原因'));
                    return;
                }
            }
            alert('仍在生成中，稍后可再次尝试');
        } catch (err) {
            alert('请求异常: ' + err);
        } finally {
            inFlight = false;
        }
    }

    window.submitFeedback = submitFeedback;
})();
