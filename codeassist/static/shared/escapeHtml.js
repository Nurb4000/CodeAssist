// Canonical HTML-escaping helper, loaded (as a classic script) before the page
// scripts that use it so every page shares one implementation. Escapes &, <, >,
// " and ' -- the DOM-based variant (textContent -> innerHTML) left quotes
// untouched, which was unsafe where values are interpolated into double-quoted
// attributes (value="...") or single-quoted inline onclick="..." strings: the
// browser HTML-decodes the attribute before parsing JS, so an unescaped quote
// breaks out and injects markup. Kept dependency-free on purpose.
function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
