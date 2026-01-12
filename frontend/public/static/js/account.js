(function () {
    // Allauth "Email addresses" page: add a friendly placeholder.
    try {
        const el = document.getElementById('id_email');
        if (el && !el.getAttribute('placeholder')) {
            el.setAttribute('placeholder', 'you@example.com');
        }
    } catch {
        // ignore
    }

    // Lucide icon hydration (used on login/signup forms).
    try {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    } catch {
        // ignore
    }

    // Password show/hide toggle is initialized globally in vixogram.js.
    // Keep account.js lean to avoid duplicate event handlers.
})();
