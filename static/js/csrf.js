(() => {
    'use strict';

    const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

    function readCookie(name) {
        const prefix = `${name}=`;
        const entry = document.cookie.split('; ').find(item => item.startsWith(prefix));
        return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options = {}) => {
        const targetUrl = input instanceof Request ? input.url : input;
        const targetOrigin = new URL(targetUrl, window.location.href).origin;
        const requestMethod = (
            options.method || (input instanceof Request ? input.method : 'GET')
        ).toUpperCase();
        if (targetOrigin !== window.location.origin || SAFE_METHODS.has(requestMethod)) {
            return originalFetch(input, options);
        }

        const token = readCookie('notedb_csrf');
        if (!token) {
            return originalFetch(input, options);
        }

        const headers = new Headers(
            input instanceof Request ? input.headers : undefined
        );
        new Headers(options.headers).forEach((value, key) => headers.set(key, value));
        headers.set('X-CSRF-Token', token);
        return originalFetch(input, { ...options, headers });
    };
})();
