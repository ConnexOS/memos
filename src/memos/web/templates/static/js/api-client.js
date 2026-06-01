// src/memos/web/templates/static/js/api-client.js
// 统一 API 客户端：自动注入当前项目 ID
// 依赖 dashboard.js 中的全局函数 api() 和全局状态 window.state

const apiClient = {
    /**
     * 发起 API 请求，自动附加当前项目 ID。
     * @param {string} url - API 路径
     * @param {object} options - 透传给 api() 的选项
     * @returns {Promise<object>} 响应数据
     */
    async request(url, options = {}) {
        if (window.state?.currentProject) {
            const sep = url.includes('?') ? '&' : '?';
            url += sep + 'project_id=' + encodeURIComponent(window.state.currentProject);
        }
        return api(url, options);
    }
};
