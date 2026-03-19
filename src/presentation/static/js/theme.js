(function() {
    const themeConfig = window.__LLM_PROXY_THEME_CONFIG__ || {};
    const defaultTheme = themeConfig.defaultTheme === 'light' ? 'light' : 'dark';

    function normalizeTheme(theme) {
        return theme === 'light' ? 'light' : 'dark';
    }

    function getCurrentTheme() {
        return normalizeTheme(document.documentElement.getAttribute('data-theme') || defaultTheme);
    }

    function applyTheme(theme) {
        const nextTheme = normalizeTheme(theme);
        document.documentElement.setAttribute('data-theme', nextTheme);
        localStorage.setItem('theme', nextTheme);

        const themeToggle = document.getElementById('themeToggle');
        if (!themeToggle) {
            return nextTheme;
        }

        const label = nextTheme === 'dark' ? '切换到浅色主题' : '切换到深色主题';
        themeToggle.setAttribute('aria-label', label);
        themeToggle.setAttribute('title', label);
        return nextTheme;
    }

    function initThemeToggle() {
        const themeToggle = document.getElementById('themeToggle');
        if (!themeToggle) {
            return;
        }

        applyTheme(getCurrentTheme());
        themeToggle.addEventListener('click', function() {
            const currentTheme = getCurrentTheme();
            applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
        });
    }

    window.applyTheme = applyTheme;
    window.initThemeToggle = initThemeToggle;
})();
