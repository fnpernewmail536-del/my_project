/* Shared account API client: never treat an HTML login/error page as success. */
(() => {
  'use strict';
  let csrfToken = '';
  window.accountAPI = async function api(url, options = {}, retryCSRF = true) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const headers = new Headers(options.headers || {});
      headers.set('Accept', 'application/json');
      if (csrfToken && headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', csrfToken);
      const response = await fetch(url, {...options, headers, cache:'no-store', credentials:'same-origin', signal:controller.signal});
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
        throw new Error(response.status === 429 ? '操作が多すぎます。しばらく待ってお試しください。' : 'ログイン状態を確認できません。ページを再読み込みしてください。');
      }
      const data = await response.json();
      if (response.status === 403 && data.code === 'csrf_expired' && retryCSRF) {
        const fresh = await api('/api/account/csrf', {}, false);
        csrfToken = fresh.csrf_token;
        return api(url, options, false);
      }
      if (!response.ok) throw new Error(data.error || `通信エラー (${response.status})`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('通信がタイムアウトしました。接続を確認してください。');
      throw error;
    } finally { clearTimeout(timeout); }
  };
})();
