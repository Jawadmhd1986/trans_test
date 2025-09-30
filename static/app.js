
// Generate unique nonce for requests
function generateNonce() {
    return 'nonce_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
}

// Global utilities
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 3000);
}

function detectDevice() {
    return /Mobi|Android/i.test(navigator.userAgent) ? 'mobile' : 'desktop';
}

function normalizeCode(code) {
    if (!code) return '';
    return code.trim().toUpperCase().replace(/[^A-Z0-9\-_]/g, '');
}

// Handle online/offline status
window.addEventListener('online', () => {
    showToast('Connection restored', 'success');
});

window.addEventListener('offline', () => {
    showToast('Working offline', 'error');
});
