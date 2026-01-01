/**
 * TrackUI - Frontend Application
 * All frontend logic: modals, API calls, polling, filtering, etc.
 */

// =============================================================================
// State Management
// =============================================================================

const state = {
    users: [],
    tags: [],
    queue: [],
    settings: {},
    filters: {
        platform: '',
        tag: '',
        search: ''
    },
    pollingInterval: null
};

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    initModals();
    initFilters();
    initQueuePolling();
    initTooltips();
    initLazyLoading();
    loadSettings();
    initTheme();
    checkFirstTimeSetup();
    initViewMode();
});

// View Mode (Grid/List)
function initViewMode() {
    const savedMode = localStorage.getItem('dashboard_view_mode') || 'grid';
    setViewMode(savedMode);
}

function setViewMode(mode) {
    const grid = document.querySelector('.user-grid');
    if (!grid) return;

    // Update grid class
    if (mode === 'list') {
        grid.classList.add('list-view');
    } else {
        grid.classList.remove('list-view');
    }

    // Update toggle buttons
    document.querySelectorAll('.view-toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === mode);
    });

    // Save preference
    localStorage.setItem('dashboard_view_mode', mode);
}

// Register service worker for PWA
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
        .catch(err => console.log('SW registration failed:', err));
}

// =============================================================================
// Theme Toggle
// =============================================================================

function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);

    showToast(`Switched to ${newTheme} mode`, 'info');
}

function updateThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle');
    if (btn) {
        btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    }
}

// =============================================================================
// Auto-Lock & Panic Button
// =============================================================================

let autoLockTimer = null;
let autoLockEnabled = false;
let autoLockDelay = 5; // minutes
let panicEnabled = true;
let panicKeyPresses = [];

function initAutoLock() {
    // Load settings from localStorage (quick access)
    autoLockEnabled = localStorage.getItem('auto_lock_enabled') === 'true';
    autoLockDelay = parseInt(localStorage.getItem('auto_lock_delay') || '5');
    panicEnabled = localStorage.getItem('panic_button_enabled') !== 'false';

    if (autoLockEnabled) {
        resetAutoLockTimer();

        // Reset timer on user activity
        ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(event => {
            document.addEventListener(event, resetAutoLockTimer, { passive: true });
        });
    }

    // Panic button listener (Esc 3x quickly)
    if (panicEnabled) {
        document.addEventListener('keydown', handlePanicKey);
    }
}

function resetAutoLockTimer() {
    if (autoLockTimer) {
        clearTimeout(autoLockTimer);
    }

    if (autoLockEnabled) {
        autoLockTimer = setTimeout(() => {
            // Lock the app
            window.location.href = '/logout';
        }, autoLockDelay * 60 * 1000);
    }
}

function handlePanicKey(e) {
    if (e.key === 'Escape' && panicEnabled) {
        const now = Date.now();
        panicKeyPresses.push(now);

        // Keep only presses from last 1 second
        panicKeyPresses = panicKeyPresses.filter(t => now - t < 1000);

        if (panicKeyPresses.length >= 3) {
            // Panic! Lock and redirect to innocent page
            window.location.href = 'https://www.google.com';
        }
    }
}

function updateAutoLockSettings() {
    const enabled = document.getElementById('auto-lock-enabled')?.checked;
    const delay = document.getElementById('auto-lock-delay')?.value;
    const panicOn = document.getElementById('panic-button-enabled')?.checked;

    localStorage.setItem('auto_lock_enabled', enabled ? 'true' : 'false');
    localStorage.setItem('auto_lock_delay', delay || '5');
    localStorage.setItem('panic_button_enabled', panicOn ? 'true' : 'false');

    autoLockEnabled = enabled;
    autoLockDelay = parseInt(delay || '5');
    panicEnabled = panicOn;

    if (enabled) {
        resetAutoLockTimer();
    } else if (autoLockTimer) {
        clearTimeout(autoLockTimer);
    }
}

// Initialize auto-lock on page load
document.addEventListener('DOMContentLoaded', initAutoLock);

// =============================================================================
// Toast Notifications
// =============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container') || createToastContainer();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${getToastIcon(type)}</span>
        <span class="toast-message">${message}</span>
    `;

    container.appendChild(toast);

    // Remove after animation
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
    return container;
}

function getToastIcon(type) {
    const icons = {
        success: '✓',
        error: '✕',
        warning: '⚠',
        info: 'ℹ'
    };
    return icons[type] || icons.info;
}

// =============================================================================
// Modal Management
// =============================================================================

function initModals() {
    // Close modal on backdrop click
    document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) {
                closeAllModals();
            }
        });
    });

    // Close modal on escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeAllModals();
        }
    });

    // Close buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', closeAllModals);
    });
}

function openModal(modalId) {
    const backdrop = document.getElementById('modal-backdrop');
    const modal = document.getElementById(modalId);

    if (backdrop && modal) {
        backdrop.classList.add('active');
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';

        // Load all cookies when settings modal opens
        if (modalId === 'settings-modal') {
            loadAllCookies();
        }
    }
}

function closeModal(modalId) {
    const backdrop = document.getElementById('modal-backdrop');
    const modal = document.getElementById(modalId);

    if (modal) {
        modal.classList.remove('active');
    }

    // Check if any modals are still open
    const openModals = document.querySelectorAll('.modal.active');
    if (openModals.length === 0 && backdrop) {
        backdrop.classList.remove('active');
        document.body.style.overflow = '';
    }
}

function closeAllModals() {
    const backdrop = document.getElementById('modal-backdrop');
    document.querySelectorAll('.modal.active').forEach(modal => {
        modal.classList.remove('active');
    });

    if (backdrop) {
        backdrop.classList.remove('active');
    }
    document.body.style.overflow = '';
}

// =============================================================================
// User Management
// =============================================================================

function toggleCoomerFields() {
    const platform = document.getElementById('add-platform').value;
    const serviceGroup = document.getElementById('coomer-service-group');
    if (serviceGroup) {
        if (platform === 'coomer') {
            serviceGroup.classList.remove('hidden');
        } else {
            serviceGroup.classList.add('hidden');
        }
    }
}

async function addUser(event) {
    event.preventDefault();

    const form = event.target;
    const username = form.querySelector('#add-username').value.trim();
    const platform = form.querySelector('#add-platform').value;

    // Get coomer service if applicable
    let coomerService = null;
    if (platform === 'coomer') {
        const serviceSelect = form.querySelector('#add-coomer-service');
        if (serviceSelect) {
            coomerService = serviceSelect.value;
        }
    }

    if (!username || !platform) {
        showToast('Please fill in all fields', 'error');
        return;
    }

    try {
        const body = { username, platform };
        if (coomerService) {
            body.coomer_service = coomerService;
        }

        const response = await fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`Added ${platform}/${username}`, 'success');
            closeAllModals();
            form.reset();
            toggleCoomerFields(); // Reset visibility
            location.reload(); // Refresh to show new user
        } else {
            showToast(data.error || 'Failed to add user', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function deleteUser(userId, username) {
    if (!confirm(`Delete ${username}? This cannot be undone.`)) {
        return;
    }

    const deleteFiles = confirm('Also delete downloaded files?');

    try {
        const response = await fetch(`/api/users/${userId}?delete_files=${deleteFiles}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showToast(`Deleted ${username}`, 'success');
            // Remove card from DOM
            const card = document.querySelector(`[data-user-id="${userId}"]`);
            if (card) {
                card.remove();
            }
        } else {
            showToast('Failed to delete user', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function syncUser(userId, username) {
    try {
        const response = await fetch(`/api/users/${userId}/sync`, {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`Started sync for ${username}`, 'success');
            openModal('downloads-modal');
        } else {
            showToast(data.error || 'Failed to start sync', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function syncAllUsers() {
    if (!confirm('Start sync for all tracked users?')) {
        return;
    }

    try {
        const response = await fetch('/api/sync-all', {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast(data.message, 'success');
            openModal('downloads-modal');
        } else {
            showToast(data.error || 'Failed to start sync', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

// =============================================================================
// Tag Management
// =============================================================================

async function loadTags() {
    try {
        const response = await fetch('/api/tags');
        state.tags = await response.json();
        renderTagsList();
    } catch (error) {
        console.error('Failed to load tags:', error);
    }
}

function renderTagsList() {
    const container = document.getElementById('tags-list');
    if (!container) return;

    if (state.tags.length === 0) {
        container.innerHTML = '<p class="text-muted">No tags created yet</p>';
        return;
    }

    container.innerHTML = state.tags.map(tag => `
        <div class="cookie-item" data-tag-id="${tag.id}">
            <span class="tag" style="background-color: ${tag.color}20; color: ${tag.color}; border: 1px solid ${tag.color}">
                ${tag.name}
            </span>
            <div class="cookie-item-actions">
                <button class="btn btn-ghost btn-icon" onclick="editTag(${tag.id})" title="Edit">✏️</button>
                <button class="btn btn-ghost btn-icon" onclick="deleteTag(${tag.id})" title="Delete">🗑️</button>
            </div>
        </div>
    `).join('');
}

async function addTag(event) {
    event.preventDefault();

    const form = event.target;
    const name = form.querySelector('#tag-name').value.trim();
    const color = form.querySelector('#tag-color').value;

    if (!name) {
        showToast('Please enter a tag name', 'error');
        return;
    }

    try {
        const response = await fetch('/api/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, color })
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`Created tag "${name}"`, 'success');
            form.reset();
            form.querySelector('#tag-color').value = '#3b82f6';
            loadTags();
        } else {
            showToast(data.error || 'Failed to create tag', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function deleteTag(tagId) {
    if (!confirm('Delete this tag?')) {
        return;
    }

    try {
        const response = await fetch(`/api/tags/${tagId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showToast('Tag deleted', 'success');
            loadTags();
        } else {
            showToast('Failed to delete tag', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function assignTag(userId, tagId) {
    try {
        const response = await fetch(`/api/users/${userId}/tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_id: tagId })
        });

        if (response.ok) {
            showToast('Tag assigned', 'success');
            location.reload();
        } else {
            showToast('Failed to assign tag', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

async function removeTag(userId, tagId) {
    try {
        const response = await fetch(`/api/users/${userId}/tags`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_id: tagId })
        });

        if (response.ok) {
            showToast('Tag removed', 'success');
            location.reload();
        } else {
            showToast('Failed to remove tag', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

function openTagAssignModal(userId, currentTags) {
    // Build modal content with available tags
    const availableTags = state.tags.filter(t => !currentTags.includes(t.id));

    let content = '<div class="form-group"><label class="form-label">Select tag to assign:</label>';

    if (availableTags.length === 0) {
        content += '<p class="text-muted">No more tags available</p>';
    } else {
        content += '<div class="flex flex-col gap-sm">';
        availableTags.forEach(tag => {
            content += `
                <button class="btn btn-secondary" onclick="assignTag(${userId}, ${tag.id}); closeAllModals();">
                    <span class="tag" style="background-color: ${tag.color}20; color: ${tag.color}">${tag.name}</span>
                </button>
            `;
        });
        content += '</div>';
    }
    content += '</div>';

    // Show in a temporary modal or use existing
    const modal = document.getElementById('tag-assign-modal');
    if (modal) {
        modal.querySelector('.modal-body').innerHTML = content;
        openModal('tag-assign-modal');
    }
}

// =============================================================================
// Random User
// =============================================================================

async function randomUser() {
    try {
        const response = await fetch('/api/users/random');
        const data = await response.json();

        if (response.ok && data.username) {
            showToast(`🎲 Random pick: ${data.display_name || data.username}`, 'info');
            window.location.href = `/user/${data.platform}/${data.username}`;
        } else {
            showToast(data.error || 'No users found', 'error');
        }
    } catch (error) {
        showToast('Failed to get random user', 'error');
        console.error(error);
    }
}

// =============================================================================
// Download Queue
// =============================================================================

function initQueuePolling() {
    // Poll queue status every 2 seconds
    state.pollingInterval = setInterval(updateQueue, 2000);
    updateQueue();
}

async function updateQueue() {
    try {
        const response = await fetch('/api/queue');
        state.queue = await response.json();
        renderQueue();
        updateFloatingButton();
    } catch (error) {
        console.error('Failed to update queue:', error);
    }
}

function renderQueue() {
    const container = document.getElementById('queue-list');
    if (!container) return;

    if (state.queue.length === 0) {
        container.innerHTML = `
            <div class="queue-empty">
                <p>📭 No downloads in queue</p>
            </div>
        `;
        return;
    }

    // Group by status
    const active = state.queue.filter(j => j.status === 'active');
    const queued = state.queue.filter(j => j.status === 'queued');
    const paused = state.queue.filter(j => j.status === 'paused');
    const completed = state.queue.filter(j => j.status === 'completed');
    const failed = state.queue.filter(j => j.status === 'failed');

    let html = '';

    if (active.length > 0) {
        html += '<div class="queue-section"><h4>🔄 Active</h4>';
        html += active.map(j => renderQueueItem(j)).join('');
        html += '</div>';
    }

    if (queued.length > 0) {
        html += '<div class="queue-section"><h4>⏳ Queued</h4>';
        html += queued.map(j => renderQueueItem(j)).join('');
        html += '</div>';
    }

    if (paused.length > 0) {
        html += '<div class="queue-section"><h4>⏸️ Paused</h4>';
        html += paused.map(j => renderQueueItem(j)).join('');
        html += '</div>';
    }

    if (completed.length > 0) {
        html += '<div class="queue-section"><h4>✅ Completed</h4>';
        html += completed.slice(0, 10).map(j => renderQueueItem(j)).join('');
        html += '</div>';
    }

    if (failed.length > 0) {
        html += '<div class="queue-section"><h4>❌ Failed</h4>';
        html += failed.map(j => renderQueueItem(j)).join('');
        html += '</div>';
    }

    container.innerHTML = html;
}

function renderQueueItem(job) {
    const statusClass = job.status;
    const progress = job.status === 'completed' ? 100 : (job.status === 'active' ? 50 : 0);

    let actions = '';
    if (job.status === 'active') {
        actions = `<button class="btn btn-ghost btn-icon" onclick="pauseDownload(${job.id})" title="Pause">⏸️</button>`;
    } else if (job.status === 'paused') {
        actions = `<button class="btn btn-ghost btn-icon" onclick="resumeDownload(${job.id})" title="Resume">▶️</button>`;
    } else if (job.status === 'queued') {
        actions = `<button class="btn btn-ghost btn-icon" onclick="cancelDownload(${job.id})" title="Cancel">✕</button>`;
    }

    return `
        <div class="queue-item ${statusClass}">
            <div class="queue-item-info">
                <div class="queue-item-title">${job.platform}/${job.username}</div>
                <div class="queue-item-status">${job.message}</div>
                ${job.status === 'active' ? `
                    <div class="queue-item-progress">
                        <div class="queue-item-progress-bar" style="width: ${progress}%"></div>
                    </div>
                ` : ''}
            </div>
            <div class="queue-item-actions">
                ${actions}
            </div>
        </div>
    `;
}

function updateFloatingButton() {
    const badge = document.getElementById('queue-badge');
    if (!badge) return;

    const activeCount = state.queue.filter(j => j.status === 'active' || j.status === 'queued').length;

    if (activeCount > 0) {
        badge.textContent = activeCount;
        badge.classList.remove('hidden');
    } else {
        badge.classList.add('hidden');
    }
}

async function pauseDownload(queueId) {
    try {
        await fetch(`/api/queue/${queueId}/pause`, { method: 'POST' });
        showToast('Download paused', 'info');
        updateQueue();
    } catch (error) {
        showToast('Failed to pause download', 'error');
    }
}

async function resumeDownload(queueId) {
    try {
        await fetch(`/api/queue/${queueId}/resume`, { method: 'POST' });
        showToast('Download resumed', 'info');
        updateQueue();
    } catch (error) {
        showToast('Failed to resume download', 'error');
    }
}

async function cancelDownload(queueId) {
    try {
        await fetch(`/api/queue/${queueId}`, { method: 'DELETE' });
        showToast('Download cancelled', 'info');
        updateQueue();
    } catch (error) {
        showToast('Failed to cancel download', 'error');
    }
}

async function clearCompletedDownloads() {
    try {
        await fetch('/api/queue/clear', { method: 'POST' });
        showToast('Cleared completed downloads', 'success');
        updateQueue();
    } catch (error) {
        showToast('Failed to clear downloads', 'error');
    }
}

// External URL Download
async function downloadExternalUrl(event) {
    event.preventDefault();

    const form = event.target;
    const url = form.querySelector('#external-url').value.trim();
    const folder = form.querySelector('#external-folder').value.trim() || 'external';

    if (!url) {
        showToast('Please enter a URL', 'error');
        return;
    }

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, folder })
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Added to download queue', 'success');
            form.reset();
            openModal('downloads-modal');
        } else {
            showToast(data.error || 'Failed to add download', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

// =============================================================================
// Filtering
// =============================================================================

function initFilters() {
    const platformFilter = document.getElementById('filter-platform');
    const tagFilter = document.getElementById('filter-tag');
    const searchInput = document.getElementById('filter-search');

    if (platformFilter) {
        platformFilter.addEventListener('change', () => {
            state.filters.platform = platformFilter.value;
            applyFilters();
        });
    }

    if (tagFilter) {
        tagFilter.addEventListener('change', () => {
            state.filters.tag = tagFilter.value;
            applyFilters();
        });
    }

    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                state.filters.search = searchInput.value.toLowerCase();
                applyFilters();
            }, 300);
        });
    }
}

function applyFilters() {
    const cards = document.querySelectorAll('.user-card');

    cards.forEach(card => {
        const platform = card.dataset.platform;
        const username = card.dataset.username?.toLowerCase() || '';
        const displayName = card.dataset.displayName?.toLowerCase() || '';
        const tags = card.dataset.tags?.split(',') || [];

        let visible = true;

        // Platform filter
        if (state.filters.platform && platform !== state.filters.platform) {
            visible = false;
        }

        // Tag filter
        if (state.filters.tag && !tags.includes(state.filters.tag)) {
            visible = false;
        }

        // Search filter
        if (state.filters.search) {
            const searchMatch = username.includes(state.filters.search) ||
                displayName.includes(state.filters.search);
            if (!searchMatch) {
                visible = false;
            }
        }

        card.style.display = visible ? '' : 'none';
    });
}

// =============================================================================
// Settings
// =============================================================================

async function loadSettings() {
    try {
        const response = await fetch('/api/settings');
        state.settings = await response.json();
        populateSettingsForm();
        loadPasswordStatus();
    } catch (error) {
        console.error('Failed to load settings:', error);
    }
}

function populateSettingsForm() {
    // Scheduler
    const schedulerEnabled = document.getElementById('scheduler-enabled');
    const schedulerTime = document.getElementById('scheduler-time');
    const schedulerInterval = document.getElementById('scheduler-interval');

    if (schedulerEnabled) {
        schedulerEnabled.checked = state.settings.scheduler_enabled === 'true';
    }
    if (schedulerTime) {
        schedulerTime.value = state.settings.scheduler_time || '03:00';
    }
    if (schedulerInterval) {
        schedulerInterval.value = state.settings.scheduler_interval || 'daily';
    }

    // Telegram
    const telegramToken = document.getElementById('telegram-token');
    const telegramChatId = document.getElementById('telegram-chat-id');

    if (telegramToken) {
        telegramToken.value = state.settings.telegram_bot_token || '';
    }
    if (telegramChatId) {
        telegramChatId.value = state.settings.telegram_chat_id || '';
    }

    // Downloads
    const maxConcurrent = document.getElementById('max-concurrent');
    if (maxConcurrent) {
        maxConcurrent.value = state.settings.max_concurrent_downloads || '2';
    }

    // Auto-lock settings from localStorage
    const autoLockEnabled = document.getElementById('auto-lock-enabled');
    const autoLockDelay = document.getElementById('auto-lock-delay');
    const panicButtonEnabled = document.getElementById('panic-button-enabled');

    if (autoLockEnabled) {
        autoLockEnabled.checked = localStorage.getItem('auto_lock_enabled') === 'true';
    }
    if (autoLockDelay) {
        autoLockDelay.value = localStorage.getItem('auto_lock_delay') || '5';
    }
    if (panicButtonEnabled) {
        panicButtonEnabled.checked = localStorage.getItem('panic_button_enabled') !== 'false';
    }
}

async function saveSettings(event) {
    event.preventDefault();

    const settings = {
        scheduler_enabled: document.getElementById('scheduler-enabled')?.checked ? 'true' : 'false',
        scheduler_time: document.getElementById('scheduler-time')?.value || '03:00',
        scheduler_interval: document.getElementById('scheduler-interval')?.value || 'daily',
        telegram_bot_token: document.getElementById('telegram-token')?.value || '',
        telegram_chat_id: document.getElementById('telegram-chat-id')?.value || '',
        max_concurrent_downloads: document.getElementById('max-concurrent')?.value || '2'
    };

    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        if (response.ok) {
            showToast('Settings saved', 'success');
            state.settings = settings;
            updateAutoLockSettings(); // Save auto-lock settings to localStorage
        } else {
            showToast('Failed to save settings', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

async function refreshAvatars() {
    if (!confirm('This will re-download all profile pictures. Continue?')) {
        return;
    }

    showToast('Refreshing avatars...', 'info');

    try {
        const response = await fetch('/api/refresh-avatars', {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`${data.message}`, 'success');
        } else {
            showToast(data.error || 'Failed to refresh avatars', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
        console.error(error);
    }
}

// =============================================================================
// Password Protection
// =============================================================================

function togglePasswordFields() {
    const enabled = document.getElementById('password-enabled').checked;
    const fields = document.getElementById('password-fields');
    fields.style.display = enabled ? 'block' : 'none';
}

async function loadPasswordStatus() {
    try {
        const response = await fetch('/api/password/status');
        const data = await response.json();
        document.getElementById('password-enabled').checked = data.enabled;
        togglePasswordFields();
    } catch (error) {
        console.error('Error loading password status:', error);
    }
}

async function savePassword() {
    const password = document.getElementById('app-password').value;
    const confirm = document.getElementById('app-password-confirm').value;

    if (!password) {
        showToast('Please enter a password', 'error');
        return;
    }

    if (password !== confirm) {
        showToast('Passwords do not match', 'error');
        return;
    }

    if (password.length < 4) {
        showToast('Password must be at least 4 characters', 'error');
        return;
    }

    try {
        const response = await fetch('/api/password/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Password set successfully! App is now protected.', 'success');
            document.getElementById('app-password').value = '';
            document.getElementById('app-password-confirm').value = '';
        } else {
            showToast(data.error || 'Failed to set password', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

async function removePassword() {
    if (!confirm('Remove password protection? The app will be accessible without logging in.')) {
        return;
    }

    try {
        const response = await fetch('/api/password/remove', {
            method: 'POST'
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Password protection removed', 'success');
            document.getElementById('password-enabled').checked = false;
            togglePasswordFields();
        } else {
            showToast(data.error || 'Failed to remove password', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

// =============================================================================
// First Time Setup Wizard
// =============================================================================

let setupCurrentStep = 1;
const setupTotalSteps = 6;
let setupCookieFile = null;

async function checkFirstTimeSetup() {
    try {
        const response = await fetch('/api/setup/status');
        const data = await response.json();

        if (!data.completed) {
            openModal('setup-wizard-modal');
        }
    } catch (error) {
        console.error('Failed to check setup status:', error);
    }
}

function setupUpdateUI() {
    // Update step indicators
    document.querySelectorAll('.setup-step').forEach(el => {
        const step = parseInt(el.dataset.step);
        el.classList.remove('active', 'completed');
        if (step === setupCurrentStep) {
            el.classList.add('active');
        } else if (step < setupCurrentStep) {
            el.classList.add('completed');
        }
    });

    // Show/hide content
    for (let i = 1; i <= setupTotalSteps; i++) {
        const content = document.getElementById(`setup-step-${i}`);
        if (content) {
            content.classList.toggle('hidden', i !== setupCurrentStep);
        }
    }

    // Update buttons
    document.getElementById('setup-prev-btn').style.display = setupCurrentStep > 1 ? '' : 'none';
    document.getElementById('setup-next-btn').textContent = setupCurrentStep >= setupTotalSteps ? 'Finish' : 'Next';

    // First step: show "Get Started" instead of "Next"
    if (setupCurrentStep === 1) {
        document.getElementById('setup-next-btn').textContent = 'Get Started';
    }
}

async function setupRestoreBackup(event) {
    const file = event.target.files[0];
    if (!file) return;

    const statusEl = document.getElementById('setup-backup-status');
    statusEl.textContent = 'Restoring backup...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/restore', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            statusEl.textContent = '✅ Backup restored! Reloading...';
            await fetch('/api/setup/complete', { method: 'POST' });
            setTimeout(() => location.reload(), 1500);
        } else {
            statusEl.textContent = '❌ ' + (data.error || 'Failed to restore backup');
        }
    } catch (error) {
        statusEl.textContent = '❌ Network error';
    }

    event.target.value = '';
}

async function setupNextStep() {
    // Save current step data based on step number
    // Steps 1-2: Welcome and Backup (no action needed, just navigation)

    if (setupCurrentStep === 3) {
        // Save password if provided
        const password = document.getElementById('setup-password').value;
        const confirm = document.getElementById('setup-password-confirm').value;

        if (password) {
            if (password !== confirm) {
                showToast('Passwords do not match', 'error');
                return;
            }
            if (password.length < 4) {
                showToast('Password must be at least 4 characters', 'error');
                return;
            }
            await fetch('/api/password/set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password })
            });
        }

        // Save auto-lock settings
        const autoLockOn = document.getElementById('setup-auto-lock')?.checked;
        const autoLockDelayVal = document.getElementById('setup-auto-lock-delay')?.value;
        localStorage.setItem('auto_lock_enabled', autoLockOn ? 'true' : 'false');
        localStorage.setItem('auto_lock_delay', autoLockDelayVal || '5');
    } else if (setupCurrentStep === 4) {
        // Save Telegram settings
        const token = document.getElementById('setup-telegram-token').value;
        const chatId = document.getElementById('setup-telegram-chat').value;

        if (token || chatId) {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    telegram_bot_token: token,
                    telegram_chat_id: chatId
                })
            });
        }
    } else if (setupCurrentStep === 5) {
        // Save scheduler settings
        const enabled = document.getElementById('setup-scheduler-enabled').checked;
        const time = document.getElementById('setup-scheduler-time').value;

        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scheduler_enabled: enabled ? 'true' : 'false',
                scheduler_time: time
            })
        });
    } else if (setupCurrentStep === 6) {
        // Upload cookie if selected
        if (setupCookieFile) {
            const formData = new FormData();
            formData.append('file', setupCookieFile);
            await fetch('/api/cookies/upload', {
                method: 'POST',
                body: formData
            });
        }
    }

    if (setupCurrentStep >= setupTotalSteps) {
        finishSetup();
    } else {
        setupCurrentStep++;
        setupUpdateUI();
    }
}

function setupPrevStep() {
    if (setupCurrentStep > 1) {
        setupCurrentStep--;
        setupUpdateUI();
    }
}

async function skipSetup() {
    finishSetup();
}

async function finishSetup() {
    try {
        await fetch('/api/setup/complete', { method: 'POST' });
        closeModal('setup-wizard-modal');
        showToast('🎉 Setup complete! Welcome to TrackUI!', 'success');
    } catch (error) {
        console.error('Failed to complete setup:', error);
    }
}

function setupCookieSelected(event) {
    const file = event.target.files[0];
    if (file) {
        setupCookieFile = file;
        document.getElementById('setup-cookie-status').textContent = `Selected: ${file.name}`;
    }
}

// =============================================================================
// Cookie Management
// =============================================================================

async function loadCookies(platform = 'instagram') {
    try {
        const response = await fetch(`/api/cookies?platform=${platform}`);
        const cookies = await response.json();
        renderCookiesList(cookies, platform);
    } catch (error) {
        console.error('Failed to load cookies:', error);
    }
}

// Load all platform cookies on settings open
function loadAllCookies() {
    loadCookies('instagram');
    loadCookies('tiktok');
}

function renderCookiesList(cookies, platform = 'instagram') {
    const containerId = platform === 'tiktok' ? 'tiktok-cookies-list' : 'cookies-list';
    const container = document.getElementById(containerId);
    if (!container) return;

    if (cookies.length === 0) {
        container.innerHTML = '<p class="text-muted">No cookies uploaded yet</p>';
        return;
    }

    container.innerHTML = cookies.map(cookie => `
        <div class="cookie-item ${cookie.is_default ? 'default' : ''}">
            <div class="cookie-item-info">
                <div class="cookie-item-name">
                    ${cookie.filename}
                    ${cookie.is_default ? '<span class="badge badge-success">Default</span>' : ''}
                </div>
                <div class="cookie-item-meta">
                    ${formatBytes(cookie.size)} • Last modified: ${formatDate(cookie.modified * 1000)}
                </div>
            </div>
            <div class="cookie-item-actions">
                ${!cookie.is_default ? `
                    <button class="btn btn-ghost btn-icon" onclick="setDefaultCookie('${cookie.filename}', '${platform}')" title="Set as default">⭐</button>
                ` : ''}
                <button class="btn btn-ghost btn-icon" onclick="renameCookie('${cookie.filename}', '${platform}')" title="Rename">✏️</button>
                <button class="btn btn-ghost btn-icon" onclick="deleteCookie('${cookie.filename}', '${platform}')" title="Delete">🗑️</button>
            </div>
        </div>
    `).join('');
}

async function uploadCookie(event, platform = 'instagram') {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('platform', platform);

    try {
        const response = await fetch('/api/cookies/upload', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`Uploaded ${data.filename} for ${platform}`, 'success');
            loadCookies(platform);
        } else {
            showToast(data.error || 'Failed to upload cookie', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }

    // Reset input
    event.target.value = '';
}

async function deleteCookie(filename, platform = 'instagram') {
    if (!confirm(`Delete cookie "${filename}"?`)) {
        return;
    }

    try {
        const response = await fetch(`/api/cookies/${encodeURIComponent(filename)}?platform=${platform}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showToast('Cookie deleted', 'success');
            loadCookies(platform);
        } else {
            showToast('Failed to delete cookie', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

async function renameCookie(filename, platform = 'instagram') {
    const newName = prompt('Enter new name:', filename.replace('.txt', ''));
    if (!newName || newName === filename.replace('.txt', '')) {
        return;
    }

    try {
        const response = await fetch(`/api/cookies/${encodeURIComponent(filename)}/rename?platform=${platform}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_name: newName })
        });

        const data = await response.json();

        if (response.ok) {
            showToast(`Renamed to ${data.filename}`, 'success');
            loadCookies(platform);
        } else {
            showToast(data.error || 'Failed to rename cookie', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

async function setDefaultCookie(filename, platform = 'instagram') {
    try {
        const response = await fetch('/api/cookies/default', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, platform })
        });

        if (response.ok) {
            showToast(`Set ${filename} as default for ${platform}`, 'success');
            loadCookies(platform);
        } else {
            showToast('Failed to set default cookie', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

// =============================================================================
// Instagram Following Import
// =============================================================================

let followingList = [];

// Initialize following import buttons - try multiple times to handle modal loading
function initFollowingImport() {
    const fetchBtn = document.getElementById('fetch-following-btn');
    const selectAllBtn = document.getElementById('select-all-btn');
    const deselectAllBtn = document.getElementById('deselect-all-btn');
    const importBtn = document.getElementById('import-selected-btn');

    console.log('[Following Import] Initializing buttons:', { fetchBtn, selectAllBtn, deselectAllBtn, importBtn });

    if (fetchBtn && !fetchBtn.hasAttribute('data-initialized')) {
        fetchBtn.setAttribute('data-initialized', 'true');
        fetchBtn.addEventListener('click', fetchFollowing);
        console.log('[Following Import] Fetch button initialized');
    }
    if (selectAllBtn && !selectAllBtn.hasAttribute('data-initialized')) {
        selectAllBtn.setAttribute('data-initialized', 'true');
        selectAllBtn.addEventListener('click', selectAllFollowing);
    }
    if (deselectAllBtn && !deselectAllBtn.hasAttribute('data-initialized')) {
        deselectAllBtn.setAttribute('data-initialized', 'true');
        deselectAllBtn.addEventListener('click', deselectAllFollowing);
    }
    if (importBtn && !importBtn.hasAttribute('data-initialized')) {
        importBtn.setAttribute('data-initialized', 'true');
        importBtn.addEventListener('click', importSelectedFollowing);
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', initFollowingImport);

// Also try when modal opens (use event delegation)
document.addEventListener('click', (e) => {
    if (e.target.id === 'fetch-following-btn' && !e.target.hasAttribute('data-initialized')) {
        initFollowingImport();
        fetchFollowing();
    }
});

async function fetchFollowing() {
    const statusEl = document.getElementById('following-status');
    const loadingEl = document.getElementById('following-loading');
    const listEl = document.getElementById('following-list');
    const fetchBtn = document.getElementById('fetch-following-btn');

    statusEl.classList.add('hidden');
    loadingEl.classList.remove('hidden');
    if (fetchBtn) fetchBtn.disabled = true;

    try {
        const response = await fetch('/api/instagram/following');
        const data = await response.json();

        loadingEl.classList.add('hidden');
        if (fetchBtn) fetchBtn.disabled = false;

        if (response.ok) {
            followingList = data.following || [];

            if (followingList.length === 0) {
                listEl.innerHTML = '<p class="text-muted">No following found. Make sure you have Instagram cookies set.</p>';
            } else {
                renderFollowingList();
            }

            statusEl.classList.remove('hidden');
            showToast(`Found ${followingList.length} accounts`, 'success');
        } else {
            showToast(data.error || 'Failed to fetch following', 'error');
        }
    } catch (error) {
        loadingEl.classList.add('hidden');
        if (fetchBtn) fetchBtn.disabled = false;
        showToast('Network error - check your Instagram cookies', 'error');
        console.error(error);
    }
}

function renderFollowingList() {
    const listEl = document.getElementById('following-list');

    // Generate a color based on username
    const getAvatarColor = (username) => {
        const colors = ['#e91e63', '#9c27b0', '#673ab7', '#3f51b5', '#2196f3', '#00bcd4', '#009688', '#4caf50', '#ff9800', '#ff5722'];
        let hash = 0;
        for (let i = 0; i < username.length; i++) {
            hash = username.charCodeAt(i) + ((hash << 5) - hash);
        }
        return colors[Math.abs(hash) % colors.length];
    };

    // Get proxied image URL or fallback placeholder
    const getAvatarHtml = (user) => {
        if (user.profile_pic) {
            const proxyUrl = `/api/proxy-image?url=${encodeURIComponent(user.profile_pic)}`;
            const fallbackStyle = `background: ${getAvatarColor(user.username)}`;
            return `<img src="${proxyUrl}" class="following-avatar" onerror="this.outerHTML='<div class=\\'following-avatar-placeholder\\' style=\\'${fallbackStyle}\\'>${user.username.charAt(0).toUpperCase()}</div>'">`;
        }
        return `<div class="following-avatar-placeholder" style="background: ${getAvatarColor(user.username)}">${user.username.charAt(0).toUpperCase()}</div>`;
    };

    listEl.innerHTML = followingList.map((user, index) => `
        <div class="following-item" data-index="${index}">
            <label class="following-checkbox">
                <input type="checkbox" id="follow-${index}" onchange="updateSelectedCount()">
                <div class="following-item-info">
                    ${getAvatarHtml(user)}
                    <div class="following-details">
                        <div class="following-username">@${user.username}</div>
                        ${user.full_name ? `<div class="following-name">${user.full_name}</div>` : ''}
                    </div>
                </div>
            </label>
        </div>
    `).join('');

    updateSelectedCount();
}

function selectAllFollowing() {
    document.querySelectorAll('#following-list input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
    });
    updateSelectedCount();
}

function deselectAllFollowing() {
    document.querySelectorAll('#following-list input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    updateSelectedCount();
}

function updateSelectedCount() {
    const checkboxes = document.querySelectorAll('#following-list input[type="checkbox"]:checked');
    const count = checkboxes.length;
    const countEl = document.getElementById('selected-count');
    const btn = document.getElementById('import-selected-btn');

    if (countEl) countEl.textContent = count;
    if (btn) btn.disabled = count === 0;
}

async function importSelectedFollowing() {
    const checkboxes = document.querySelectorAll('#following-list input[type="checkbox"]:checked');
    const selectedUsers = [];

    checkboxes.forEach(cb => {
        const index = parseInt(cb.id.replace('follow-', ''));
        if (followingList[index]) {
            selectedUsers.push(followingList[index].username);
        }
    });

    if (selectedUsers.length === 0) {
        showToast('No users selected', 'error');
        return;
    }

    showToast(`Importing ${selectedUsers.length} users...`, 'info');

    let successCount = 0;
    let failCount = 0;

    for (const username of selectedUsers) {
        try {
            const response = await fetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: username,
                    platform: 'instagram'
                })
            });

            if (response.ok) {
                successCount++;
            } else {
                const data = await response.json();
                if (data.error && data.error.includes('already exists')) {
                    // Skip existing users silently
                } else {
                    failCount++;
                }
            }
        } catch (error) {
            failCount++;
        }
    }

    if (successCount > 0) {
        showToast(`Imported ${successCount} users!`, 'success');
    }
    if (failCount > 0) {
        showToast(`${failCount} failed to import `, 'warning');
    }

    closeModal('import-following-modal');

    // Reload page to show new users
    setTimeout(() => location.reload(), 1000);
}

// =============================================================================
// Backup & Restore
// =============================================================================

function downloadBackup() {
    window.location.href = '/api/backup';
    showToast('Downloading backup...', 'info');
}

async function restoreBackup(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (!confirm('This will overwrite your current data. Continue?')) {
        event.target.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/restore', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Backup restored! Reloading...', 'success');
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast(data.error || 'Failed to restore backup', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }

    event.target.value = '';
}

async function factoryReset() {
    const confirmText = prompt('Type "RESET" to confirm factory reset:');
    if (confirmText !== 'RESET') {
        return;
    }

    const deleteFiles = confirm('Also delete all downloaded files?');

    try {
        const response = await fetch('/api/factory-reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ delete_files: deleteFiles })
        });

        if (response.ok) {
            showToast('Factory reset complete! Reloading...', 'success');
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast('Failed to perform factory reset', 'error');
        }
    } catch (error) {
        showToast('Network error', 'error');
    }
}

// =============================================================================
// Media Viewer
// =============================================================================

// =============================================================================
// Storage Stats
// =============================================================================

async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();

        // Update summary cards
        document.getElementById('stat-total-storage').textContent = data.total_storage_formatted;
        document.getElementById('stat-total-files').textContent = data.total_files.toLocaleString();
        document.getElementById('stat-total-users').textContent = data.total_users;

        // Render platform chart
        const chartContainer = document.getElementById('platform-chart');
        if (data.platforms.length === 0) {
            chartContainer.innerHTML = '<p class="text-muted">No data yet</p>';
        } else {
            const platformIcons = { instagram: '📸', tiktok: '🎵', coomer: '💖' };
            chartContainer.innerHTML = data.platforms.map(p => `
                <div class="platform-bar">
                    <div class="platform-bar-label">${platformIcons[p.name] || ''} ${p.name}</div>
                    <div class="platform-bar-track">
                        <div class="platform-bar-fill ${p.name}" style="width: ${p.percentage}%"></div>
                    </div>
                    <div class="platform-bar-size">${p.size_formatted}</div>
                </div>
            `).join('');
        }

        // Render user list
        const userList = document.getElementById('stats-user-list');
        if (data.users.length === 0) {
            userList.innerHTML = '<p class="text-muted">No tracked profiles yet</p>';
        } else {
            const platformIcons = { instagram: '📸', tiktok: '🎵', coomer: '💖' };
            userList.innerHTML = data.users.map(u => `
                <a href="/user/${u.platform}/${u.username}" class="stats-user-item">
                    <span class="stats-user-platform">${platformIcons[u.platform] || '📁'}</span>
                    <span class="stats-user-name">${u.display_name || u.username}</span>
                    <span class="stats-user-files">${u.files} files</span>
                    <span class="stats-user-size">${u.size_formatted}</span>
                </a>
            `).join('');
        }
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

function initLazyLoading() {
    const images = document.querySelectorAll('.media-item img[data-src]');

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    img.src = img.dataset.src;
                    img.removeAttribute('data-src');
                    img.parentElement.classList.remove('loading');
                    observer.unobserve(img);
                }
            });
        }, {
            rootMargin: '100px'
        });

        images.forEach(img => observer.observe(img));
    } else {
        // Fallback for older browsers
        images.forEach(img => {
            img.src = img.dataset.src;
            img.removeAttribute('data-src');
            img.parentElement.classList.remove('loading');
        });
    }
}

function openMediaViewer(src, type) {
    const modal = document.getElementById('media-viewer-modal');
    const container = modal?.querySelector('.media-viewer-content');

    if (!modal || !container) return;

    // Stop any currently playing video first
    const existingVideo = container.querySelector('video');
    if (existingVideo) {
        existingVideo.pause();
        existingVideo.src = '';
    }

    // Clear container before adding new content
    container.innerHTML = '';

    if (type === 'video') {
        container.innerHTML = `
            <video controls autoplay style="max-width: 100%; max-height: 80vh;">
                <source src="${src}" type="video/mp4">
            </video>
        `;
    } else {
        container.innerHTML = `
            <img src="${src}" style="max-width: 100%; max-height: 80vh; object-fit: contain;">
        `;
    }

    openModal('media-viewer-modal');
}

function closeMediaViewer() {
    const modal = document.getElementById('media-viewer-modal');
    const container = modal?.querySelector('.media-viewer-content');

    if (container) {
        // Stop video if playing
        const video = container.querySelector('video');
        if (video) {
            video.pause();
        }
        container.innerHTML = '';
    }

    closeModal('media-viewer-modal');
}

// Toggle like/favorite
async function toggleLike(filename) {
    try {
        const response = await fetch('/api/likes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        });

        const data = await response.json();

        if (response.ok) {
            const btn = document.querySelector(`[data - like - file="${filename}"]`);
            if (btn) {
                btn.classList.toggle('liked');
            }
            showToast(data.action === 'unliked' ? 'Removed from favorites' : 'Added to favorites', 'success');
        }
    } catch (error) {
        showToast('Failed to update favorite', 'error');
    }
}

// =============================================================================
// Tab Navigation
// =============================================================================

function switchTab(tabId) {
    // Update tab buttons
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabId);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab - ${tabId} `);
    });
}

// =============================================================================
// Utility Functions
// =============================================================================

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(timestamp) {
    return new Date(timestamp).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

function formatTimeAgo(dateString) {
    if (!dateString) return 'Never';

    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;

    return formatDate(date);
}

function initTooltips() {
    // Simple tooltip implementation
    document.querySelectorAll('[title]').forEach(el => {
        el.addEventListener('mouseenter', (e) => {
            const title = e.target.getAttribute('title');
            if (!title) return;

            e.target.setAttribute('data-title', title);
            e.target.removeAttribute('title');
        });

        el.addEventListener('mouseleave', (e) => {
            const title = e.target.getAttribute('data-title');
            if (title) {
                e.target.setAttribute('title', title);
                e.target.removeAttribute('data-title');
            }
        });
    });
}

// Debounce helper
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}
