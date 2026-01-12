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

    const cfg = (window.__vixo_chat_config && typeof window.__vixo_chat_config === 'object')
        ? window.__vixo_chat_config
        : (readJsonScript('vixo-chat-config') || {});

    const chatroomName = String(cfg.chatroomName || '');
    const currentUserId = parseInt(cfg.currentUserId || 0, 10) || 0;
    const currentUsername = String(cfg.currentUsername || '');
    let lastOtherReadId = parseInt(cfg.otherLastReadId || 0, 10) || 0;
    const pollUrl = String(cfg.pollUrl || '');
    const inviteUrl = String(cfg.inviteUrl || '');
    const tokenUrl = String(cfg.tokenUrl || '');
    const presenceUrl = String(cfg.presenceUrl || '');

    const wsScheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${wsScheme}://${window.location.host}/ws/chatroom/${chatroomName}`;

    // Expose current room + scroll state helpers to global notification handlers
    window.__activeChatroomName = chatroomName;
    window.__isChatNearBottom = function () {
        const chatContainer = document.getElementById('chat_container');
        if (!chatContainer) return true;
        const distanceFromBottom = (chatContainer.scrollHeight - (chatContainer.scrollTop + chatContainer.clientHeight));
        return distanceFromBottom <= 140;
    };

    // Autoscroll behavior:
    // - If user is near bottom, follow new messages.
    // - If user scrolls up to read, stop auto-scrolling until they return near bottom.
    let __chatAutoScrollEnabled = true;
    (function initAutoscrollToggle() {
        const chatContainer = document.getElementById('chat_container');
        if (!chatContainer) return;
        const recompute = () => {
            try {
                __chatAutoScrollEnabled = !!window.__isChatNearBottom();
            } catch {
                __chatAutoScrollEnabled = true;
            }
        };
        recompute();
        chatContainer.addEventListener('scroll', recompute, { passive: true });
    })();

    function highlightMentionMessage(messageId) {
        const mid = parseInt(messageId || 0, 10) || 0;
        if (!mid) return;

        const el = document.getElementById(`msg-${mid}`);
        if (!el) return;

        // Full-row highlight (behind the bubble), without changing bubble background.
        const existing = el.querySelector('[data-mention-highlight]');
        if (existing) {
            try { existing.remove(); } catch {}
        }
        const overlay = document.createElement('div');
        overlay.setAttribute('data-mention-highlight', '1');
        overlay.className = 'absolute inset-x-0 -inset-y-1 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 pointer-events-none';
        el.insertBefore(overlay, el.firstChild);

        setTimeout(() => {
            try { overlay.remove(); } catch {}
        }, 2400);
    }

    // When a global mention notification arrives, highlight the message in this room.
    window.addEventListener('chat:mention', (e) => {
        const d = e && e.detail ? e.detail : {};
        if (!d || !d.chatroom_name) return;
        if (String(d.chatroom_name) !== String(chatroomName)) return;

        const messageId = d.message_id || 0;
        let tries = 0;
        const tick = () => {
            tries += 1;
            const el = document.getElementById(`msg-${messageId}`);
            if (el) {
                highlightMentionMessage(messageId);
                return;
            }
            if (tries < 12) setTimeout(tick, 120);
        };
        tick();
    });

    // When a global call control notification arrives, close the call popup.
    window.addEventListener('call:control', (e) => {
        const d = e && e.detail ? e.detail : {};
        if (!d || !d.chatroom_name) return;
        if (String(d.chatroom_name) !== String(chatroomName)) return;
        if (d.action === 'end' || d.action === 'decline') {
            endCallPopup(d.action === 'decline' ? 'Call declined' : 'Call ended');
        }
    });

    let __lastChatBlockedState = null;
    function applyChatBlockedUI(blocked, opts) {
        const options = opts || {};
        const normalized = !!blocked;
        const allowPopup = options.allowPopup !== false;
        const isTransition = (typeof __lastChatBlockedState === 'boolean' && __lastChatBlockedState !== normalized);
        __lastChatBlockedState = normalized;

        const form = document.getElementById('chat_message_form');
        const input = document.getElementById('id_body');
        const fileInput = document.getElementById('chat_file_input');
        const captionInput = document.getElementById('chat_file_caption');
        const sendBtn = document.getElementById('chat_send_btn');

        // If the server rendered the blocked state, the form doesn't exist.
        // On unblock, reload to restore the form.
        if (!form) {
            if (!normalized) {
                try { window.location.reload(); } catch {}
            }
            return;
        }

        let banner = document.getElementById('chat_blocked_banner');
        if (normalized) {
            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'chat_blocked_banner';
                banner.className = 'mb-2 rounded-xl border border-gray-800 bg-gray-900/80 px-4 py-3 text-sm text-gray-200';
                banner.textContent = 'You are blocked from sending messages.';
                form.parentNode.insertBefore(banner, form);
            }
        } else if (banner) {
            try { banner.remove(); } catch {}
        }

        const disable = normalized;
        if (input) input.disabled = disable;
        if (fileInput) fileInput.disabled = disable;
        if (captionInput) captionInput.disabled = disable;
        if (sendBtn) {
            sendBtn.disabled = disable;
            sendBtn.classList.toggle('opacity-70', disable);
            sendBtn.classList.toggle('cursor-not-allowed', disable);
        }

        // Only show popup when the state actually changes (not on initial page load).
        if (allowPopup && isTransition) {
            if (disable) {
                try { __popup('Blocked', 'You are blocked from chatting right now.'); } catch {}
            } else {
                try { __popup('Unblocked', 'You can chat again.'); } catch {}
            }
        }
    }

    // Realtime block/unblock (admin action)
    window.addEventListener('chat:block_status', (e) => {
        const d = e && e.detail ? e.detail : {};
        applyChatBlockedUI(!!d.blocked, { allowPopup: true });
    });

    // Initialize from embedded config (if present)
    if (typeof cfg.chatBlocked !== 'undefined') {
        // Initialize UI silently (no popups on load).
        applyChatBlockedUI(!!cfg.chatBlocked, { allowPopup: false });
    }

    (function initChatEmojiPicker() {
        const btn = document.getElementById('emoji_btn');
        const panel = document.getElementById('emoji_panel');
        const mount = document.getElementById('emoji_mart_mount');

        if (!btn || !panel || !mount) return;

        let pickerEl = null;
        const ANIM_MS = 160;

        function getActiveInput() {
            const captionWrap = document.getElementById('chat_file_caption_wrap');
            const captionInput = document.getElementById('chat_file_caption');
            if (captionWrap && !captionWrap.classList.contains('hidden') && captionInput && !captionInput.disabled) {
                return captionInput;
            }
            return document.getElementById('id_body');
        }

        function insertAtCursor(input, text) {
            if (!input) return;
            const start = input.selectionStart ?? input.value.length;
            const end = input.selectionEnd ?? input.value.length;
            const before = input.value.slice(0, start);
            const after = input.value.slice(end);
            input.value = before + text + after;
            const nextPos = start + text.length;
            try {
                input.setSelectionRange(nextPos, nextPos);
            } catch (e) {}
            try {
                input.focus();
            } catch (e) {}
        }

        function ensurePicker() {
            if (pickerEl) return;
            mount.innerHTML = '';

            // Primary: emoji-mart
            if (window.EmojiMart && window.EmojiMart.Picker) {
                pickerEl = new window.EmojiMart.Picker({
                    theme: 'dark',
                    set: 'native',
                    dynamicWidth: true,
                    previewPosition: 'none',
                    onEmojiSelect: (emoji) => {
                        const nativeEmoji = (emoji && emoji.native) ? emoji.native : '';
                        if (!nativeEmoji) return;
                        insertAtCursor(getActiveInput(), nativeEmoji);
                        close();
                    },
                });

                // Make it wider and less tall (not "lamban"). Inline styles override the component's default :host height.
                try {
                    pickerEl.style.width = '100%';
                    pickerEl.style.height = '320px';
                } catch (e) {}

                mount.appendChild(pickerEl);
                return;
            }

            // Fallback: small built-in emoji grid (works offline / when CDN is blocked)
            const fallback = document.createElement('div');
            fallback.className = 'grid grid-cols-8 gap-1 text-lg select-none';
            const emojis = [
                '😀','😁','😂','🤣','😊','😍','😘','😎',
                '😅','😇','🙂','😉','😋','😜','🤔','😴',
                '😢','😭','😡','🤯','👍','👎','🙏','👏',
                '🔥','✨','💯','❤️','💔','🎉','😮','🤝',
            ];
            emojis.forEach((em) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.textContent = em;
                b.className = 'h-9 w-9 rounded-lg hover:bg-gray-800/60';
                b.addEventListener('click', () => {
                    insertAtCursor(getActiveInput(), em);
                    close();
                });
                fallback.appendChild(b);
            });
            pickerEl = fallback;
            mount.appendChild(fallback);
        }

        const open = () => {
            ensurePicker();
            panel.classList.remove('hidden');
            // animate in
            requestAnimationFrame(() => {
                panel.classList.remove('opacity-0', 'scale-95', 'pointer-events-none');
                panel.classList.add('opacity-100', 'scale-100');
            });
            btn.setAttribute('aria-expanded', 'true');
        };
        const close = () => {
            if (panel.classList.contains('hidden')) {
                btn.setAttribute('aria-expanded', 'false');
                return;
            }
            // animate out
            panel.classList.remove('opacity-100', 'scale-100');
            panel.classList.add('opacity-0', 'scale-95', 'pointer-events-none');
            btn.setAttribute('aria-expanded', 'false');

            const done = () => {
                panel.classList.add('hidden');
                panel.removeEventListener('transitionend', done);
            };
            panel.addEventListener('transitionend', done);
            // fallback (in case transitionend doesn't fire)
            setTimeout(() => {
                if (!panel.classList.contains('hidden')) panel.classList.add('hidden');
                panel.removeEventListener('transitionend', done);
            }, ANIM_MS + 40);
        };
        const toggle = () => {
            if (panel.classList.contains('hidden')) open();
            else close();
        };

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            toggle();
        });

        document.addEventListener('click', (e) => {
            const path = (e && typeof e.composedPath === 'function') ? e.composedPath() : null;
            if (path) {
                if (path.includes(btn) || path.includes(panel)) return;
            } else {
                const t = e.target;
                if (t && (panel.contains(t) || btn.contains(t))) return;
            }
            close();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && btn.getAttribute('aria-expanded') === 'true') close();
        });
    })();

    (function initChatUploadProgress() {
        const form = document.getElementById('chat_message_form');
        const sendBtn = document.getElementById('chat_send_btn');
        const sendText = document.getElementById('chat_send_text');
        const spinner = document.getElementById('chat_upload_spinner');
        const fileInput = document.getElementById('chat_file_input');
        const wrap = document.getElementById('chat_upload_progress');
        const bar = document.getElementById('chat_upload_bar');
        const percentEl = document.getElementById('chat_upload_percent');
        const statusEl = document.getElementById('chat_upload_status');

        if (!form || !sendBtn || !spinner || !wrap || !bar || !percentEl || !statusEl) return;

        function hasFileSelected() {
            return !!(fileInput && fileInput.files && fileInput.files.length > 0);
        }

        function setUploadingState(on) {
            if (on) {
                wrap.classList.remove('hidden');
                spinner.classList.remove('hidden');
                sendBtn.disabled = true;
                sendBtn.classList.add('opacity-70', 'cursor-not-allowed');
                if (sendText) sendText.textContent = 'Uploading…';
                if (fileInput) fileInput.disabled = true;
            } else {
                spinner.classList.add('hidden');
                sendBtn.disabled = false;
                sendBtn.classList.remove('opacity-70', 'cursor-not-allowed');
                if (sendText) sendText.textContent = 'Send';
                if (fileInput) fileInput.disabled = false;
                // Keep progress hidden after request to avoid UI flicker on text-only sends.
                wrap.classList.add('hidden');
                statusEl.textContent = 'Uploading…';
                percentEl.textContent = '0%';
                bar.style.width = '0%';
            }
        }

        // Only show upload UI when a file is being sent.
        document.body.addEventListener('htmx:beforeRequest', (e) => {
            if (!e || !e.target) return;
            if (e.target !== form) return;
            if (!hasFileSelected()) return;
            setUploadingState(true);
        });

        document.body.addEventListener('htmx:xhr:progress', (e) => {
            if (!e || !e.target) return;
            if (e.target !== form) return;
            if (!hasFileSelected()) return;
            const detail = e.detail || {};
            const loaded = Number(detail.loaded || 0);
            const total = Number(detail.total || 0);
            if (!total || total <= 0) return;
            const pct = Math.max(0, Math.min(100, Math.round((loaded / total) * 100)));
            percentEl.textContent = pct + '%';
            bar.style.width = pct + '%';
            statusEl.textContent = pct >= 100 ? 'Processing…' : 'Uploading…';
        });

        document.body.addEventListener('htmx:afterRequest', (e) => {
            if (!e || !e.target) return;
            if (e.target !== form) return;
            setUploadingState(false);
        });

        document.body.addEventListener('htmx:responseError', (e) => {
            if (!e || !e.target) return;
            if (e.target !== form) return;
            setUploadingState(false);
        });
    })();

    // Keep the "Upload (x/y used)" counter in sync without requiring a page refresh.
    // The server triggers this event after a successful upload.
    document.body.addEventListener('uploadCountUpdated', (e) => {
        const detail = (e && e.detail) ? e.detail : {};
        const used = parseInt(detail.used ?? 0, 10) || 0;
        const limit = parseInt(detail.limit ?? 0, 10) || 0;
        const remaining = parseInt(detail.remaining ?? 0, 10);

        const counter = document.getElementById('upload_counter');
        if (counter && limit > 0) {
            counter.textContent = `Upload (${used}/${limit} used)`;
        }

        const msg = document.getElementById('upload_limit_reached_msg');
        if (msg && !Number.isNaN(remaining)) {
            msg.classList.toggle('hidden', remaining > 0);
        }

        const fileInput = document.getElementById('chat_file_input');
        if (fileInput && !Number.isNaN(remaining)) {
            fileInput.disabled = remaining <= 0;
            if (typeof window.__syncUploadMode === 'function') {
                window.__syncUploadMode();
            }
        }
    });

    function resetComposerAfterSend() {
        const form = document.getElementById('chat_message_form');
        if (!form) return;

        const input = document.getElementById('id_body');
        const typedMsInput = document.getElementById('typed_ms');
        const replyToIdInput = document.getElementById('reply_to_id');
        const fileInput = document.getElementById('chat_file_input');
        const captionInput = document.getElementById('chat_file_caption');
        const feedback = document.getElementById('upload_feedback');

        const replyBar = document.getElementById('reply_bar');
        const replyBarAuthor = document.getElementById('reply_bar_author');
        const replyBarPreview = document.getElementById('reply_bar_preview');

        if (input) input.value = '';
        if (typedMsInput) typedMsInput.value = '';
        if (replyToIdInput) replyToIdInput.value = '';

        if (captionInput) captionInput.value = '';
        if (fileInput) fileInput.value = '';
        if (feedback) feedback.innerHTML = '';

        if (replyBar) replyBar.classList.add('hidden');
        if (replyBarAuthor) replyBarAuthor.textContent = '';
        if (replyBarPreview) replyBarPreview.textContent = '';

        if (typeof window.__syncUploadMode === 'function') {
            window.__syncUploadMode();
        }
    }

    // Successful HTMX sends (covers file uploads and text sends when WS is unavailable).
    document.body.addEventListener('htmx:afterRequest', (event) => {
        const form = document.getElementById('chat_message_form');
        if (!form) return;
        if (!event || event.target !== form) return;
        if (!event.detail || !event.detail.successful) return;
        const xhr = event.detail.xhr;
        const status = xhr ? Number(xhr.status || 0) : 0;
        if (status && (status < 200 || status >= 300)) return;
        resetComposerAfterSend();
    });

    // Explicit marker from server after a file upload; ensure the file chooser is cleared.
    document.body.addEventListener('chatFileUploaded', () => {
        resetComposerAfterSend();
    });

    (function initMentionAutocomplete() {
        const input = document.getElementById('id_body');
        const panel = document.getElementById('mention_panel');
        const list = document.getElementById('mention_list');
        const url = String(cfg.mentionSearchUrl || '');

        if (!input || !panel || !list) return;
        if (!url) return;

        let open = false;
        let activeIndex = 0;
        let results = [];
        let current = null; // { atIndex, caret }
        let debounceTimer = null;
        let abortController = null;

        function isOpen() {
            return open && !panel.classList.contains('hidden');
        }

        function close() {
            open = false;
            results = [];
            current = null;
            activeIndex = 0;
            panel.classList.add('hidden');
            list.innerHTML = '';
        }

        function show() {
            open = true;
            panel.classList.remove('hidden');
        }

        function getMentionContext() {
            const caret = input.selectionStart ?? (input.value || '').length;
            const before = (input.value || '').slice(0, caret);
            const atIndex = before.lastIndexOf('@');
            if (atIndex < 0) return null;
            if (atIndex > 0) {
                const prev = before[atIndex - 1];
                if (prev && !/\s/.test(prev)) return null;
            }
            const raw = before.slice(atIndex + 1);
            if (!raw) return null;
            if (/\s/.test(raw)) return null;
            if (!/^[A-Za-z0-9_.-]{1,32}$/.test(raw)) return null;
            return { atIndex, caret, q: raw };
        }

        function render() {
            if (!results.length) {
                close();
                return;
            }
            show();
            const safeIndex = Math.max(0, Math.min(activeIndex, results.length - 1));
            activeIndex = safeIndex;

            list.innerHTML = results
                .map((r, idx) => {
                    const active = idx === activeIndex;
                    const cls = active
                        ? 'bg-gray-800/80 text-white'
                        : 'hover:bg-gray-800/50 text-gray-200';
                    const avatar = r.avatar
                        ? `<img src="${r.avatar}" alt="" class="h-7 w-7 rounded-full border border-gray-800" />`
                        : `<div class="h-7 w-7 rounded-full border border-gray-800 bg-gray-800/70"></div>`;
                    const display = (r.display || r.username || '').toString();
                    const username = (r.username || '').toString();
                    return `
                            <button
                                type="button"
                                class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left ${cls}"
                                data-idx="${idx}"
                            >
                                ${avatar}
                                <div class="min-w-0">
                                    <div class="text-sm font-semibold truncate">${display}</div>
                                    <div class="text-[11px] text-gray-400 truncate">@${username}</div>
                                </div>
                            </button>
                        `;
                })
                .join('');
        }

        function insertMention(username) {
            if (!current || !username) return;
            const value = input.value || '';
            const before = value.slice(0, current.atIndex);
            const after = value.slice(current.caret);
            const insert = `@${username} `;
            input.value = before + insert + after;
            const nextPos = (before + insert).length;
            try { input.setSelectionRange(nextPos, nextPos); } catch (e) {}
            try { input.focus(); } catch (e) {}
            close();
        }

        async function fetchResults(q) {
            if (abortController) {
                try { abortController.abort(); } catch (e) {}
            }
            abortController = new AbortController();
            const resp = await fetch(`${url}?q=${encodeURIComponent(q)}`, {
                credentials: 'same-origin',
                signal: abortController.signal,
                headers: { 'Accept': 'application/json' },
            });
            if (!resp.ok) return [];
            const data = await resp.json();
            return Array.isArray(data && data.results) ? data.results : [];
        }

        function schedule() {
            const ctx = getMentionContext();
            if (!ctx) {
                close();
                return;
            }
            current = { atIndex: ctx.atIndex, caret: ctx.caret };
            const q = ctx.q;

            if (debounceTimer) window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(async () => {
                try {
                    const res = await fetchResults(q);
                    results = res || [];
                    activeIndex = 0;
                    render();
                } catch (e) {
                    // ignore abort/network errors
                }
            }, 150);
        }

        input.addEventListener('input', schedule);
        input.addEventListener('click', schedule);

        input.addEventListener('keydown', (e) => {
            if (!isOpen()) {
                if (e.key === 'Escape') close();
                return;
            }

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                activeIndex = Math.min(activeIndex + 1, results.length - 1);
                render();
                return;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                activeIndex = Math.max(activeIndex - 1, 0);
                render();
                return;
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                const chosen = results[activeIndex];
                if (chosen && chosen.username) {
                    e.preventDefault();
                    insertMention(chosen.username);
                }
                return;
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                close();
            }
        });

        panel.addEventListener('click', (e) => {
            const btn = e.target && e.target.closest ? e.target.closest('button[data-idx]') : null;
            if (!btn) return;
            const idx = Number(btn.getAttribute('data-idx'));
            const chosen = results[idx];
            if (chosen && chosen.username) insertMention(chosen.username);
        });

        document.addEventListener('click', (e) => {
            const t = e.target;
            if (!t) return;
            if (panel.contains(t) || input.contains(t)) return;
            close();
        });
    })();

    // If server rate-limits message sends (429), show a countdown on the Send button.
    let sendCooldownTimer = null;
    function startSendCooldown(seconds) {
        const btn = document.getElementById('chat_send_btn');
        const input = document.getElementById('id_body');
        if (!btn || !input) return;

        if (!btn.dataset.originalText) {
            btn.dataset.originalText = (btn.textContent || 'Send').trim();
        }

        let remaining = Math.max(1, Number(seconds || 1));
        btn.disabled = true;
        input.disabled = true;

        const tick = () => {
            btn.textContent = `Wait ${remaining}s`;
            remaining -= 1;
            if (remaining < 0) {
                window.clearInterval(sendCooldownTimer);
                sendCooldownTimer = null;
                btn.disabled = false;
                input.disabled = false;
                btn.textContent = btn.dataset.originalText || 'Send';
                try { input.focus(); } catch (e) {}
            }
        };

        if (sendCooldownTimer) window.clearInterval(sendCooldownTimer);
        tick();
        sendCooldownTimer = window.setInterval(tick, 1000);
    }

    // When a file is selected, switch the main form to upload mode.
    (function setupUploadModeSwitcher() {
        const msgForm = document.getElementById('chat_message_form');
        const fileInput = document.getElementById('chat_file_input');
        const uploadBtn = document.getElementById('chat_upload_btn');
        const captionWrap = document.getElementById('chat_file_caption_wrap');
        const captionInput = document.getElementById('chat_file_caption');
        const bodyWrap = document.getElementById('chat_body_wrap');
        const msgInput = (msgForm && msgForm.querySelector('[name="body"]')) || document.getElementById('id_body');

        if (!msgForm || !fileInput) return;

        function syncUploadMode() {
            const hasFile = !!(fileInput.files && fileInput.files.length);
            if (captionWrap) captionWrap.classList.toggle('hidden', !hasFile);
            if (bodyWrap) bodyWrap.classList.toggle('hidden', hasFile);
            if (!hasFile) {
                if (captionInput) captionInput.value = '';
                return;
            }
        }

        window.__syncUploadMode = syncUploadMode;

        if (uploadBtn) {
            uploadBtn.addEventListener('click', (e) => {
                e.preventDefault();
                if (fileInput && !fileInput.disabled) {
                    try { fileInput.click(); } catch (err) {}
                }
            });
        }
        fileInput.addEventListener('change', syncUploadMode);
        syncUploadMode();

        // If switching to upload mode and message box has text, move it into caption.
        fileInput.addEventListener('change', () => {
            const hasFile = !!(fileInput.files && fileInput.files.length);
            if (!hasFile) return;
            if (!captionInput) return;
            const cap = (captionInput.value || '').trim();
            const body = (msgInput && (msgInput.value || '').trim()) || '';
            if (!cap && body) {
                captionInput.value = body.slice(0, 300);
            }
        });
    })();

    // --- Link policy (links only allowed in private chats) ---
    const __linksAllowed = !!cfg.linksAllowed;
    function __containsLink(text) {
        const s = (text || '').trim();
        if (!s) return false;
        // Catch http(s)://, www., and bare domains with a TLD.
        const schemeOrWww = /\b(?:https?:\/\/|www\.)[^\s<>"']+/i;
        if (schemeOrWww.test(s)) return true;
        const bare = /\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,})(?:\/[\S]*)?/i;
        const m = s.match(bare);
        if (!m) return false;
        return /[a-z]/i.test(m[0]);
    }

    function __popup(title, message) {
        if (typeof window.__openConfirm === 'function') {
            window.__openConfirm({
                title: title || 'Notice',
                message: message || '',
                showCancel: false,
                okText: 'OK',
            });
        } else {
            alert(message || '');
        }
    }

    // Instant popup + cancel request if user tries to send links in non-private chats.
    document.body.addEventListener('htmx:beforeRequest', (event) => {
        const form = document.getElementById('chat_message_form');
        if (!form) return;
        if (event.target !== form) return;
        if (__linksAllowed) return;

        const fileInput = document.getElementById('chat_file_input');
        const inUploadMode = !!(fileInput && fileInput.files && fileInput.files.length);
        const bodyInput = document.getElementById('id_body');
        const captionInput = document.getElementById('chat_file_caption');
        const text = inUploadMode ? (captionInput && captionInput.value) : (bodyInput && bodyInput.value);

        if (__containsLink(text || '')) {
            event.preventDefault();
            event.stopPropagation();
            __popup('Links not allowed', 'Sending links is only allowed in private chats.');
        }
    }, true);

    // If server blocks links (HX-Trigger), show popup.
    document.body.addEventListener('linksNotAllowed', (e) => {
        const reason = (e && e.detail && e.detail.reason) ? e.detail.reason : 'Links are only allowed in private chats.';
        __popup('Links not allowed', reason);
    });

    // Confirm before opening external links.
    document.addEventListener('click', (e) => {
        const a = e.target && e.target.closest ? e.target.closest('a') : null;
        if (!a) return;
        const href = a.getAttribute('href') || '';
        if (!(href.startsWith('http://') || href.startsWith('https://'))) return;

        e.preventDefault();
        e.stopPropagation();

        const openLink = () => {
            try {
                window.open(href, '_blank', 'noopener');
            } catch {
                window.location.href = href;
            }
        };

        if (typeof window.__openConfirm === 'function') {
            window.__openConfirm({
                title: 'Open link?',
                message: 'Are you sure you want to open this link?',
                okText: 'Open',
                cancelText: 'Cancel',
                showCancel: true,
                onConfirm: openLink,
            });
        } else {
            if (confirm('Are you sure you want to open this link?')) openLink();
        }
    }, true);

    const initialMuted = Number(cfg.chatMutedSeconds || 0);
    if (Number.isFinite(initialMuted) && initialMuted > 0) {
        startSendCooldown(initialMuted);
    }

    const otherUsername = String(cfg.otherUsername || '');
    const meDisplayName = String(cfg.meDisplayName || '');
    const meAvatarUrl = String(cfg.meAvatarUrl || '');
    const otherDisplayName = String(cfg.otherDisplayName || '');
    const otherAvatarUrl = String(cfg.otherAvatarUrl || '');
    const callEventUrl = String(cfg.callEventUrl || '');
    const messageEditUrlTemplate = String(cfg.messageEditUrlTemplate || '');
    const messageDeleteUrlTemplate = String(cfg.messageDeleteUrlTemplate || '');
    const messageReactUrlTemplate = String(cfg.messageReactUrlTemplate || '');

    // -------- Ringtone (incoming) --------
    let audioCtx = null;
    let ringTimer = null;
    let audioUnlocked = false;

    function unlockAudioOnce() {
        if (audioUnlocked) return;
        try {
            audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
            // create a tiny silent buffer to unlock
            const o = audioCtx.createOscillator();
            const g = audioCtx.createGain();
            g.gain.value = 0.00001;
            o.connect(g);
            g.connect(audioCtx.destination);
            o.start();
            o.stop(audioCtx.currentTime + 0.01);
            audioUnlocked = true;
        } catch {
            // ignore
        }
    }

    document.addEventListener('click', unlockAudioOnce, { once: true, capture: true });

    function stopIncomingRing() {
        if (ringTimer) {
            clearInterval(ringTimer);
            ringTimer = null;
        }
    }

    function playIncomingRing() {
        // Browsers may block until user interacts; we try best-effort.
        try {
            audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
            if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
        } catch {
            return;
        }

        stopIncomingRing();

        const ringOnce = () => {
            try {
                const ctx = audioCtx;
                const now = ctx.currentTime;
                const g = ctx.createGain();
                g.gain.setValueAtTime(0.0001, now);
                g.gain.exponentialRampToValueAtTime(0.15, now + 0.02);
                g.gain.exponentialRampToValueAtTime(0.0001, now + 1.8);
                g.connect(ctx.destination);

                const o1 = ctx.createOscillator();
                const o2 = ctx.createOscillator();
                o1.type = 'sine';
                o2.type = 'sine';
                o1.frequency.setValueAtTime(480, now);
                o2.frequency.setValueAtTime(620, now);
                o1.connect(g);
                o2.connect(g);
                o1.start(now);
                o2.start(now);
                o1.stop(now + 1.9);
                o2.stop(now + 1.9);
            } catch {
                // ignore
            }
        };

        // Classic ring cadence: ring ~2s, pause ~3s
        ringOnce();
        ringTimer = setInterval(ringOnce, 5000);
    }

    const form = document.getElementById('chat_message_form');
    const input = document.getElementById('id_body');
    const typedMsInput = document.getElementById('typed_ms');
    const messagesEl = document.getElementById('chat_messages');
    const onlineCountEl = document.getElementById('online-count');

    // Chat input behavior:
    // - Enter: send
    // - Shift+Enter: new line
    // Note: when the mention panel is open, Enter is used to select a mention.
    if (form && input) {
        input.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') return;
            if (e.shiftKey) return;
            if (e.isComposing) return;

            const mentionPanel = document.getElementById('mention_panel');
            const mentionOpen = !!(mentionPanel && !mentionPanel.classList.contains('hidden'));
            if (mentionOpen) return;

            e.preventDefault();
            try {
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                }
            } catch (err) {
                // ignore
            }
        });
    }

    const replyBar = document.getElementById('reply_bar');
    const replyBarAuthor = document.getElementById('reply_bar_author');
    const replyBarPreview = document.getElementById('reply_bar_preview');
    const replyBarCancel = document.getElementById('reply_bar_cancel');
    const replyToIdInput = document.getElementById('reply_to_id');

    const editBar = document.getElementById('edit_bar');
    const editBarPreview = document.getElementById('edit_bar_preview');
    const editBarCancel = document.getElementById('edit_bar_cancel');
    let editingMessageId = null;

    function startEditingMessage(messageId, body) {
        if (!input) return;
        editingMessageId = messageId;
        if (replyToIdInput) replyToIdInput.value = '';
        if (replyBar) replyBar.classList.add('hidden');

        if (editBarPreview) editBarPreview.textContent = (body || '').slice(0, 120);
        if (editBar) editBar.classList.remove('hidden');

        input.value = body || '';
        input.focus();
    }

    function cancelEditingMessage() {
        editingMessageId = null;
        if (editBar) editBar.classList.add('hidden');
        if (editBarPreview) editBarPreview.textContent = '';
    }

    if (editBarCancel) {
        editBarCancel.addEventListener('click', function () {
            cancelEditingMessage();
        });
    }

    const typingIndicatorEl = document.getElementById('typing_indicator');
    const typingIndicatorTextEl = document.getElementById('typing_indicator_text');
    const typingUsers = new Map();
    const typingTimers = new Map();
    let typingStopTimer = null;
    let lastTypingPingAt = 0;

    function renderTypingIndicator() {
        if (!typingIndicatorEl || !typingIndicatorTextEl) return;
        const names = Array.from(typingUsers.values()).filter(Boolean);
        if (!names.length) {
            typingIndicatorEl.classList.add('hidden');
            typingIndicatorTextEl.textContent = '';
            return;
        }
        typingIndicatorEl.classList.remove('hidden');
        typingIndicatorTextEl.textContent = `${names.join(', ')} typing...`;
    }

    function handleTypingEvent(payload) {
        const authorId = payload.author_id;
        if (!authorId) return;

        const username = (payload.username || '').trim();
        const isTyping = !!payload.is_typing;

        if (typingTimers.has(authorId)) {
            clearTimeout(typingTimers.get(authorId));
            typingTimers.delete(authorId);
        }

        if (isTyping) {
            typingUsers.set(authorId, username);
            typingTimers.set(authorId, setTimeout(() => {
                typingUsers.delete(authorId);
                typingTimers.delete(authorId);
                renderTypingIndicator();
            }, 4000));
        } else {
            typingUsers.delete(authorId);
        }

        renderTypingIndicator();
    }

    function sendTyping(isTyping) {
        try {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ type: 'typing', is_typing: !!isTyping }));
        } catch {
            // ignore
        }
    }

    if (input) {
        let typingStartedAt = null;
        input.addEventListener('input', () => {
            if (typingStartedAt === null && (input.value || '').trim().length > 0) {
                typingStartedAt = Date.now();
            }
            const now = Date.now();
            if (now - lastTypingPingAt > 1200) {
                sendTyping(true);
                lastTypingPingAt = now;
            }
            if (typingStopTimer) clearTimeout(typingStopTimer);
            typingStopTimer = setTimeout(() => {
                sendTyping(false);
                typingStopTimer = null;
            }, 1800);
        });

        input.addEventListener('blur', () => {
            if (typingStopTimer) clearTimeout(typingStopTimer);
            typingStopTimer = null;
            sendTyping(false);
        });

        document.body.addEventListener('htmx:beforeRequest', (event) => {
            if (!form || event.target !== form) return;
            if (!typedMsInput) return;
            if (typingStartedAt === null) {
                typedMsInput.value = '';
                return;
            }
            const ms = Math.max(0, Date.now() - typingStartedAt);
            typedMsInput.value = String(ms);
        });

        document.body.addEventListener('htmx:afterRequest', (event) => {
            if (!form || event.target !== form) return;
            if (!event.detail.successful) return;
            typingStartedAt = null;
            if (typedMsInput) typedMsInput.value = '';
        });
    }

    if (form) {
        // Capture submit before HTMX when editing.
        form.addEventListener('submit', async (e) => {
            if (!editingMessageId) return;
            e.preventDefault();
            e.stopPropagation();

            const body = (input && input.value ? input.value : '').trim();
            if (!body) return;

            const url = messageEditUrlTemplate.replace('/0/', `/${editingMessageId}/`);
            try {
                const params = new URLSearchParams();
                params.set('body', body);
                await fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': (typeof getCookie === 'function' ? getCookie('csrftoken') : ''),
                    },
                    body: params,
                });
            } catch {
                // ignore
            }

            cancelEditingMessage();
            if (input) input.value = '';
            if (__chatAutoScrollEnabled) forceScrollToBottomNow();
        }, true);

        // For normal text messages, prefer WebSocket for instant send.
        // Keep HTMX submit for file uploads and as a fallback when WS isn't connected.
        form.addEventListener('submit', async (e) => {
            if (editingMessageId) return;

            const fileInput = document.getElementById('chat_file_input');
            const inUploadMode = !!(fileInput && fileInput.files && fileInput.files.length);
            if (inUploadMode) return;

            const hasHtmx = !!(window.htmx && typeof window.htmx === 'object');
            const wsOk = !!(socket && socket.readyState === WebSocket.OPEN && wsConnected);

            // If HTMX isn't available (e.g., CDN blocked/offline) and WS isn't connected,
            // fall back to a direct AJAX POST that mimics an HTMX request.
            if (!wsOk && !hasHtmx) {
                e.preventDefault();
                e.stopPropagation();
                try { e.stopImmediatePropagation(); } catch {}

                const sendBtn = document.getElementById('chat_send_btn');
                if (sendBtn) {
                    sendBtn.disabled = true;
                    sendBtn.classList.add('opacity-70', 'cursor-not-allowed');
                }

                try {
                    const url = form.getAttribute('hx-post') || form.getAttribute('action') || window.location.pathname;
                    const fd = new FormData(form);

                    const res = await fetch(url, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: {
                            'HX-Request': 'true',
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        body: fd,
                    });

                    if (res.status === 429) {
                        const retry = parseInt(res.headers.get('Retry-After') || '0', 10) || 0;
                        if (typeof __popup === 'function') {
                            __popup('Slow down', retry ? `Please wait ${retry}s and try again.` : 'Please wait and try again.');
                        }
                        return;
                    }

                    if (res.status === 403) {
                        if (typeof __popup === 'function') {
                            __popup('Not allowed', 'You cannot send messages right now.');
                        }
                        return;
                    }

                    const html = await res.text();
                    if (res.ok && html) {
                        const emptyEl = document.getElementById('empty_state');
                        if (emptyEl) emptyEl.remove();
                        if (messagesEl && messagesEl.classList.contains('hidden')) {
                            messagesEl.classList.remove('hidden');
                        }
                        if (messagesEl) {
                            messagesEl.insertAdjacentHTML('beforeend', html);
                            hydrateLocalTimes(messagesEl);
                            updateLastIdFromDom();
                        }
                        window.__forceNextChatScroll = true;
                        try { __chatAutoScrollEnabled = true; } catch {}
                        forceScrollToBottomNow();
                    }

                    // Reset UI after send
                    if (input) input.value = '';
                    if (typedMsInput) typedMsInput.value = '';
                    if (replyToIdInput) replyToIdInput.value = '';
                    if (replyBar) replyBar.classList.add('hidden');
                    if (replyBarPreview) replyBarPreview.textContent = '';
                    if (replyBarAuthor) replyBarAuthor.textContent = '';
                } catch {
                    // ignore
                } finally {
                    if (sendBtn) {
                        sendBtn.disabled = false;
                        sendBtn.classList.remove('opacity-70', 'cursor-not-allowed');
                    }
                }

                return;
            }

            if (!wsOk) return;

            const body = (input && input.value ? input.value : '').trim();
            if (!body) {
                e.preventDefault();
                e.stopPropagation();
                try { e.stopImmediatePropagation(); } catch {}
                return;
            }

            // Mirror the link policy UX when skipping HTMX.
            if (typeof __linksAllowed !== 'undefined' && typeof __containsLink === 'function') {
                if (!__linksAllowed && __containsLink(body)) {
                    e.preventDefault();
                    e.stopPropagation();
                    try { e.stopImmediatePropagation(); } catch {}
                    if (typeof __popup === 'function') {
                        __popup('Links not allowed', 'Sending links is only allowed in private chats.');
                    }
                    return;
                }
            }

            e.preventDefault();
            e.stopPropagation();
            try { e.stopImmediatePropagation(); } catch {}

            const payload = {
                body: body,
            };
            try {
                const replyToId = replyToIdInput ? (replyToIdInput.value || '').trim() : '';
                if (replyToId) payload.reply_to_id = replyToId;
                const typedMs = typedMsInput ? (typedMsInput.value || '').trim() : '';
                if (typedMs) payload.typed_ms = typedMs;
            } catch {
                // ignore
            }

            try {
                socket.send(JSON.stringify(payload));
            } catch {
                // If WS send fails, allow user to retry; don't fall back automatically.
                return;
            }

            // Always jump to the latest message after sending (even if user is scrolled up).
            // The actual message HTML is appended when the server echoes it back over WS,
            // so we use a one-shot flag to force-scroll on insert.
            window.__forceNextChatScroll = true;
            try { __chatAutoScrollEnabled = true; } catch {}

            // Reset UI immediately (HTMX won't run when we skip the submit).
            try {
                if (typingStopTimer) clearTimeout(typingStopTimer);
                typingStopTimer = null;
                sendTyping(false);
            } catch {
                // ignore
            }
            if (input) input.value = '';
            if (typedMsInput) typedMsInput.value = '';
            if (replyToIdInput) replyToIdInput.value = '';
            if (replyBar) replyBar.classList.add('hidden');
            if (replyBarPreview) replyBarPreview.textContent = '';
            if (replyBarAuthor) replyBarAuthor.textContent = '';
            if (typeof scrollToBottom === 'function') {
                scrollToBottom({ force: true, behavior: 'auto' });
            }
        }, true);

        form.addEventListener('submit', () => {
            if (typingStopTimer) clearTimeout(typingStopTimer);
            typingStopTimer = null;
            sendTyping(false);
        });
    }

    let socket = null;
    let wsConnected = false;
    let lastId = 0;

    function sendReadAck(id) {
        if (!wsConnected || !socket) return;
        const last = parseInt(id || 0, 10) || 0;
        if (last <= 0) return;
        try {
            socket.send(JSON.stringify({ type: 'read', last_read_id: last }));
        } catch {
            // ignore
        }
    }

    function applyReadTicks(lastReadId) {
        const maxId = parseInt(lastReadId || 0, 10) || 0;
        if (maxId <= 0) return;
        const ticks = document.querySelectorAll('[data-read-tick][data-message-id]') || [];
        ticks.forEach((el) => {
            const mid = parseInt(el.getAttribute('data-message-id') || '0', 10) || 0;
            if (mid && mid <= maxId) el.textContent = '✓✓';
        });
    }

    function maybeAckReadIfVisible() {
        if (document.visibilityState !== 'visible') return;
        updateLastIdFromDom();
        sendReadAck(lastId);
    }

    function hydrateLocalTimes(root) {
        const container = root || document;
        const nodes = container.querySelectorAll ? container.querySelectorAll('time[data-dt]') : [];
        if (!nodes || !nodes.length) return;

        const timeFmt = new Intl.DateTimeFormat(undefined, {
            hour: '2-digit',
            minute: '2-digit',
        });
        const fullFmt = new Intl.DateTimeFormat(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
        });

        nodes.forEach((el) => {
            const iso = el.getAttribute('data-dt');
            if (!iso) return;
            const d = new Date(iso);
            if (Number.isNaN(d.getTime())) return;

            el.textContent = timeFmt.format(d);
            // Hover tooltip for clarity across dates/timezones
            el.setAttribute('title', fullFmt.format(d));
            if (!el.getAttribute('datetime')) el.setAttribute('datetime', iso);
        });
    }

    function safeScrollToBottom() {
        if (typeof scrollToBottom === 'function') scrollToBottom();
    }

    function forceScrollToBottomNow() {
        if (typeof scrollToBottom === 'function') {
            scrollToBottom({ force: true, behavior: 'auto' });
        }
    }

    function updateLastIdFromDom() {
        const last = messagesEl && messagesEl.lastElementChild;
        const id = last && last.dataset ? parseInt(last.dataset.messageId || '0', 10) : 0;
        if (!Number.isNaN(id) && id > lastId) lastId = id;
    }

    function connect() {
        socket = new WebSocket(wsUrl);

        socket.onopen = function () {
            wsConnected = true;
            updateLastIdFromDom();
            if (document.visibilityState === 'visible') sendReadAck(lastId);
            // On initial load/room switch, start at the latest message.
            forceScrollToBottomNow();
        };

        socket.onmessage = function (event) {
            let payload;
            try {
                payload = JSON.parse(event.data);
            } catch {
                // Ignore unexpected non-JSON frames
                return;
            }

            if (payload.type === 'chat_message' && payload.html) {
                const emptyEl = document.getElementById('empty_state');
                if (emptyEl) emptyEl.remove();
                if (messagesEl && messagesEl.classList.contains('hidden')) {
                    messagesEl.classList.remove('hidden');
                }
                messagesEl.insertAdjacentHTML('beforeend', payload.html);
                hydrateLocalTimes(messagesEl);
                updateLastIdFromDom();
                const shouldForce = !!window.__forceNextChatScroll || !!__chatAutoScrollEnabled;
                if (window.__forceNextChatScroll) {
                    window.__forceNextChatScroll = false;
                    __chatAutoScrollEnabled = true;
                }
                if (shouldForce) forceScrollToBottomNow();
                if (document.visibilityState === 'visible') sendReadAck(lastId);
                return;
            }

            if (payload.type === 'read_receipt') {
                const readerId = parseInt(payload.reader_id || 0, 10) || 0;
                const lastReadId = parseInt(payload.last_read_id || 0, 10) || 0;
                if (readerId && readerId !== currentUserId) {
                    lastOtherReadId = Math.max(lastOtherReadId || 0, lastReadId || 0);
                    applyReadTicks(lastReadId);
                }
                return;
            }

            if (payload.type === 'typing') {
                handleTypingEvent(payload);
                return;
            }

            if (payload.type === 'message_update' && payload.message_id && payload.html) {
                const el = document.getElementById(`msg-${payload.message_id}`);
                if (el) {
                    el.outerHTML = payload.html;
                    hydrateLocalTimes(document);
                    applyReadTicks(lastOtherReadId);
                }
                return;
            }

            if (payload.type === 'message_delete' && payload.message_id) {
                const el = document.getElementById(`msg-${payload.message_id}`);
                if (el) el.remove();
                return;
            }

            if (payload.type === 'reactions' && payload.message_id && payload.html) {
                const el = document.getElementById(`reactions-${payload.message_id}`);
                if (el) {
                    el.outerHTML = payload.html;
                }
                return;
            }

            if (payload.type === 'online_count' && typeof payload.online_count !== 'undefined') {
                if (onlineCountEl) onlineCountEl.textContent = payload.online_count;
            }

            if (payload.type === 'call_invite') {
                if (!window.__hasGlobalCallInvite) {
                    showIncomingCall(payload);
                }
            }

            if (payload.type === 'call_control') {
                if (payload.action === 'end' || payload.action === 'decline') {
                    endCallPopup(payload.action === 'decline' ? 'Call declined' : 'Call ended');
                }
            }

            if (payload.type === 'call_presence') {
                // UI-only: keep the call popup participant list in sync.
                const action = (payload.action || 'join');
                const username = payload.username || '';

                // Only care when a call popup is open.
                if (!callActive) return;

                if (username === currentUsername) {
                    if (action === 'leave') setParticipant('me', 'Left', '—');
                    else setParticipant('me', 'In call', 'On call');
                    return;
                }

                if (otherUsername && username === otherUsername) {
                    if (action === 'leave') setParticipant('other', 'Left', '—');
                    else setParticipant('other', 'In call', 'On call');
                    return;
                }
            }

            if (payload.type === 'error' && payload.code === 'links_not_allowed') {
                __popup('Links not allowed', payload.message || 'Links are only allowed in private chats.');
                return;
            }
        };

        socket.onclose = function () {
            wsConnected = false;
            // Simple reconnect
            setTimeout(connect, 1000);
        };

        socket.onerror = function () {
            wsConnected = false;
        };
    }

    function isNearBottom() {
        try {
            if (typeof window.__isChatNearBottom === 'function') {
                return !!window.__isChatNearBottom();
            }
            const el = document.getElementById('chat_container');
            if (!el) return true;
            return (el.scrollHeight - (el.scrollTop + el.clientHeight)) <= 140;
        } catch {
            return true;
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        updateLastIdFromDom();
        if (isNearBottom()) sendReadAck(lastId);
    });

    window.addEventListener('focus', () => {
        updateLastIdFromDom();
        if (isNearBottom()) sendReadAck(lastId);
    });

    // Use query-by-id here to avoid referencing `chatContainer` before it's defined later in this file.
    const __chatContainerForRead = document.getElementById('chat_container');
    if (__chatContainerForRead) {
        __chatContainerForRead.addEventListener('scroll', () => {
            if (!isNearBottom()) return;
            updateLastIdFromDom();
            if (document.visibilityState === 'visible') sendReadAck(lastId);
        }, { passive: true });
    }

    // Ensure correct position on initial render
    document.addEventListener('DOMContentLoaded', function () {
        updateLastIdFromDom();
        hydrateLocalTimes(document);
        applyReadTicks(lastOtherReadId);
        safeScrollToBottom();
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') maybeAckReadIfVisible();
    });
    window.addEventListener('focus', () => {
        maybeAckReadIfVisible();
    });

    (function initReadAckOnScroll() {
        const chatContainer = document.getElementById('chat_container');
        if (!chatContainer) return;
        let timer = null;
        chatContainer.addEventListener('scroll', () => {
            if (timer) window.clearTimeout(timer);
            timer = window.setTimeout(() => {
                try {
                    const remaining = chatContainer.scrollHeight - (chatContainer.scrollTop + chatContainer.clientHeight);
                    if (remaining <= 80) maybeAckReadIfVisible();
                } catch {
                    // ignore
                }
            }, 120);
        }, { passive: true });
    })();

    (function initPinnedBannerCollapse() {
        const chatContainer = document.getElementById('chat_container');
        const banner = document.getElementById('pinned_banner');
        const full = document.getElementById('pinned_full');
        const fullInner = document.getElementById('pinned_full_inner');
        const collapsed = document.getElementById('pinned_collapsed');
        const label = document.getElementById('pinned_label');
        const toggle = document.getElementById('pinned_toggle');
        if (!chatContainer || !banner || !full || !fullInner || !collapsed) return;

        let forceExpanded = false;

        function setExpanded(expanded) {
            // Expanded: show full content, hide collapsed line.
            // Collapsed: show collapsed line, animate full content closed.
            const durationMs = 300;

            if (expanded) {
                // Ensure collapsed is visible during transition (fade out), then hide.
                if (!collapsed.classList.contains('hidden')) {
                    collapsed.style.opacity = '0';
                    window.setTimeout(() => {
                        collapsed.classList.add('hidden');
                    }, 180);
                }

                full.classList.remove('hidden');
                full.style.opacity = '1';

                // Start from 0 height if currently collapsed.
                if (String(full.style.height || '') === '0px') {
                    full.style.height = '0px';
                }

                const target = fullInner.scrollHeight;
                // Force a reflow so height transition triggers.
                void full.offsetHeight;
                full.style.height = `${target}px`;

                window.setTimeout(() => {
                    // After transition, let it size naturally.
                    full.style.height = 'auto';
                }, durationMs);
            } else {
                // Show collapsed (fade in)
                collapsed.classList.remove('hidden');
                // Force reflow for opacity.
                void collapsed.offsetHeight;
                collapsed.style.opacity = '1';

                // Collapse full: from auto -> px -> 0
                const current = fullInner.scrollHeight;
                full.style.height = `${current}px`;
                full.style.opacity = '1';
                void full.offsetHeight;
                full.style.height = '0px';
                full.style.opacity = '0';

                window.setTimeout(() => {
                    full.classList.add('hidden');
                    // Keep height at 0px for next expand start.
                    full.style.height = '0px';
                }, durationMs);
            }

            if (label) {
                if (expanded) label.classList.remove('hidden');
                else label.classList.add('hidden');
            }

            if (toggle) {
                toggle.setAttribute('aria-expanded', String(expanded));
            }
        }

        function sync() {
            const scrolled = (chatContainer.scrollTop || 0) > 12;
            const shouldBeCollapsed = scrolled && !forceExpanded;
            setExpanded(!shouldBeCollapsed);
        }

        chatContainer.addEventListener('scroll', sync, { passive: true });

        if (toggle) {
            toggle.addEventListener('click', () => {
                const scrolled = (chatContainer.scrollTop || 0) > 12;
                // If user is at top, we always consider it expanded.
                if (!scrolled) {
                    forceExpanded = false;
                    sync();
                    return;
                }

                forceExpanded = !forceExpanded;
                sync();
            });
        }

        // initial state
        sync();
    })();

    // -------- Reactions (WhatsApp style click popup) --------
    function closeAllReactionPickers() {
        document.querySelectorAll('[data-reaction-picker]').forEach((el) => {
            el.classList.add('hidden');
        });
    }

    document.addEventListener('click', function (e) {
        const toggleBtn = e.target.closest('[data-reaction-toggle]');
        const emojiBtn = e.target.closest('[data-react-emoji]');

        if (toggleBtn) {
            e.preventDefault();
            e.stopPropagation();
            const messageId = toggleBtn.dataset.messageId;
            if (!messageId) return;
            const picker = document.querySelector(`[data-reaction-picker][data-message-id="${messageId}"]`);
            if (!picker) return;
            const willOpen = picker.classList.contains('hidden');
            closeAllReactionPickers();
            if (willOpen) picker.classList.remove('hidden');
            return;
        }

        if (emojiBtn) {
            e.preventDefault();
            e.stopPropagation();
            const messageId = emojiBtn.dataset.messageId;
            const emoji = emojiBtn.dataset.emoji;
            if (!messageId || !emoji) return;
            closeAllReactionPickers();
            const url = messageReactUrlTemplate.replace('/0/', `/${messageId}/`);
            const params = new URLSearchParams();
            params.set('emoji', emoji);
            fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': (typeof getCookie === 'function' ? getCookie('csrftoken') : ''),
                },
                body: params,
            }).catch(() => {});
            return;
        }

        // Outside click closes pickers
        closeAllReactionPickers();
    });

    function closeAllMessageMenus(exceptMenu) {
        const menus = document.querySelectorAll('[data-message-menu]');
        menus.forEach((menu) => {
            if (exceptMenu && menu === exceptMenu) return;
            menu.classList.add('hidden');
            const toggle = menu.parentElement ? menu.parentElement.querySelector('[data-message-menu-toggle]') : null;
            if (toggle) toggle.setAttribute('aria-expanded', 'false');
        });
    }

    // Toggle actions menu on 3-dots click (not hover)
    document.addEventListener('click', function (e) {
        const toggle = e.target && e.target.closest ? e.target.closest('[data-message-menu-toggle]') : null;
        const insideMenu = e.target && e.target.closest ? e.target.closest('[data-message-menu]') : null;

        if (toggle) {
            e.preventDefault();
            e.stopPropagation();

            const wrapper = toggle.parentElement;
            const menu = wrapper ? wrapper.querySelector('[data-message-menu]') : null;
            if (!menu) return;

            const willOpen = menu.classList.contains('hidden');
            closeAllMessageMenus(willOpen ? menu : null);

            if (willOpen) {
                menu.classList.remove('hidden');
                toggle.setAttribute('aria-expanded', 'true');
            } else {
                menu.classList.add('hidden');
                toggle.setAttribute('aria-expanded', 'false');
            }
            return;
        }

        // Clicks inside menu should not close it
        if (insideMenu) return;

        // Click anywhere else closes menus
        closeAllMessageMenus();
    }, true);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAllMessageMenus();
    }, true);

    // -------- Message Edit/Delete (actions menu) --------
    document.addEventListener('click', async function (e) {
        const editBtn = e.target.closest('[data-edit-message]');
        const delBtn = e.target.closest('[data-delete-message]');

        if (!editBtn && !delBtn) return;
        e.preventDefault();
        e.stopPropagation();

        const messageId = (editBtn || delBtn).dataset.messageId;
        if (!messageId) return;

        // Close any open message menu after choosing an action
        closeAllMessageMenus();

        if (editBtn) {
            const body = editBtn.dataset.messageBody || '';
            startEditingMessage(messageId, body);
            return;
        }

        if (delBtn) {
            const url = messageDeleteUrlTemplate.replace('/0/', `/${messageId}/`);
            try {
                await fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': (typeof getCookie === 'function' ? getCookie('csrftoken') : ''),
                    },
                    body: new URLSearchParams(),
                });
            } catch {
                // ignore
            }
        }
    });

    function ensureIncomingContainer() {
        let c = document.getElementById('incoming-call-container');
        if (c) return c;
        c = document.createElement('div');
        c.id = 'incoming-call-container';
        c.className = 'fixed top-24 right-6 z-50 w-[min(24rem,calc(100vw-3rem))] space-y-3';
        document.body.appendChild(c);
        return c;
    }

    function showIncomingCall(payload) {
        const container = ensureIncomingContainer();
        const from = payload.from_username || 'Someone';
        const type = (payload.call_type || 'voice').toLowerCase() === 'video' ? 'video' : 'voice';

        playIncomingRing();

        const toast = document.createElement('div');
        toast.className = 'pointer-events-auto flex items-start gap-3 rounded-xl border border-gray-800 bg-gray-900/90 px-4 py-3 text-sm text-gray-100 shadow-lg shadow-black/20';
        toast.innerHTML = `
                <div class="mt-0.5 h-2.5 w-2.5 flex-none rounded-full bg-emerald-400"></div>
                <div class="flex-1">
                    <div class="font-semibold">Incoming ${type === 'video' ? 'video' : 'voice'} call</div>
                    <div class="text-gray-300 text-xs mt-0.5">from ${from}</div>
                    <div class="mt-3 flex gap-2">
                        <button type="button" data-accept class="text-xs bg-emerald-500 hover:bg-emerald-600 text-white px-3 py-1.5 rounded-lg transition-colors">Accept</button>
                        <button type="button" data-decline class="text-xs bg-gray-800 hover:bg-gray-700 text-white px-3 py-1.5 rounded-lg transition-colors">Decline</button>
                    </div>
                </div>
                <button type="button" data-close class="-mr-1 -mt-1 inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-300 hover:text-white hover:bg-gray-800/60 transition" aria-label="Dismiss">
                    <span aria-hidden="true">×</span>
                </button>
            `;

        const removeToast = () => {
            toast.classList.add('opacity-0');
            setTimeout(() => toast.remove(), 200);
            stopIncomingRing();
        };
        toast.querySelector('[data-close]')?.addEventListener('click', removeToast);
        toast.querySelector('[data-accept]')?.addEventListener('click', async () => {
            stopIncomingRing();
            window.__hasGlobalCallInvite = true;
            removeToast();
            await openCallPopup(type, 'callee');
        });
        toast.querySelector('[data-decline]')?.addEventListener('click', async () => {
            try {
                const body = new URLSearchParams();
                body.set('action', 'decline');
                body.set('type', type);
                await fetch(callEventUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': (typeof getCookie === 'function' ? getCookie('csrftoken') : ''),
                    },
                    body,
                });
            } catch {
                // ignore
            }
            removeToast();
        });
        setTimeout(removeToast, 15000);

        container.appendChild(toast);
    }

    // --- Call popup (no separate page) ---
    const callPopupEl = document.getElementById('call_popup');
    const callPopupHeaderEl = document.getElementById('call_popup_header');
    const callPopupTitleEl = document.getElementById('call_popup_title');
    const callPopupSubtitleEl = document.getElementById('call_popup_subtitle');
    const callPopupStatusEl = document.getElementById('call_popup_status');
    const callPopupVideoEl = document.getElementById('call_popup_video');
    const callLocalEl = document.getElementById('call_local_player');
    const callRemoteEl = document.getElementById('call_remote_player');
    const callEndBtn = document.getElementById('call_popup_end');

    const callPartMeAvatarEl = document.getElementById('call_part_me_avatar');
    const callPartMeNameEl = document.getElementById('call_part_me_name');
    const callPartMeStateEl = document.getElementById('call_part_me_state');
    const callPartMeBadgeEl = document.getElementById('call_part_me_badge');

    const callPartOtherAvatarEl = document.getElementById('call_part_other_avatar');
    const callPartOtherNameEl = document.getElementById('call_part_other_name');
    const callPartOtherStateEl = document.getElementById('call_part_other_state');
    const callPartOtherBadgeEl = document.getElementById('call_part_other_badge');

    let callActive = false;
    let callTypeActive = null;
    let callRoleActive = null;
    let callClient = null;
    let localTracks = { audio: null, video: null };

    // Remote audio can be blocked by autoplay policies (Chrome/Safari). Keep refs to retry on user gesture.
    const callRemoteAudioTracks = new Map(); // uid -> audioTrack
    let callAudioPlaybackBlocked = false;

    function tryPlayCallRemoteAudio() {
        let playedAny = false;
        for (const track of callRemoteAudioTracks.values()) {
            if (!track) continue;
            try {
                track.play();
                playedAny = true;
            } catch {
                // still blocked
            }
        }
        if (playedAny) {
            callAudioPlaybackBlocked = false;
            setCallStatus('In call');
        }
    }

    document.addEventListener('pointerdown', () => {
        if (!callAudioPlaybackBlocked) return;
        tryPlayCallRemoteAudio();
    }, { passive: true });

    function initCallParticipantsUi() {
        try {
            if (callPartMeNameEl) callPartMeNameEl.textContent = 'You';
            if (callPartMeAvatarEl) callPartMeAvatarEl.src = meAvatarUrl || '';

            if (callPartOtherNameEl) callPartOtherNameEl.textContent = otherDisplayName || otherUsername || 'Participant';
            if (callPartOtherAvatarEl) callPartOtherAvatarEl.src = otherAvatarUrl || '';
        } catch {
            // ignore
        }
    }

    function setParticipant(which, stateText, badgeText) {
        const state = stateText || '';
        const badge = badgeText || '—';
        if (which === 'me') {
            if (callPartMeStateEl) callPartMeStateEl.textContent = state;
            if (callPartMeBadgeEl) callPartMeBadgeEl.textContent = badge;
            return;
        }
        if (which === 'other') {
            if (callPartOtherStateEl) callPartOtherStateEl.textContent = state;
            if (callPartOtherBadgeEl) callPartOtherBadgeEl.textContent = badge;
        }
    }

    initCallParticipantsUi();

    function setCallStatus(text) {
        if (callPopupStatusEl) callPopupStatusEl.textContent = text || '';
        if (callPopupSubtitleEl) callPopupSubtitleEl.textContent = text || '';
    }

    function showCallPopup() {
        if (!callPopupEl) return;
        callPopupEl.classList.remove('hidden');
    }

    function hideCallPopup() {
        if (!callPopupEl) return;
        callPopupEl.classList.add('hidden');
    }

    async function fetchAgoraToken() {
        const res = await fetch(tokenUrl, { credentials: 'same-origin' });

        // Handle rate limit cleanly.
        if (res.status === 429) {
            const retryAfter = Number(res.headers.get('Retry-After') || '10');
            throw new Error(`Rate limited. Try again in ${Number.isFinite(retryAfter) ? retryAfter : 10}s.`);
        }

        let data = null;
        try {
            data = await res.json();
        } catch {
            // Some servers return HTML on errors; include a short hint.
            const text = await res.text().catch(() => '');
            const hint = (text || '').slice(0, 140).replace(/\s+/g, ' ').trim();
            throw new Error(!res.ok ? `Token request failed (${res.status}). ${hint}` : 'Token response was not JSON.');
        }

        if (!res.ok || (data && data.error)) {
            throw new Error((data && data.error) ? String(data.error) : `Token request failed (${res.status}).`);
        }
        return data;
    }

    function getCsrf() {
        try { return (typeof getCookie === 'function' ? getCookie('csrftoken') : ''); } catch { return ''; }
    }

    async function announcePresence(action, uid, type) {
        try {
            const body = new URLSearchParams();
            body.set('action', action);
            body.set('type', type);
            body.set('uid', String(uid || 0));
            await fetch(presenceUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': getCsrf(),
                },
                body,
            });
        } catch {
            // ignore
        }
    }

    async function postCallEvent(action, type) {
        try {
            const body = new URLSearchParams();
            body.set('action', action);
            body.set('type', type);
            await fetch(callEventUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': getCsrf(),
                },
                body,
            });
        } catch {
            // ignore
        }
    }

    async function openCallPopup(type, role) {
        if (callActive) return;
        callActive = true;
        callTypeActive = (type || 'voice');
        callRoleActive = (role || 'caller');

        initCallParticipantsUi();
        setParticipant('me', 'Connecting…', '…');
        setParticipant('other', 'Waiting…', '…');

        showCallPopup();
        if (callPopupTitleEl) callPopupTitleEl.textContent = callTypeActive === 'video' ? 'Video call' : 'Voice call';
        setCallStatus('Connecting…');

        if (callPopupVideoEl) {
            if (callTypeActive === 'video') callPopupVideoEl.classList.remove('hidden');
            else callPopupVideoEl.classList.add('hidden');
        }

        try {
            const { token, uid, channel, app_id } = await fetchAgoraToken();
            if (!app_id) throw new Error('Agora is not configured (missing AGORA_APP_ID).');

            callClient = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });

            callClient.on('user-joined', () => {
                if (callPopupSubtitleEl) {
                    callPopupSubtitleEl.textContent = otherUsername ? `${otherUsername} joined` : 'User joined';
                }
                setParticipant('other', 'Joined', 'On call');
            });

            callClient.on('user-published', async (user, mediaType) => {
                await callClient.subscribe(user, mediaType);
                if (mediaType === 'video' && callRemoteEl) {
                    try { user.videoTrack.play(callRemoteEl); } catch {}
                }
                if (mediaType === 'audio') {
                    try {
                        callRemoteAudioTracks.set(String(user.uid), user.audioTrack);
                        user.audioTrack.play();
                    } catch {
                        callAudioPlaybackBlocked = true;
                        setCallStatus('Tap anywhere to enable audio');
                    }
                }
                setCallStatus('In call');
                setParticipant('other', 'In call', 'On call');
            });

            callClient.on('user-left', () => {
                setCallStatus('Waiting…');
                setParticipant('other', 'Left', '—');
            });

            await callClient.join(app_id, channel, token, uid);
            await announcePresence('join', uid, callTypeActive);

            setParticipant('me', 'In call', 'On call');

            if (callRoleActive === 'caller') {
                await postCallEvent('start', callTypeActive);
            }

            localTracks.audio = await AgoraRTC.createMicrophoneAudioTrack({ AEC: true, AGC: true, ANS: true });
            if (callTypeActive === 'video') {
                localTracks.video = await AgoraRTC.createCameraVideoTrack();
                if (callLocalEl) {
                    try { localTracks.video.play(callLocalEl); } catch {}
                }
                await callClient.publish([localTracks.audio, localTracks.video]);
            } else {
                await callClient.publish([localTracks.audio]);
            }

            setCallStatus('In call');
        } catch (e) {
            setCallStatus('Error: ' + (e && e.message ? e.message : String(e)));

            // Allow retry without refresh.
            try {
                if (localTracks.video) {
                    try { localTracks.video.stop(); } catch {}
                    try { localTracks.video.close(); } catch {}
                }
                if (localTracks.audio) {
                    try { localTracks.audio.stop(); } catch {}
                    try { localTracks.audio.close(); } catch {}
                }
            } catch {}
            try {
                if (callClient) {
                    try { await callClient.leave(); } catch {}
                }
            } catch {}

            callClient = null;
            localTracks = { audio: null, video: null };
            callActive = false;
            callTypeActive = null;
            callRoleActive = null;
            setParticipant('me', 'Waiting…', '—');
            setParticipant('other', 'Waiting…', '—');
        }
    }

    async function endCallPopup(reason) {
        if (!callActive) return;
        try {
            if (reason) setCallStatus(reason);
            // Broadcast end marker (only once is fine; server dedupes)
            if (callTypeActive) await postCallEvent('end', callTypeActive);
        } catch {}

        try {
            callRemoteAudioTracks.clear();
            callAudioPlaybackBlocked = false;
        } catch {}

        try {
            if (localTracks.video) {
                try { localTracks.video.stop(); } catch {}
                try { localTracks.video.close(); } catch {}
            }
            if (localTracks.audio) {
                try { localTracks.audio.stop(); } catch {}
                try { localTracks.audio.close(); } catch {}
            }
        } catch {}

        try {
            if (callClient) {
                try { await callClient.leave(); } catch {}
            }
        } catch {}

        callClient = null;
        localTracks = { audio: null, video: null };
        callActive = false;
        callTypeActive = null;
        callRoleActive = null;
        setParticipant('me', 'Waiting…', '—');
        setParticipant('other', 'Waiting…', '—');
        hideCallPopup();
    }

    if (callEndBtn) {
        callEndBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            endCallPopup('Call ended');
        });
    }

    // Draggable popup
    (function () {
        if (!callPopupEl || !callPopupHeaderEl) return;
        let dragging = false;
        let offsetX = 0;
        let offsetY = 0;

        function stopDrag(e) {
            if (!dragging) return;
            dragging = false;
            try {
                if (e && typeof e.pointerId !== 'undefined') {
                    callPopupHeaderEl.releasePointerCapture(e.pointerId);
                }
            } catch {}
        }

        callPopupHeaderEl.addEventListener('pointerdown', (e) => {
            // Don't start dragging when clicking the End button (or any interactive element).
            const interactive = e.target && e.target.closest ? e.target.closest('button, a, input, select, textarea') : null;
            if (interactive) return;
            // Only left-click for mouse pointers.
            if (typeof e.button === 'number' && e.button !== 0) return;
            dragging = true;
            try { callPopupHeaderEl.setPointerCapture(e.pointerId); } catch {}
            const rect = callPopupEl.getBoundingClientRect();
            offsetX = e.clientX - rect.left;
            offsetY = e.clientY - rect.top;
        });

        callPopupHeaderEl.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            const left = Math.max(8, Math.min(window.innerWidth - callPopupEl.offsetWidth - 8, e.clientX - offsetX));
            const top = Math.max(8, Math.min(window.innerHeight - callPopupEl.offsetHeight - 8, e.clientY - offsetY));
            callPopupEl.style.left = `${left}px`;
            callPopupEl.style.top = `${top}px`;
            callPopupEl.style.right = 'auto';
            callPopupEl.style.bottom = 'auto';
        });

        callPopupHeaderEl.addEventListener('pointerup', (e) => {
            stopDrag(e);
        });

        callPopupHeaderEl.addEventListener('pointercancel', (e) => {
            stopDrag(e);
        });

        // Safety: if pointerup happens outside header, still stop dragging.
        window.addEventListener('pointerup', stopDrag, true);
        window.addEventListener('pointercancel', stopDrag, true);
    })();

    // When clicking call buttons, notify the other member first, then open popup.
    document.addEventListener('click', async function (e) {
        const a = e.target && e.target.closest ? e.target.closest('[data-call-btn]') : null;
        if (!a) return;
        e.preventDefault();

        const type = a.getAttribute('data-call-type') || 'voice';
        try {
            const body = new URLSearchParams();
            body.set('type', type);
            await fetch(inviteUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': (typeof getCookie === 'function' ? getCookie('csrftoken') : ''),
                },
                body,
            });
        } catch {
            // ignore
        }
        await openCallPopup(type, 'caller');
    }, true);

    async function poll() {
        // Fallback: if websocket isn't connected, poll for new messages
        if (wsConnected) return;
        try {
            updateLastIdFromDom();
            const res = await fetch(`${pollUrl}?after=${lastId}`, { credentials: 'same-origin' });
            if (!res.ok) return;
            const data = await res.json();
            if (data && typeof data.online_count !== 'undefined') {
                if (onlineCountEl) onlineCountEl.textContent = data.online_count;
            }
            if (data && data.messages_html) {
                const emptyEl = document.getElementById('empty_state');
                if (emptyEl) emptyEl.remove();
                if (messagesEl && messagesEl.classList.contains('hidden')) {
                    messagesEl.classList.remove('hidden');
                }
                messagesEl.insertAdjacentHTML('beforeend', data.messages_html);
                hydrateLocalTimes(messagesEl);
                updateLastIdFromDom();
                if (__chatAutoScrollEnabled) forceScrollToBottomNow();
            }
            if (data && typeof data.last_id !== 'undefined') {
                const newLast = parseInt(String(data.last_id), 10);
                if (!Number.isNaN(newLast) && newLast > lastId) lastId = newLast;
            }
        } catch {
            // ignore
        }
    }

    // Initial load: ensure we land on the latest message.
    // (On first render we are not "near bottom", so guarded autoscroll would do nothing.)
    (function initialScrollToLatest() {
        if (window.__didInitialChatScroll) return;
        window.__didInitialChatScroll = true;

        // Run a few times to handle layout/avatars loading.
        try {
            requestAnimationFrame(() => {
                forceScrollToBottomNow();
                requestAnimationFrame(() => forceScrollToBottomNow());
            });
        } catch {
            // ignore
        }
        setTimeout(forceScrollToBottomNow, 0);
        setTimeout(forceScrollToBottomNow, 120);
        setTimeout(forceScrollToBottomNow, 320);
    })();

    setInterval(poll, 1200);
    connect();

    function clearReply() {
        if (replyToIdInput) replyToIdInput.value = '';
        if (replyBar) replyBar.classList.add('hidden');
        if (replyBarAuthor) replyBarAuthor.textContent = '';
        if (replyBarPreview) replyBarPreview.textContent = '';
    }

    if (replyBarCancel) {
        replyBarCancel.addEventListener('click', function () {
            clearReply();
            if (input) input.focus();
        });
    }

    // Click "Reply" on a message bubble
    document.addEventListener('click', function (e) {
        const btn = e.target && e.target.closest ? e.target.closest('[data-reply-button]') : null;
        if (!btn) return;

        const msgId = btn.getAttribute('data-reply-to-id') || '';
        const author = btn.getAttribute('data-reply-author') || '';
        const preview = btn.getAttribute('data-reply-preview') || '';

        if (replyToIdInput) replyToIdInput.value = msgId;
        if (replyBarAuthor) replyBarAuthor.textContent = author;
        if (replyBarPreview) replyBarPreview.textContent = preview;
        if (replyBar) replyBar.classList.remove('hidden');
        if (input) input.focus();
    }, true);

    // Click replied-to snippet to jump to original
    document.addEventListener('click', function (e) {
        const jump = e.target && e.target.closest ? e.target.closest('[data-scroll-to]') : null;
        if (!jump) return;
        const targetId = jump.getAttribute('data-scroll-to');
        if (!targetId) return;
        const el = document.getElementById(targetId);
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, true);

    (function initMobileSidebarToggle() {
        const sidebar = document.getElementById('chat_sidebar');
        const panel = document.getElementById('chat_sidebar_panel');
        const openBtn = document.getElementById('chat_sidebar_open');
        const closeBtn = document.getElementById('chat_sidebar_close');
        if (!sidebar || !panel || !openBtn || !closeBtn) return;

        const isMobile = () => !window.matchMedia('(min-width: 1024px)').matches;

        const overlayClasses = [
            'fixed', 'inset-0', 'z-50',
            'flex', 'items-start', 'justify-center',
            'bg-gray-900/60', 'p-4', 'pt-20'
        ];

        function closeSidebar() {
            if (!isMobile()) return;
            sidebar.classList.add('hidden');
            sidebar.classList.remove(...overlayClasses);
            document.body.classList.remove('overflow-hidden');
        }

        function openSidebar() {
            if (!isMobile()) return;
            sidebar.classList.remove('hidden');
            sidebar.classList.add(...overlayClasses);
            document.body.classList.add('overflow-hidden');
        }

        function sync() {
            if (!isMobile()) {
                sidebar.classList.remove('hidden');
                sidebar.classList.remove(...overlayClasses);
                document.body.classList.remove('overflow-hidden');
                return;
            }
            closeSidebar();
        }

        openBtn.addEventListener('click', openSidebar);
        closeBtn.addEventListener('click', closeSidebar);

        sidebar.addEventListener('click', (e) => {
            if (e.target === sidebar) {
                closeSidebar();
                return;
            }

            const link = e.target && e.target.closest ? e.target.closest('a') : null;
            if (!link) return;
            closeSidebar();
        });

        window.addEventListener('resize', sync);
        sync();
    })();
})();
