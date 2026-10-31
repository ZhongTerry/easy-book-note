/* --- START OF FILE pure2.1.js --- */

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
    // 🚀 全局加载动画
    loading: {
        show(msg = '加载中...') {
            let el = document.getElementById('p-loading');
            if (!el) {
                el = document.createElement('div');
                el.id = 'p-loading';
                el.innerHTML = `
                    <div class="p-loading-backdrop">
                        <div class="p-loading-spinner">
                            <div class="spinner"></div>
                            <div class="loading-text">${msg}</div>
                        </div>
                    </div>
                `;
                document.body.appendChild(el);
            }
            el.querySelector('.loading-text').innerText = msg;
            el.style.display = 'flex';
        },
        hide() {
            const el = document.getElementById('p-loading');
            if (el) el.style.display = 'none';
        }
    },
    // 🚀 模态框优化版
    modal: {
        open(id) { 
            const el = document.getElementById(id);
            if (el) {
                el.classList.add('active'); 
                // 锁定背景滚动，防止穿透
                document.body.style.overflow = 'hidden';
            }
        },
        close(id) { 
            const el = document.getElementById(id);
            if (el) {
                el.classList.remove('active'); 
                // 恢复背景滚动
                document.body.style.overflow = '';
            }
        }
    },
    // Tab 切换
    switchTab(btn, targetId, groupClass = 'p-tab-content') {
        btn.parentElement.querySelectorAll('.active').forEach(e => e.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.' + groupClass).forEach(e => e.style.display = 'none');
        document.getElementById(targetId).style.display = 'block';
    },
    // 下拉框组件
    dropdown: {
        toggle(id) {
            document.querySelectorAll('.p-dropdown-container').forEach(el => {
                if (el.id !== id) el.classList.remove('active');
            });
            const el = document.getElementById(id);
            if (el) el.classList.toggle('active');
        },
        select(containerId, value, text) {
            const container = document.getElementById(containerId);
            if (!container) return;

            const input = container.querySelector('input[type="hidden"]');
            if (input) {
                input.value = value;
                // 手动触发 change 事件，以便 Vue 或其他监听器能捕获
                const event = new Event('change', { bubbles: true });
                input.dispatchEvent(event);
            }

            const triggerText = container.querySelector('.p-dropdown-trigger span');
            if (triggerText) triggerText.innerText = text;

            container.querySelectorAll('.p-dropdown-item').forEach(item => {
                item.classList.remove('selected');
                if (item.innerText.includes(text)) item.classList.add('selected');
            });

            container.classList.remove('active');
            if(window.event) window.event.stopPropagation();
        },
        init() {
            document.addEventListener('click', (e) => {
                if (!e.target.closest('.p-dropdown-container')) {
                    document.querySelectorAll('.p-dropdown-container').forEach(el => {
                        el.classList.remove('active');
                    });
                }
            });
        }
    }
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    PureUI.theme.init();
    PureUI.dropdown.init();
    
    // 模态框全局行为
    document.querySelectorAll('.p-modal').forEach(m => {
        // 1. 点击背景关闭
        m.addEventListener('click', e => { 
            if(e.target === m) PureUI.modal.close(m.id); 
        });

        // 2. 自动注入关闭按钮 (如果内容里没有的话)
        const content = m.querySelector('.p-modal-content');
        if (content && !content.querySelector('.p-modal-close-btn, [data-modal-close]')) {
            const closeBtn = document.createElement('button');
            closeBtn.className = 'p-modal-close-btn';
            closeBtn.type = 'button';
            closeBtn.setAttribute('aria-label', '关闭');
            closeBtn.innerHTML = '×';
            closeBtn.onclick = () => PureUI.modal.close(m.id);
            content.appendChild(closeBtn);
        }
    });

    // 3. 全局 ESC 键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const activeModal = document.querySelector('.p-modal.active');
            if (activeModal) {
                PureUI.modal.close(activeModal.id);
            }
        }
    });
});     