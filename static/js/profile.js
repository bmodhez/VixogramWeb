(function () {
    function readJsonScript(id) {
        try {
            const el = document.getElementById(id);
            if (!el) return null;
            const raw = (el.textContent || '').trim();
            if (!raw) return null;
            return JSON.parse(raw);
        } catch {
            return null;
        }
    }

    const cfg = (window.__vixo_profile_config || readJsonScript('vixo-profile-config') || {});
    const profileUsername = String(cfg.profileUsername || '');
    const isOwner = !!cfg.isOwner;

    function setPresenceUi(online) {
        const dot = document.getElementById('presence_dot');
        const text = document.getElementById('presence_text');
        if (!dot || !text) return;
        dot.classList.toggle('bg-emerald-400', !!online);
        dot.classList.toggle('bg-gray-500', !online);
        text.textContent = online ? 'Online' : 'Offline';
    }

    function initPresence() {
        // Template hides presence for bots; if elements don't exist, do nothing.
        const dot = document.getElementById('presence_dot');
        const text = document.getElementById('presence_text');
        if (!dot || !text) return;

        // Set initial state from server config (already respects stealth for non-owners).
        if (typeof cfg.presenceOnline === 'boolean') {
            setPresenceUi(cfg.presenceOnline);
        }

        // Don't open the socket if backend told us not to (stealth or bots).
        if (!cfg.presenceWsEnabled) return;
        if (!profileUsername) return;

        const wsScheme = (window.location.protocol === 'https:') ? 'wss' : 'ws';
        const wsUrl = `${wsScheme}://${window.location.host}/ws/presence/${encodeURIComponent(profileUsername)}/`;

        let socket;
        const connect = () => {
            try {
                socket = new WebSocket(wsUrl);
            } catch {
                return;
            }

            socket.onmessage = (event) => {
                let payload;
                try { payload = JSON.parse(event.data); } catch { return; }
                if (payload && payload.type === 'presence') {
                    if (typeof payload.online === 'boolean') setPresenceUi(payload.online);
                }
            };

            socket.onclose = () => {
                // Basic reconnect (only while tab is open)
                try {
                    if (document.visibilityState === 'hidden') return;
                } catch {}
                setTimeout(connect, 1200);
            };
        };

        connect();
    }

    function initFollowListModal() {
        const modal = document.getElementById('follow_modal');
        const titleEl = document.getElementById('follow_modal_title');
        const closeBtn = document.getElementById('follow_modal_close');
        const bodyEl = document.getElementById('follow_modal_body');
        const followersEl = document.getElementById('followers_count_value');
        const followingEl = document.getElementById('following_count_value');
        if (!modal || !titleEl || !closeBtn || !bodyEl) return;

        const open = ({ kind, url }) => {
            modal.dataset.kind = kind;
            modal.dataset.baseUrl = url;
            modal.dataset.full = '0';
            modal.dataset.profileUsername = profileUsername;

            if (kind === 'following') titleEl.textContent = isOwner ? 'Your followings' : 'Following';
            else titleEl.textContent = isOwner ? 'Your followers' : 'Followers';

            modal.classList.remove('hidden');
            modal.setAttribute('aria-hidden', 'false');
            bodyEl.innerHTML = '<div class="text-sm text-gray-400">Loading...</div>';

            try {
                if (window.htmx && typeof window.htmx.ajax === 'function') {
                    window.htmx.ajax('GET', url, { target: '#follow_modal_body', swap: 'innerHTML' });
                }
            } catch {}
        };

        const close = () => {
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
        };

        closeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            close();
        });
        modal.addEventListener('click', (e) => {
            if (e.target === modal || e.target === modal.firstElementChild) close();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) close();
        });

        document.addEventListener('click', (e) => {
            const btn = e.target && e.target.closest ? e.target.closest('[data-follow-modal]') : null;
            if (!btn) return;
            const kind = btn.getAttribute('data-follow-modal');
            const url = btn.getAttribute('data-url');
            if (!kind || !url) return;
            e.preventDefault();
            open({ kind, url });
        }, true);

        document.body.addEventListener('htmx:afterSwap', (e) => {
            try {
                const target = e && e.detail && e.detail.target;
                if (!target || target.id !== 'follow_modal_body') return;
                const state = document.getElementById('follow_modal_state');
                if (!state) return;
                modal.dataset.kind = state.getAttribute('data-kind') || (modal.dataset.kind || '');
                modal.dataset.full = state.getAttribute('data-full') || (modal.dataset.full || '0');
            } catch {}
        });

        document.body.addEventListener('followChanged', (e) => {
            try {
                const detail = (e && e.detail) ? e.detail : {};
                if (!detail || detail.profile_username !== profileUsername) return;

                if (followersEl && typeof detail.followers_count === 'number') followersEl.textContent = String(detail.followers_count);
                if (followingEl && typeof detail.following_count === 'number') followingEl.textContent = String(detail.following_count);

                if (modal.classList.contains('hidden')) return;
                if ((modal.dataset.kind || '') !== 'following') return;
                const baseUrl = modal.dataset.baseUrl || '';
                if (!baseUrl) return;

                const isFull = (modal.dataset.full || '0') === '1';
                const url = isFull ? (baseUrl + '?full=1') : baseUrl;
                if (window.htmx && typeof window.htmx.ajax === 'function') {
                    window.htmx.ajax('GET', url, { target: '#follow_modal_body', swap: 'innerHTML' });
                }
            } catch {}
        });
    }

    initFollowListModal();
    initPresence();
})();
