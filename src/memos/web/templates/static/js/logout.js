/* 退出登录 & 401 自动跳转 */
function handleLogout() {
  fetch('/api/auth/logout', { method: 'POST' })
    .finally(() => {
      localStorage.removeItem('memos_token');
      window.location.href = '/login';
    });
}

// 拦截 API 响应，401 时跳转登录页
(function intercept401() {
  const origFetch = window.fetch;
  window.fetch = function(...args) {
    return origFetch.apply(this, args).then(resp => {
      if (resp.status === 401 && !resp.url.endsWith('/api/auth/login')) {
        localStorage.removeItem('memos_token');
        window.location.href = '/login';
      }
      return resp;
    });
  };
})();
