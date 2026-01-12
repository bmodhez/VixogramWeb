(function () {
    function extractRoomFromPath() {
        const path = String(window.location.pathname || '');
        const marker = '/chat/call/';
        const idx = path.indexOf(marker);
        if (idx === -1) return '';
        const rest = path.slice(idx + marker.length);
        const room = rest.split('/')[0] || '';
        try {
            return decodeURIComponent(room);
        } catch {
            return room;
        }
    }

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
        const room = extractRoomFromPath();
        if (!room) return;

        try {
            const qs = window.location.search || '';
            const url = '/chat/call/config/' + encodeURIComponent(room) + qs;
            const cfg = await fetchJson(url);
            window.__vixo_call_config = cfg;
        } catch (e) {
            // ignore
        }

        const s = document.createElement('script');
        s.src = '/static/js/call.js';
        s.defer = true;
        document.head.appendChild(s);
    }

    main();
})();
