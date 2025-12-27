const PureUI = {
    // 主题切换
    theme: {
        init() {
            const saved = localStorage.getItem('p-theme');
            if (saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
        },
        toggle() {
            const current = document.documentElement.getAttribute('data-theme');
            const target = current === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', target);
            localStorage.setItem('p-theme', target);
        }
    },
    // Toast 提示
    toast(msg, type = 'info') {
        let el = document.getElementById('p-toast');
        if (!el) { el = document.createElement('div'); el.id = 'p-toast'; document.body.appendChild(el); }
        let icon = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
        el.innerHTML = `<span>${icon} ${msg}</span>`;
        el.classList.add('show');
        clearTimeout(window._toastTimer);
        window._toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
    },
    // 复制功能
    copy(text) {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).then(() => this.toast('已复制'));
        } else {
            const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
            try { document.execCommand('copy'); this.toast('已复制'); } catch(e){} document.body.removeChild(t);
        }
    },
    // 模态框
    modal: {
        open(id) { document.getElementById(id).classList.add('active'); },
        close(id) { document.getElementById(id).classList.remove('active'); }
    },
    // Tab 切换
    switchTab(btn, targetId, groupClass = 'p-tab-content') {
        btn.parentElement.querySelectorAll('.active').forEach(e => e.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.' + groupClass).forEach(e => e.style.display = 'none');
        document.getElementById(targetId).style.display = 'block';
    }
};
document.addEventListener('DOMContentLoaded', () => {
    PureUI.theme.init();
    document.querySelectorAll('.p-modal').forEach(m => m.addEventListener('click', e => { if(e.target === m) m.classList.remove('active'); }));
});