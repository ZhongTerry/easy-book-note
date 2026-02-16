/**
 * 前端工具函数库（优化版）
 * 提供常用的前端工具函数，减少重复代码
 */

// ================================
// 1. DOM 操作优化
// ================================

/**
 * 防抖函数
 * 用于优化高频触发的事件（如搜索输入、窗口resize等）
 */
function debounce(func, wait = 300) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 节流函数
 * 确保函数在指定时间内最多执行一次
 */
function throttle(func, limit = 300) {
    let inThrottle;
    return function(...args) {
        if (!inThrottle) {
            func.apply(this, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

/**
 * 批量 DOM 操作
 * 使用 DocumentFragment 提升性能
 */
function batchDOMUpdate(container, items, createElementFunc) {
    const fragment = document.createDocumentFragment();
    
    items.forEach(item => {
        const element = createElementFunc(item);
        fragment.appendChild(element);
    });
    
    container.innerHTML = '';
    container.appendChild(fragment);
}

/**
 * 安全的选择器
 * 避免直接使用 querySelector 导致的错误
 */
function $(selector, parent = document) {
    try {
        return parent.querySelector(selector);
    } catch (e) {
        console.error(`Invalid selector: ${selector}`, e);
        return null;
    }
}

function $$(selector, parent = document) {
    try {
        return Array.from(parent.querySelectorAll(selector));
    } catch (e) {
        console.error(`Invalid selector: ${selector}`, e);
        return [];
    }
}

// ================================
// 2. 网络请求优化
// ================================

/**
 * 统一的 API 请求函数
 * 自动处理错误、加载状态等
 */
class APIClient {
    constructor(baseURL = '/api') {
        this.baseURL = baseURL;
        this.defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'same-origin'
        };
    }

    /**
     * 发送 GET 请求
     */
    async get(endpoint, options = {}) {
        return this.request(endpoint, {
            ...options,
            method: 'GET'
        });
    }

    /**
     * 发送 POST 请求
     */
    async post(endpoint, data, options = {}) {
        return this.request(endpoint, {
            ...options,
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    /**
     * 发送 DELETE 请求
     */
    async delete(endpoint, options = {}) {
        return this.request(endpoint, {
            ...options,
            method: 'DELETE'
        });
    }

    /**
     * 统一的请求处理
     */
    async request(endpoint, options = {}) {
        const url = `${this.baseURL}${endpoint}`;
        const config = {
            ...this.defaultOptions,
            ...options,
            headers: {
                ...this.defaultOptions.headers,
                ...options.headers
            }
        };

        try {
            const response = await fetch(url, config);
            const data = await response.json();

            // 统一处理响应
            if (data.status === 'success' || response.ok) {
                return {
                    success: true,
                    data: data.data || data,
                    message: data.message
                };
            } else {
                return {
                    success: false,
                    error: data.message || '请求失败',
                    code: data.code || response.status
                };
            }
        } catch (error) {
            console.error('API Error:', error);
            return {
                success: false,
                error: '网络错误，请检查连接',
                code: 'NETWORK_ERROR'
            };
        }
    }
}

// 创建全局 API 客户端实例
const api = new APIClient();

// ================================
// 3. 本地存储优化
// ================================

/**
 * 增强的本地存储工具
 * 支持过期时间、JSON 序列化等
 */
class Storage {
    constructor(type = 'localStorage') {
        this.storage = type === 'session' ? sessionStorage : localStorage;
    }

    /**
     * 设置数据（支持过期时间）
     */
    set(key, value, expireMinutes = null) {
        try {
            const data = {
                value: value,
                timestamp: Date.now(),
                expire: expireMinutes ? Date.now() + expireMinutes * 60 * 1000 : null
            };
            this.storage.setItem(key, JSON.stringify(data));
            return true;
        } catch (e) {
            console.error('Storage set error:', e);
            return false;
        }
    }

    /**
     * 获取数据（自动检查过期）
     */
    get(key, defaultValue = null) {
        try {
            const item = this.storage.getItem(key);
            if (!item) return defaultValue;

            const data = JSON.parse(item);

            // 检查是否过期
            if (data.expire && Date.now() > data.expire) {
                this.remove(key);
                return defaultValue;
            }

            return data.value;
        } catch (e) {
            console.error('Storage get error:', e);
            return defaultValue;
        }
    }

    /**
     * 移除数据
     */
    remove(key) {
        try {
            this.storage.removeItem(key);
            return true;
        } catch (e) {
            console.error('Storage remove error:', e);
            return false;
        }
    }

    /**
     * 清空所有数据
     */
    clear() {
        try {
            this.storage.clear();
            return true;
        } catch (e) {
            console.error('Storage clear error:', e);
            return false;
        }
    }

    /**
     * 获取所有键
     */
    keys() {
        return Object.keys(this.storage);
    }
}

// 创建全局存储实例
const storage = new Storage('localStorage');
const sessionStore = new Storage('session');

// ================================
// 4. UI 交互优化
// ================================

/**
 * 加载状态管理器
 */
class LoadingManager {
    constructor() {
        this.tasks = new Set();
        this.overlay = this.createOverlay();
    }

    createOverlay() {
        const overlay = document.createElement('div');
        overlay.id = 'global-loading';
        overlay.style.cssText = `
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.3);
            z-index: 9999;
            align-items: center;
            justify-content: center;
        `;
        overlay.innerHTML = `
            <div style="
                background: white;
                padding: 30px 40px;
                border-radius: 12px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.1);
                text-align: center;
            ">
                <div class="spinner" style="
                    width: 40px;
                    height: 40px;
                    border: 4px solid #f3f3f3;
                    border-top: 4px solid #4f46e5;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 15px;
                "></div>
                <p style="margin: 0; color: #666;">加载中...</p>
            </div>
        `;
        document.body.appendChild(overlay);
        
        // 添加动画
        const style = document.createElement('style');
        style.textContent = `
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
        `;
        document.head.appendChild(style);
        
        return overlay;
    }

    start(taskId = 'default') {
        this.tasks.add(taskId);
        this.updateUI();
    }

    finish(taskId = 'default') {
        this.tasks.delete(taskId);
        this.updateUI();
    }

    updateUI() {
        if (this.tasks.size > 0) {
            this.overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        } else {
            this.overlay.style.display = 'none';
            document.body.style.overflow = '';
        }
    }

    async wrap(promise, taskId = 'default') {
        this.start(taskId);
        try {
            const result = await promise;
            return result;
        } finally {
            this.finish(taskId);
        }
    }
}

const loading = new LoadingManager();

/**
 * Toast 提示管理器
 */
class ToastManager {
    constructor() {
        this.container = this.createContainer();
    }

    createContainer() {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = `
            position: fixed;
            top: 70px;
            right: 20px;
            z-index: 10000;
            display: flex;
            flex-direction: column;
            gap: 10px;
        `;
        document.body.appendChild(container);
        return container;
    }

    show(message, type = 'info', duration = 3000) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        
        const colors = {
            success: '#10b981',
            error: '#ef4444',
            warning: '#f59e0b',
            info: '#3b82f6'
        };
        
        const icons = {
            success: '✓',
            error: '✕',
            warning: '⚠',
            info: 'ℹ'
        };
        
        toast.style.cssText = `
            background: white;
            color: #333;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 250px;
            max-width: 400px;
            animation: slideIn 0.3s ease-out;
            border-left: 4px solid ${colors[type]};
        `;
        
        toast.innerHTML = `
            <span style="
                width: 24px;
                height: 24px;
                border-radius: 50%;
                background: ${colors[type]};
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
            ">${icons[type]}</span>
            <span style="flex: 1;">${message}</span>
        `;
        
        // 添加动画
        if (!document.getElementById('toast-animation-style')) {
            const style = document.createElement('style');
            style.id = 'toast-animation-style';
            style.textContent = `
                @keyframes slideIn {
                    from {
                        transform: translateX(400px);
                        opacity: 0;
                    }
                    to {
                        transform: translateX(0);
                        opacity: 1;
                    }
                }
                @keyframes slideOut {
                    from {
                        transform: translateX(0);
                        opacity: 1;
                    }
                    to {
                        transform: translateX(400px);
                        opacity: 0;
                    }
                }
            `;
            document.head.appendChild(style);
        }
        
        this.container.appendChild(toast);
        
        // 点击关闭
        toast.addEventListener('click', () => this.remove(toast));
        
        // 自动关闭
        if (duration > 0) {
            setTimeout(() => this.remove(toast), duration);
        }
        
        return toast;
    }

    remove(toast) {
        toast.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 300);
    }

    success(message, duration) {
        return this.show(message, 'success', duration);
    }

    error(message, duration) {
        return this.show(message, 'error', duration);
    }

    warning(message, duration) {
        return this.show(message, 'warning', duration);
    }

    info(message, duration) {
        return this.show(message, 'info', duration);
    }
}

const toast = new ToastManager();

// ================================
// 5. 工具函数
// ================================

/**
 * 格式化文件大小
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * 格式化时间
 */
function formatTime(timestamp) {
    const date = new Date(timestamp * 1000);
    const now = new Date();
    const diff = now - date;
    
    // 1分钟内
    if (diff < 60000) {
        return '刚刚';
    }
    // 1小时内
    if (diff < 3600000) {
        return `${Math.floor(diff / 60000)} 分钟前`;
    }
    // 今天
    if (date.toDateString() === now.toDateString()) {
        return `今天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
    }
    // 昨天
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
        return `昨天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
    }
    // 其他
    return `${date.getMonth() + 1}-${date.getDate()} ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/**
 * 生成唯一 ID
 */
function generateId() {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * 深拷贝对象
 */
function deepClone(obj) {
    if (obj === null || typeof obj !== 'object') return obj;
    if (obj instanceof Date) return new Date(obj);
    if (obj instanceof Array) return obj.map(item => deepClone(item));
    if (obj instanceof Object) {
        const cloned = {};
        for (const key in obj) {
            if (obj.hasOwnProperty(key)) {
                cloned[key] = deepClone(obj[key]);
            }
        }
        return cloned;
    }
}

/**
 * 检测移动端
 */
function isMobile() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
}

/**
 * 复制到剪贴板
 */
async function copyToClipboard(text) {
    try {
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
            return true;
        } else {
            // 兼容旧浏览器
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            return true;
        }
    } catch (e) {
        console.error('Copy failed:', e);
        return false;
    }
}

// ================================
// 导出（如果使用模块化）
// ================================

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        debounce,
        throttle,
        batchDOMUpdate,
        $,
        $$,
        api,
        storage,
        sessionStore,
        loading,
        toast,
        formatFileSize,
        formatTime,
        generateId,
        deepClone,
        isMobile,
        copyToClipboard
    };
}
