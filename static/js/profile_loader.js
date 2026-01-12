(function () {
    async function fetchJson(url) {
        const res = await fetch(url, {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return await res.json();
    }

    async function main() {
        try {
            // profile URLs are under /profile/ ... so a relative "config/" is safest.
            const url = new URL('config/', window.location.href).toString();
            const cfg = await fetchJson(url);
            window.__vixo_profile_config = cfg;
        } catch (e) {
            // ignore
        }

        const s = document.createElement('script');
        s.src = '/static/js/profile.js';
        s.defer = true;
        document.head.appendChild(s);
    }

    main();
})();
