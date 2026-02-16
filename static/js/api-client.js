/**
 * 优化的API客户端
 * 配合优化后的后端API使用
 * 提供类型安全、错误处理、加载状态管理
 */

class OptimizedAPIClient {
    constructor(baseURL = '/api/v2') {
        this.baseURL = baseURL;
        this.defaultHeaders = {
            'Content-Type': 'application/json'
        };
        
        // 请求拦截器（可扩展为添加token等）
        this.requestInterceptors = [];
        // 响应拦截器（可用于统一错误处理）
        this.responseInterceptors = [];
    }
    
    /**
     * 基础请求方法
     */
    async request(endpoint, options = {}) {
        const url = `${this.baseURL}${endpoint}`;
        const config = {
            ...options,
            headers: {
                ...this.defaultHeaders,
                ...options.headers
            }
        };
        
        // 执行请求拦截器
        for (const interceptor of this.requestInterceptors) {
            await interceptor(config);
        }
        
        try {
            const response = await fetch(url, config);
            let data;
            
            // 尝试解析JSON
            try {
                data = await response.json();
            } catch (e) {
                data = null;
            }
            
            // 执行响应拦截器
            for (const interceptor of this.responseInterceptors) {
                data = await interceptor(data, response);
            }
            
            // 统一的响应格式处理
            if (response.ok) {
                return {
                    success: true,
                    data: data?.data || data,
                    message: data?.message || 'Success',
                    code: data?.code || response.status
                };
            } else {
                return {
                    success: false,
                    error: data?.message || data?.error || 'Request failed',
                    details: data?.details || null,
                    code: data?.code || response.status
                };
            }
        } catch (error) {
            return {
                success: false,
                error: error.message || 'Network error',
                code: 0
            };
        }
    }
    
    // ========================================
    // 便捷方法
    // ========================================
    
    async get(endpoint, params = {}) {
        const query = new URLSearchParams(params).toString();
        const url = query ? `${endpoint}?${query}` : endpoint;
        return this.request(url, { method: 'GET' });
    }
    
    async post(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }
    
    async put(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }
    
    async delete(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'DELETE',
            body: JSON.stringify(data)
        });
    }
    
    // ========================================
    // 业务API方法
    // ========================================
    
    /**
     * 搜索书籍
     */
    async searchBooks(keyword) {
        return this.post('/search', { keyword });
    }
    
    /**
     * 获取书库
     */
    async getLibrary(page = 1, perPage = 20, sortBy = 'update_time') {
        return this.get('/library', { 
            page, 
            per_page: perPage, 
            sort_by: sortBy 
        });
    }
    
    /**
     * 添加书籍
     */
    async addBook(bookKey, title, author = '未知作者', cover = '') {
        return this.post('/library/book', {
            book_key: bookKey,
            title,
            author,
            cover
        });
    }
    
    /**
     * 删除书籍（支持批量）
     */
    async deleteBooks(bookKeys) {
        // 确保bookKeys是数组
        const keys = Array.isArray(bookKeys) ? bookKeys : [bookKeys];
        return this.delete('/library/book', { book_keys: keys });
    }
    
    /**
     * 爬取内容
     */
    async fetchContent(url) {
        return this.post('/crawler/fetch', { url });
    }
    
    /**
     * 更新阅读进度
     */
    async updateProgress(bookKey, chapterIndex, chapterTitle = '') {
        return this.post('/reading/progress', {
            book_key: bookKey,
            chapter_index: chapterIndex,
            chapter_title: chapterTitle
        });
    }
    
    /**
     * 获取统计信息
     */
    async getStats() {
        return this.get('/stats');
    }
    
    /**
     * 健康检查
     */
    async healthCheck() {
        return this.get('/health');
    }
}

// ========================================
// 全局实例
// ========================================

const apiClient = new OptimizedAPIClient();

// ========================================
// 带UI反馈的API包装器
// ========================================

class UIAPIClient extends OptimizedAPIClient {
    constructor(baseURL, loadingManager, toastManager) {
        super(baseURL);
        this.loadingManager = loadingManager;
        this.toastManager = toastManager;
    }
    
    /**
     * 重写request，添加加载状态和toast提示
     */
    async request(endpoint, options = {}) {
        const showLoading = options.showLoading !== false;
        const showToast = options.showToast !== false;
        const successMessage = options.successMessage;
        
        let loadingId;
        if (showLoading && this.loadingManager) {
            loadingId = this.loadingManager.show(options.loadingText || '加载中...');
        }
        
        try {
            const result = await super.request(endpoint, options);
            
            if (showToast && this.toastManager) {
                if (result.success) {
                    if (successMessage) {
                        this.toastManager.success(successMessage);
                    }
                } else {
                    this.toastManager.error(result.error || '请求失败');
                }
            }
            
            return result;
        } finally {
            if (loadingId && this.loadingManager) {
                this.loadingManager.hide(loadingId);
            }
        }
    }
}

// ========================================
// 使用示例
// ========================================

/*
// 基础使用
const result = await apiClient.searchBooks('斗破苍穹');
if (result.success) {
    console.log('搜索结果:', result.data.results);
} else {
    console.error('搜索失败:', result.error);
}

// 批量删除
const deleteResult = await apiClient.deleteBooks(['fanqie:123', 'fanqie:456']);
if (deleteResult.success) {
    console.log('删除成功:', deleteResult.data.success_count);
}

// 带UI反馈的客户端
const loadingMgr = new LoadingManager();
const toastMgr = new ToastManager();
const uiClient = new UIAPIClient('/api/v2', loadingMgr, toastMgr);

// 自动显示加载状态和toast
await uiClient.searchBooks('斗破苍穹');

// 自定义消息
await uiClient.addBook('fanqie:123', '斗破苍穹', '天蚕土豆', '', {
    successMessage: '添加成功！',
    loadingText: '正在添加...'
});

// 添加请求拦截器（如添加token）
apiClient.requestInterceptors.push(async (config) => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers['Authorization'] = `Bearer ${token}`;
    }
});

// 添加响应拦截器（如统一错误处理）
apiClient.responseInterceptors.push(async (data, response) => {
    if (response.status === 401) {
        // 登录过期，跳转登录页
        window.location.href = '/login';
    }
    return data;
});
*/

// ========================================
// 错误处理辅助函数
// ========================================

/**
 * 统一的错误处理
 */
function handleAPIError(result, fallbackMessage = '操作失败') {
    if (result.success) return;
    
    const errorMsg = result.error || fallbackMessage;
    
    // 根据错误码进行不同处理
    switch (result.code) {
        case 400:
            console.warn('请求参数错误:', errorMsg);
            break;
        case 401:
            console.warn('未授权，请登录');
            window.location.href = '/login';
            break;
        case 403:
            console.warn('权限不足');
            break;
        case 404:
            console.warn('资源不存在');
            break;
        case 429:
            console.warn('请求过于频繁，请稍后再试');
            break;
        case 500:
            console.error('服务器错误:', errorMsg);
            break;
        default:
            console.error('请求失败:', errorMsg);
    }
    
    if (result.details) {
        console.error('错误详情:', result.details);
    }
}

// ========================================
// 重试机制
// ========================================

/**
 * 带重试的API调用
 */
async function retryAPI(apiFunc, maxRetries = 3, delay = 1000) {
    for (let i = 0; i < maxRetries; i++) {
        const result = await apiFunc();
        
        if (result.success) {
            return result;
        }
        
        // 如果是客户端错误（4xx），不重试
        if (result.code >= 400 && result.code < 500) {
            return result;
        }
        
        // 等待后重试
        if (i < maxRetries - 1) {
            await new Promise(resolve => setTimeout(resolve, delay * (i + 1)));
        }
    }
    
    return { success: false, error: '重试次数已用完' };
}

// 使用示例
// const result = await retryAPI(() => apiClient.searchBooks('斗破苍穹'), 3);

// ========================================
// 导出
// ========================================

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        OptimizedAPIClient,
        UIAPIClient,
        apiClient,
        handleAPIError,
        retryAPI
    };
}
