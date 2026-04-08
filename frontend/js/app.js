/**
 * Prosciutto - Gmail API Web App
 * Main Application Logic
 */

let currentLabel = 'INBOX';
let currentAccountId = null; // null means unified
let accounts = [];
let nextPageToken = null;
let globalSettings = {};
let currentMessages = [];
let sortColumn = 'internalDate';
let sortOrder = 'desc';
let columnWidths = {
    sender: 200,
    subject: 0 // 0 means flex: 1
};

/**
 * Initialize the application
 */
async function init() {
    
    registerServiceWorker();
    document.documentElement.style.setProperty('--sender-width', `${columnWidths.sender}px`);
    const urlParams = new URLSearchParams(window.location.search);
    let messageId = urlParams.get('messageId');
    let accountId = urlParams.get('accountId');
    const compose = urlParams.get('compose');
    const settings = urlParams.get('settings');

    if (messageId === 'null') messageId = null;
    if (accountId === 'null') accountId = null;

    setupEventDelegation();
    setupKeyboardShortcuts();

    if (settings === 'true') {
        // Settings-only view mode
        const sidebar = document.getElementById('sidebar');
        const header = document.getElementById('header');
        const listContainer = document.getElementById('message-list-container');
        
        if (sidebar) sidebar.style.display = 'none';
        if (header) header.style.display = 'none';
        if (listContainer) listContainer.style.display = 'none';
        
        const modal = document.getElementById('settings-modal');
        if (modal) {
            modal.style.display = 'flex';
            modal.classList.add('standalone');
        }

        await loadSettings();
        await loadAccounts();
        renderAccountsInSettings();
        renderContactsAccounts();
        loadStats();
        return;
    }

    if (compose === 'true') {
        // Compose-only view mode
        const sidebar = document.getElementById('sidebar');
        const header = document.getElementById('header');
        const listContainer = document.getElementById('message-list-container');
        
        if (sidebar) sidebar.style.display = 'none';
        if (header) header.style.display = 'none';
        if (listContainer) listContainer.style.display = 'none';
        
        const panel = document.getElementById('message-detail-panel');
        if (panel) {
            panel.classList.add('open', 'single-view');
        }

        await loadSettings();
        await loadAccounts();
        renderNewComposerInPanel(accountId);
        return;
    }

    if (messageId && accountId) {
        // Single-message view mode
        const sidebar = document.getElementById('sidebar');
        const header = document.getElementById('header');
        const listContainer = document.getElementById('message-list-container');
        
        if (sidebar) sidebar.style.display = 'none';
        if (header) header.style.display = 'none';
        if (listContainer) listContainer.style.display = 'none';
        
        const panel = document.getElementById('message-detail-panel');
        if (panel) {
            panel.classList.add('open', 'single-view');
        }

        await loadSettings();
        await loadAccounts();
        window.showMessage(messageId, accountId, true);
        return;
    }

    await loadSettings();
    if (globalSettings.ALWAYS_COLLAPSE_SIDEBAR === 'true') {
        setSidebarCollapsed(true);
    }
    await loadAccounts();
    if (accounts.length === 0) {
        const firstRun = document.getElementById('first-run-prompt');
        if (firstRun) firstRun.style.display = 'flex';
    } else {
        loadMailbox('INBOX');
    }
    startNewMailPolling();
    
}


/**
 * Register Service Worker for Web Push
 */
async function registerServiceWorker() {
    if ('serviceWorker' in navigator && 'PushManager' in window) {
        try {
            const registration = await navigator.serviceWorker.register('/js/sw.js');
            
            navigator.serviceWorker.addEventListener('message', event => {
                if (event.data && event.data.type === 'NEW_MAIL') {
                    // Only refresh if we're in a relevant view
                    if (currentLabel === 'INBOX' || currentLabel === 'UNIFIED') {
                        refreshMailbox();
                    }
                }
            });
            
        } catch (err) {
            console.error('Service Worker registration failed:', err);
        }
    }
}

/**
 * Convert Base64 URL-safe to Uint8Array for VAPID key
 */
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

/**
 * Subscribe to Push Notifications
 */
async function subscribeToPushNotifications() {
    try {
        const registration = await navigator.serviceWorker.ready;
        const configRes = await fetch('/accounts/push-config');
        const config = await configRes.json();
        
        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(config.public_key)
        });

        const subObj = JSON.parse(JSON.stringify(subscription));
        await fetch('/accounts/subscribe-push', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                endpoint: subObj.endpoint,
                p256dh: subObj.keys.p256dh,
                auth: subObj.keys.auth
            })
        });
        
        
        return true;
    } catch (err) {
        console.error('Failed to subscribe to Web Push:', err);
        return false;
    }
}

/**
 * Unsubscribe from Push Notifications
 */
async function unsubscribeFromPushNotifications() {
    try {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();
        if (subscription) {
            const subObj = JSON.parse(JSON.stringify(subscription));
            await fetch('/accounts/unsubscribe-push', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    endpoint: subObj.endpoint,
                    p256dh: subObj.keys.p256dh,
                    auth: subObj.keys.auth
                })
            });
            await subscription.unsubscribe();
            
        }
        return true;
    } catch (err) {
        console.error('Failed to unsubscribe from Web Push:', err);
        return false;
    }
}

/**
 * Poll for new mail every minute and show notifications
 */
async function startNewMailPolling() {
    // Only poll in the main window
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('settings') === 'true' || urlParams.get('compose') === 'true' || urlParams.get('messageId')) {
        return;
    }

    const POLLING_INTERVAL = 60000; // 1 minute

    setInterval(async () => {
        try {
            const res = await fetch('/accounts/check-new-messages');
            if (res.ok) {
                const newMessages = await res.json();
                if (newMessages && newMessages.length > 0) {
                    newMessages.forEach(msg => {
                        const title = `New Mail: ${msg.subject || '(No Subject)'}`;
                        const options = {
                            body: `From: ${msg.from}\nAccount: ${msg.account_email}`,
                            icon: '/favicon.ico'
                        };
                        
                        if (Notification.permission === "granted") {
                            const n = new Notification(title, options);
                            n.onclick = () => {
                                window.focus();
                                window.showMessage(msg.id, msg.account_id); // Assuming we have account_id, need to add it to backend response
                            };
                        }
                    });
                    
                    // Also refresh the current mailbox if we're in INBOX or UNIFIED
                    if (currentLabel === 'INBOX' || currentLabel === 'UNIFIED') {
                        refreshMailbox();
                    }
                }
            }
        } catch (err) {
            console.error("Error polling for new mail:", err);
        }
    }, POLLING_INTERVAL);
}

/**
 * Setup event delegation for the message list to avoid multiple listeners
 */
function setupEventDelegation() {
    
    const list = document.getElementById('message-list');
    if (!list) return;

    list.addEventListener('click', (e) => {
        const row = e.target.closest('.message-item');
        if (!row) return;

        // Handle checkbox clicks
        if (e.target.classList.contains('message-checkbox')) {
            e.stopPropagation();
            updateSelectionCount();
            return;
        }

        // Check if a hover button was clicked
        const actionBtn = e.target.closest('.hover-action-btn');
        if (actionBtn) {
            e.stopPropagation();
            const msgId = row.dataset.id;
            const accId = row.dataset.accid;
            const isUnread = row.querySelector('.unread-dot').classList.contains('invisible') === false;

            if (actionBtn.classList.contains('toggle-read')) {
                toggleReadStatus(msgId, accId, isUnread);
            } else if (actionBtn.classList.contains('archive')) {
                archiveMessage(msgId, accId);
            } else if (actionBtn.classList.contains('delete')) {
                trashMessage(msgId, accId);
            }
            return;
        }

        // Default: show message
        const msgId = row.dataset.id;
        const accId = row.dataset.accid;
        
        if (globalSettings.ALWAYS_OPEN_IN_SIDE_PANEL !== 'false') {
            window.showMessage(msgId, accId);
        } else {
            // Just highlight the row if side panel is disabled
            document.querySelectorAll('.message-item').forEach(item => item.classList.remove('selected'));
            row.classList.add('selected');
        }
    });

    list.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        e.preventDefault();
        const row = e.target.closest('.message-item');
        if (row) {
            const msgId = row.dataset.id;
            const accId = row.dataset.accid;
            if (globalSettings.ALWAYS_OPEN_IN_SIDE_PANEL !== 'false') {
                window.showMessage(msgId, accId);
            } else {
                openMessageInNewWindow(msgId, accId);
            }
        }
    });
}

/**
 * Load application settings from the backend
 */
async function loadSettings() {
    const res = await fetch('/settings');
    globalSettings = await res.json();

    // Populate General Settings
    const clientIdEl = document.getElementById('client-id');
    if (clientIdEl) clientIdEl.value = globalSettings.GOOGLE_CLIENT_ID || '';
    
    const clientSecretEl = document.getElementById('client-secret');
    if (clientSecretEl) clientSecretEl.value = globalSettings.GOOGLE_CLIENT_SECRET || '';
    
    const composeWindowEl = document.getElementById('setting-compose-window');
    if (composeWindowEl) composeWindowEl.checked = globalSettings.COMPOSE_NEW_WINDOW !== 'false';
    
    const composeHtmlEl = document.getElementById('setting-compose-html');
    if (composeHtmlEl) composeHtmlEl.checked = globalSettings.COMPOSE_AS_HTML !== 'false';
    
    const markReadAutoEl = document.getElementById('setting-mark-read');
    if (markReadAutoEl) markReadAutoEl.checked = globalSettings.MARK_READ_AUTOMATICALLY !== 'false';

    const warnDeleteCheckbox = document.getElementById('setting-warn-delete');
    if (warnDeleteCheckbox) {
        warnDeleteCheckbox.checked = globalSettings.WARN_BEFORE_DELETE !== 'false';
        if (!globalSettings.CAN_PERMANENTLY_DELETE) {
            warnDeleteCheckbox.disabled = true;
            if (warnDeleteCheckbox.parentElement) {
                warnDeleteCheckbox.parentElement.title = "Deletion scope not enabled in .env";
                warnDeleteCheckbox.parentElement.style.opacity = "0.6";
            }
        } else {
            warnDeleteCheckbox.disabled = false;
            if (warnDeleteCheckbox.parentElement) {
                warnDeleteCheckbox.parentElement.title = "";
                warnDeleteCheckbox.parentElement.style.opacity = "1";
            }
        }
    }

    const clientIdEnvBadge = document.getElementById('client-id-env-badge');
    if (globalSettings.is_client_id_env) {
        if (clientIdEl) clientIdEl.disabled = true;
        if (clientIdEnvBadge) clientIdEnvBadge.style.display = 'inline-block';
    }
    
    const clientSecretEnvBadge = document.getElementById('client-secret-env-badge');
    if (globalSettings.is_client_secret_env) {
        if (clientSecretEl) clientSecretEl.disabled = true;
        if (clientSecretEnvBadge) clientSecretEnvBadge.style.display = 'inline-block';
    }

    toggleClientSecretVisibility();

    const feedback = document.getElementById('credentials-feedback');
    if (feedback) {
        if (globalSettings.is_client_id_env || globalSettings.is_client_secret_env) {
            feedback.innerText = "Some settings are locked because they are provided via environment variables.";
            feedback.style.color = "var(--text-gray)";
        } else if (globalSettings.GOOGLE_CLIENT_ID && (globalSettings.OAUTH_APP_TYPE === 'desktop' || globalSettings.GOOGLE_CLIENT_SECRET)) {
            feedback.innerText = "Credentials are configured and stored in the database.";
            feedback.style.color = "green";
        } else {
            feedback.innerText = "Credentials are not yet configured.";
            feedback.style.color = "var(--accent-red)";
        }
    }

    // Populate Appearance Settings
    const themeEl = document.getElementById('setting-theme');
    if (themeEl) themeEl.value = globalSettings.THEME || 'automatic';
    
    const disclosureEl = document.getElementById('setting-disclosure');
    if (disclosureEl) disclosureEl.checked = globalSettings.SHOW_DISCLOSURE_IF_SINGLE === 'true';
    
    const starredEl = document.getElementById('setting-starred');
    if (starredEl) starredEl.checked = globalSettings.SHOW_STARRED === 'true';
    
    const collapseSidebarEl = document.getElementById('setting-collapse-sidebar');
    if (collapseSidebarEl) collapseSidebarEl.checked = globalSettings.ALWAYS_COLLAPSE_SIDEBAR === 'true';

    const sidePanelEl = document.getElementById('setting-side-panel');
    if (sidePanelEl) sidePanelEl.checked = globalSettings.ALWAYS_OPEN_IN_SIDE_PANEL !== 'false';

    // Populate Contacts Settings
    const autocompleteRecentsEl = document.getElementById('setting-autocomplete-recents');
    if (autocompleteRecentsEl) autocompleteRecentsEl.checked = globalSettings.AUTOCOMPLETE_RECENTS === 'true';

    // Populate Privacy Settings
    const remoteImagesEl = document.getElementById('setting-remote-images');
    if (remoteImagesEl) remoteImagesEl.checked = globalSettings.LOAD_REMOTE_IMAGES === 'true';

    const keyboardShortcutsEl = document.getElementById('setting-keyboard-shortcuts');
    if (keyboardShortcutsEl) keyboardShortcutsEl.checked = globalSettings.KEYBOARD_SHORTCUTS_ENABLED === 'true';

    // Populate Account Connectivity (Advanced)
    const enableDeletionEl = document.getElementById('setting-enable-deletion');
    if (enableDeletionEl) {
        enableDeletionEl.checked = globalSettings.ENABLE_DELETION_SCOPE === 'true';
        if (globalSettings.is_enable_deletion_env) {
            enableDeletionEl.disabled = true;
            enableDeletionEl.title = "Overridden by environment variable ENABLE_DELETION_SCOPE";
        }
    }

    // Refresh the accounts-based UI elements (like the default account dropdown)
    window.renderAccountsInSettings();

    applyTheme(globalSettings.THEME);
    updateStarredMailboxVisibility();
}

function toggleClientSecretVisibility() {
    const type = globalSettings.OAUTH_APP_TYPE || 'web';
    const secretGroup = document.getElementById('client-secret-group');
    if (type === 'desktop') {
        secretGroup.style.display = 'none';
    } else {
        secretGroup.style.display = 'block';
    }
}

/**
 * Save all settings to the backend
 */
async function saveAllSettings() {
    const theme = document.getElementById('setting-theme').value;
    const disclosure = document.getElementById('setting-disclosure').checked;
    const starred = document.getElementById('setting-starred').checked;
    const collapseSidebar = document.getElementById('setting-collapse-sidebar').checked;
    const sidePanel = document.getElementById('setting-side-panel').checked;
    const remoteImages = document.getElementById('setting-remote-images').checked;
    const composeWindow = document.getElementById('setting-compose-window').checked;
    const composeHtml = document.getElementById('setting-compose-html').checked;
    const markReadAuto = document.getElementById('setting-mark-read').checked;
    const warnDelete = document.getElementById('setting-warn-delete').checked;
    const keyboardShortcuts = document.getElementById('setting-keyboard-shortcuts').checked;
    const defaultAccount = document.getElementById('setting-default-account').value;
    
    let enableDeletion = false;
    const enableDeletionEl = document.getElementById('setting-enable-deletion');
    if (enableDeletionEl) {
        enableDeletion = enableDeletionEl.checked;
        // Special handling for deletion scope enabling
        if (enableDeletion && globalSettings.ENABLE_DELETION_SCOPE !== 'true') {
            const confirmed = confirm("Enabling permanent deletion requires higher permissions from Google. \n\nIMPORTANT: For this to work, you MUST remove and re-add all accounts after saving. \n\nDo you want to proceed?");
            if (!confirmed) {
                enableDeletionEl.checked = false;
                enableDeletion = false;
            }
        }
    }

    const autocompleteRecents = document.getElementById('setting-autocomplete-recents').checked;
    const autocompleteEnabledAccounts = Array.from(document.querySelectorAll('.contact-account-toggle'))
        .filter(cb => cb.checked)
        .map(cb => cb.dataset.accid)
        .join(',');

    const clientId = document.getElementById('client-id').value;
    const clientSecret = document.getElementById('client-secret').value;

    const data = {
        THEME: theme,
        SHOW_DISCLOSURE_IF_SINGLE: disclosure ? 'true' : 'false',
        SHOW_STARRED: starred ? 'true' : 'false',
        ALWAYS_COLLAPSE_SIDEBAR: collapseSidebar ? 'true' : 'false',
        ALWAYS_OPEN_IN_SIDE_PANEL: sidePanel ? 'true' : 'false',
        DEFAULT_COMPOSE_ACCOUNT: defaultAccount,
        ENABLE_DELETION_SCOPE: enableDeletion ? 'true' : 'false',
        LOAD_REMOTE_IMAGES: remoteImages ? 'true' : 'false',
        COMPOSE_NEW_WINDOW: composeWindow ? 'true' : 'false',
        COMPOSE_AS_HTML: composeHtml ? 'true' : 'false',
        MARK_READ_AUTOMATICALLY: markReadAuto ? 'true' : 'false',
        WARN_BEFORE_DELETE: warnDelete ? 'true' : 'false',
        AUTOCOMPLETE_RECENTS: autocompleteRecents ? 'true' : 'false',
        KEYBOARD_SHORTCUTS_ENABLED: keyboardShortcuts ? 'true' : 'false',
        AUTOCOMPLETE_ENABLED_ACCOUNTS: autocompleteEnabledAccounts
    };
    // Only include credentials if they are not disabled (not from env)
    if (!document.getElementById('client-id').disabled) {
        data.GOOGLE_CLIENT_ID = clientId;
    }
    if (!document.getElementById('client-secret').disabled) {
        data.GOOGLE_CLIENT_SECRET = clientSecret;
    }
    await fetch('/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });

    notifyOpener(data);

    await loadSettings();
    
    // Explicitly apply some settings that might not be fully handled by loadSettings() UI population
    setSidebarCollapsed(globalSettings.ALWAYS_COLLAPSE_SIDEBAR === 'true');

    updateDisclosureTriangles();
    updateStarredMailboxVisibility();
}

function applyTheme(theme) {
    if (theme === 'automatic') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
    } else {
        document.documentElement.setAttribute('data-theme', theme);
    }
}

/**
 * Load all connected accounts
 */
async function loadAccounts() {
    const res = await fetch('/accounts');
    accounts = await res.json();
    
    const activeAccounts = accounts.filter(a => a.is_active);
    
    // Populate account subsets in sidebar
    ['INBOX', 'SENT', 'STARRED', 'DRAFT', 'TRASH', 'SPAM', 'ALL'].forEach(label => {
        const subset = document.getElementById(`accounts-${label}`);
        if (!subset) {
            return;
        }
        subset.innerHTML = '';
        activeAccounts.forEach(acc => {
            const item = document.createElement('div');
            item.className = 'account-subset-item';
            item.tabIndex = 0;
            item.innerText = acc.email;
            item.onclick = (e) => {
                e.stopPropagation();
                loadAccountMailbox(acc.id, acc.email, label === 'ALL' ? '' : label);
            };
            subset.appendChild(item);
        });
    });

    updateDisclosureTriangles(activeAccounts);
    renderAccountsInSettings();
    await loadLabels(activeAccounts);
    
    // Trigger contact sync for each account
    activeAccounts.forEach(acc => {
        fetch(`/accounts/${acc.id}/sync-contacts`);
    });

    const mailboxList = document.getElementById('mailbox-list');
    const composeArea = document.getElementById('compose-area');
    if (activeAccounts.length === 0) {
        mailboxList.style.display = 'none';
        composeArea.style.display = 'none';
    } else {
        mailboxList.style.display = 'block';
        composeArea.style.display = 'block';
    }
}

/**
 * Load custom labels for each account
 */
async function loadLabels(activeAccounts) {
    const container = document.getElementById('labels-container');
    container.innerHTML = '';
    
    for (const acc of activeAccounts) {
        const accItem = document.createElement('div');
        accItem.className = 'mailbox-item';
        accItem.tabIndex = 0;
        accItem.style.display = 'flex';
        accItem.style.alignItems = 'center';
        accItem.innerHTML = `
            <span class="disclosure-triangle visible" onclick="toggleDisclosure(event, 'labels-${acc.id}')">▶</span>
            <div class="label-icon-wrapper" onclick="expandSidebarIfCollapsed(event)">
                <i class="fa-solid fa-folder"></i>
                <i class="fa-solid fa-arrow-right label-expand-icon"></i>
            </div>
            <span class="flex-1 text-ellipsis">${acc.email}</span>
            <i class="fa-solid fa-plus ml-auto cursor-pointer opacity-06 p-2-5" onclick="createNewLabel(event, ${acc.id}, '${acc.email}')" title="New Label"></i>
        `;
        container.appendChild(accItem);

        const subset = document.createElement('div');
        subset.id = `accounts-labels-${acc.id}`;
        subset.className = 'account-subset';
        container.appendChild(subset);

        try {
            const res = await fetch(`/accounts/${acc.id}/labels`);
            const labels = await res.json();
            
            if (labels.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'account-subset-item';
                empty.innerText = 'No custom labels';
                empty.style.fontStyle = 'italic';
                subset.appendChild(empty);
            } else {
                labels.forEach(l => {
                    const item = document.createElement('div');
                    item.className = 'account-subset-item';
                    item.tabIndex = 0;
                    item.innerText = l.name;
                    item.onclick = (e) => {
                        e.stopPropagation();
                        loadAccountMailbox(acc.id, acc.email, l.id);
                    };
                    subset.appendChild(item);
                });
            }
        } catch (e) {
            console.error(`Failed to load labels for ${acc.email}`, e);
        }
    }
}

async function createNewLabel(event, accId, email) {
    event.stopPropagation();
    const name = prompt(`Enter name for new label in ${email}:`);
    if (!name) return;
    
    try {
        const res = await fetch(`/accounts/${accId}/labels`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        
        if (res.ok) {
            await loadLabels();
        } else {
            const error = await res.json();
            alert("Error creating label: " + (error.detail || "Unknown error"));
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while creating the label");
    }
}

function updateDisclosureTriangles(activeAccounts) {
    const accs = activeAccounts || accounts.filter(a => a.is_active);
    const showAlways = document.getElementById('setting-disclosure').checked;
    const shouldShow = showAlways || accs.length > 1;
    
    document.querySelectorAll('.disclosure-triangle').forEach(t => {
        if (shouldShow) {
            t.classList.add('visible');
        } else {
            t.classList.remove('visible');
        }
    });
}

function updateStarredMailboxVisibility() {
    const starredItem = document.getElementById('mailbox-item-STARRED');
    const showStarred = globalSettings.SHOW_STARRED === 'true';
    
    if (starredItem) {
        starredItem.style.display = showStarred ? 'flex' : 'none';
    }
}

window.renderAccountsInSettings = function() {
    const list = document.getElementById('accounts-list-settings');
    if (list) {
        list.innerHTML = '';
        accounts.forEach(acc => {
            const row = document.createElement('div');
            row.className = 'account-row display-flex align-center flex-wrap';
            const displayName = acc.name ? `${acc.name} (${acc.email})` : acc.email;
            row.innerHTML = `
                <div class="display-flex align-center flex-1">
                    <input type="checkbox" class="checkbox-inline" ${acc.is_active ? 'checked' : ''} onchange="toggleAccountActive(${acc.id}, this.checked)" title="Enable Account">
                    <span class="font-14 ${!acc.is_active ? 'text-strike text-gray' : ''}">${displayName}</span>
                </div>
                <div class="display-flex align-center">
                    <label class="font-12 text-gray mr-10 display-flex align-center">
                        <input type="checkbox" class="checkbox-inline mr-5" ${acc.notifications_enabled ? 'checked' : ''} onchange="toggleAccountNotifications(${acc.id}, this.checked)">
                        Notifications
                    </label>
                    <button class="compose-btn btn-inline bg-light-gray" onclick="removeAccount(${acc.id}, '${acc.email}')">Remove</button>
                </div>
            `;
            list.appendChild(row);
        });
    }

    // Populate the default account dropdown in General settings
    const defaultAccSelect = document.getElementById('setting-default-account');
    if (defaultAccSelect) {
        // Keep the "First added account" option
        defaultAccSelect.innerHTML = '<option value="">First added account</option>';
        accounts.forEach(acc => {
            const opt = document.createElement('option');
            opt.value = acc.id;
            opt.innerText = acc.email;
            if (globalSettings.DEFAULT_COMPOSE_ACCOUNT == acc.id) {
                opt.selected = true;
            }
            defaultAccSelect.appendChild(opt);
        });
    }
}

async function toggleAccountActive(id, isActive) {
    try {
        const res = await fetch(`/accounts/${id}/toggle-active`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive })
        });
        
        if (res.ok) {
            await loadAccounts();
            renderAccountsInSettings();
            notifyOpener();
            
            if (!isActive && currentAccountId === id) {
                loadMailbox('INBOX');
            } else if (currentAccountId === null) {
                if (currentLabel === 'SEARCH') {
                    performSearch(false, false);
                } else {
                    loadMailbox(currentLabel, false, false);
                }
            }
        } else {
            alert("Failed to update account status");
            renderAccountsInSettings();
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while updating account status");
        renderAccountsInSettings();
    }
}

async function toggleAccountNotifications(id, isEnabled) {
    try {
        const res = await fetch(`/accounts/${id}/toggle-notifications`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isEnabled })
        });
        if (res.ok) {
            await loadAccounts();
            renderAccountsInSettings();
            
            if (isEnabled) {
                if (Notification.permission === "default") {
                    const permission = await Notification.requestPermission();
                    if (permission === "granted") {
                        await subscribeToPushNotifications();
                    }
                } else if (Notification.permission === "granted") {
                    await subscribeToPushNotifications();
                } else {
                    alert("Please enable notifications in your browser settings to receive new mail alerts.");
                }
            } else {
                // If NO other account has notifications enabled, we could unsubscribe.
                // But keeping the subscription is fine, the server will only send if an account is enabled.
                const anyEnabled = accounts.some(a => a.id !== id && a.notifications_enabled);
                if (!anyEnabled) {
                    await unsubscribeFromPushNotifications();
                }
            }
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while updating notification settings");
        renderAccountsInSettings();
    }
}

function renderContactsAccounts() {
    const list = document.getElementById('contacts-accounts-list');
    if (!list) return;
    list.innerHTML = '';
    
    const enabledIds = (globalSettings.AUTOCOMPLETE_ENABLED_ACCOUNTS || "").split(",").filter(id => id.trim());
    
    accounts.forEach(acc => {
        const isChecked = enabledIds.includes(acc.id.toString());
        const row = document.createElement('div');
        row.className = 'display-flex justify-between align-center p-8-0 border-bottom-gray';
        row.innerHTML = `
            <span class="font-14">${acc.email}</span>
            <input type="checkbox" class="contact-account-toggle w-auto" data-accid="${acc.id}" ${isChecked ? 'checked' : ''} onchange="saveAllSettings()">
        `;
        list.appendChild(row);
    });
    if (accounts.length === 0) {
        list.innerHTML = '<p class="text-gray font-13">No accounts connected.</p>';
    }
}

async function removeAccount(id, email) {
    if (!confirm(`Are you sure you want to remove ${email}?`)) return;

    const res = await fetch(`/accounts/${id}`, { method: 'DELETE' });
    if (res.ok) {
        await loadAccounts();
        renderAccountsInSettings();
        renderContactsAccounts();
        notifyOpener();
        if (currentAccountId === id) {
            loadMailbox('INBOX');
        }
    } else {
        alert("Failed to remove account");
    }
}

window.openSettings = function() {
    const url = '?settings=true';
    window.open(url, '_blank', 'width=800,height=680');
}
window.closeSettings = function() {
    saveAllSettings();
    const modal = document.getElementById('settings-modal');
    if (modal.classList.contains('standalone')) {
        window.close();
    } else {
        modal.style.display = 'none';
    }
}

window.clearLocalContacts = async function() {
    if (!confirm("Are you sure you want to clear all locally cached contacts and recents? This will force a full re-sync from Google.")) return;
    
    try {
        const res = await fetch('/contacts/clear', { method: 'POST' });
        if (res.ok) {
            alert("Local contact cache cleared successfully.");
            window.loadStats();
        } else {
            alert("Failed to clear contact cache.");
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while clearing the cache.");
    }
}

window.loadStats = async function() {
    try {
        const res = await fetch('/stats');
        const data = await res.json();
        
        const statusEl = document.getElementById('stats-status');
        if (data.error) {
            if (statusEl) {
                statusEl.innerText = 'Error';
                statusEl.style.color = 'red';
            }
            return;
        }

        if (statusEl) {
            statusEl.innerText = 'Connected';
            statusEl.style.color = 'green';
        }
        
        const statAccounts = document.getElementById('stat-accounts');
        if (statAccounts) statAccounts.innerText = data.accounts;
        
        const statRecents = document.getElementById('stat-recents');
        if (statRecents) statRecents.innerText = data.recent_contacts;
        
        const statContacts = document.getElementById('stat-contacts');
        if (statContacts) statContacts.innerText = data.google_contacts;
        
        const statDbSize = document.getElementById('stat-db-size');
        if (statDbSize) statDbSize.innerText = formatBytes(data.db_size_bytes);
        
        const statCacheSize = document.getElementById('stat-cache-size');
        if (statCacheSize) statCacheSize.innerText = formatBytes(data.cache_size_bytes);
        
        const statDeletion = document.getElementById('stat-deletion');
        if (statDeletion) statDeletion.innerText = data.deletion_scope_enabled ? 'Yes' : 'No (Modify only)';
    } catch (err) {
        console.error("Failed to load stats", err);
    }
}

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

window.switchTab = function(tab) {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    
    const tabEl = document.querySelector(`.modal-tab[data-tab="${tab}"]`);
    if (tabEl) tabEl.classList.add('active');
    
    const panelEl = document.getElementById(`tab-${tab}`);
    if (panelEl) panelEl.classList.add('active');
}

window.toggleDisclosure = function(event, label) {
    event.stopPropagation();
    const triangle = event.target;
    const subset = document.getElementById(`accounts-${label}`);
    if (subset) {
        triangle.classList.toggle('expanded');
        subset.classList.toggle('visible');
    }
}

/**
 * Load unified mailbox messages
 */
window.loadMailbox = async function(label, append = false, refresh = false) {
    if (!append) {
        currentLabel = label;
        currentAccountId = null;
        nextPageToken = null;
        renderSkeletons();
        const viewNameEl = document.getElementById('view-name');
        if (viewNameEl) {
            let displayName = label === '' ? 'All Mail' : label.charAt(0) + label.slice(1).toLowerCase();
            if (label === 'DRAFT') displayName = 'Drafts';
            viewNameEl.innerText = displayName;
        }
        document.getElementById('search-input').value = '';
        hideMessageDetail();
        updateBanner(label);

        // Update active state in sidebar
        document.querySelectorAll('.mailbox-item').forEach(item => {
            item.classList.remove('active');
            const targetLabel = label === '' ? 'All Mail' : label;
            if (item.innerText.toUpperCase().includes(targetLabel.toUpperCase())) {
                item.classList.add('active');
            }
        });
    }

    let url = `/unified/messages?`;
    if (label) url += `label=${label}&`;
    if (append && nextPageToken) url += `page_token=${nextPageToken}&`;
    if (refresh) url += `refresh=true&`;
    
    const res = await fetch(url);
    const data = await res.json();
    nextPageToken = data.nextPageToken || null;
    
    const loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.style.display = nextPageToken ? 'inline-block' : 'none';
    }
    renderMessages(data.messages, append);
}

/**
 * Load mailbox messages for a specific account
 */
window.loadAccountMailbox = async function(accId, email, label, append = false, refresh = false) {
    if (!append) {
        currentLabel = label;
        currentAccountId = accId;
        nextPageToken = null;
        renderSkeletons();
        const viewNameEl = document.getElementById('view-name');
        if (viewNameEl) {
            viewNameEl.innerText = `${email} - ${label === '' ? 'All Mail' : label}`;
        }
        const searchInput = document.getElementById('search-input');
        if (searchInput) searchInput.value = '';
        hideMessageDetail();
        updateBanner(label);
    }
    
    let url = `/accounts/${accId}/messages?`;
    if (label) url += `label=${label}&`;
    if (append && nextPageToken) url += `page_token=${nextPageToken}&`;
    if (refresh) url += `refresh=true&`;
    
    const res = await fetch(url);
    const data = await res.json();
    nextPageToken = data.nextPageToken || null;

    const loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.style.display = nextPageToken ? 'inline-block' : 'none';
    }
    renderMessages(data.messages, append);
}

function updateBanner(label) {
    const banner = document.getElementById('mailbox-banner');
    const bannerText = document.getElementById('banner-text');
    const emptyBtn = document.getElementById('empty-mailbox-btn');

    if (banner && bannerText && emptyBtn) {
        if (label === 'TRASH') {
            banner.style.display = 'flex';
            bannerText.innerText = 'Messages in Trash will be automatically deleted after 30 days.';
            emptyBtn.innerText = 'Empty Trash Now';
            emptyBtn.style.display = 'inline-block';
        } else if (label === 'SPAM') {
            banner.style.display = 'flex';
            bannerText.innerText = 'Messages in Spam will be automatically deleted after 30 days.';
            emptyBtn.innerText = 'Empty Spam Now';
            emptyBtn.style.display = 'inline-block';
        } else {
            banner.style.display = 'none';
        }
        
        if (label === 'TRASH' || label === 'SPAM') {
            if (!globalSettings.CAN_PERMANENTLY_DELETE) {
                emptyBtn.disabled = true;
                emptyBtn.style.opacity = '0.5';
                emptyBtn.title = 'Deletion scope not enabled in .env';
            } else {
                emptyBtn.disabled = false;
                emptyBtn.style.opacity = '1';
                emptyBtn.title = '';
            }
        }
    }
    
    updateBulkActions(label);
}

function updateBulkActions(label) {
    const bulkAction = document.getElementById('action-dropdown');
    if (!bulkAction) return;
    const trashOption = Array.from(bulkAction.options).find(o => o.value === 'trash' || o.value === 'delete');
    if (!trashOption) return;
    if (label === 'TRASH' || label === 'SPAM') {
        trashOption.value = 'delete';
        if (!globalSettings.CAN_PERMANENTLY_DELETE) {
            trashOption.disabled = true;
            trashOption.innerText = 'Permanently Delete (Disabled)';
        } else {
            trashOption.disabled = false;
            trashOption.innerText = 'Permanently Delete';
        }
    } else {
        trashOption.value = 'trash';
        trashOption.disabled = false;
        trashOption.innerText = 'Move to Trash';
    }
}

async function emptyCurrentMailbox() {
    const label = currentLabel;
    if (label !== 'TRASH' && label !== 'SPAM') return;

    if (!confirm(`Are you sure you want to permanently delete all messages in ${label.toLowerCase()}?`)) return;

    let url = currentAccountId 
        ? `/accounts/${currentAccountId}/labels/${label}/empty`
        : `/unified/labels/${label}/empty`;

    try {
        const res = await fetch(url, { method: 'DELETE' });
        const data = await res.json();
        
        if (currentAccountId) {
            loadAccountMailbox(currentAccountId, accounts.find(a => a.id === currentAccountId).email, label);
        } else {
            loadMailbox(label);
        }
    } catch (e) {
        console.error("Failed to empty mailbox", e);
        alert("Failed to empty mailbox");
    }
}

function loadMore() {
    if (currentLabel === 'SEARCH') {
        performSearch(true);
    } else if (currentAccountId === null) {
        loadMailbox(currentLabel, true);
    } else {
        const email = accounts.find(a => a.id === currentAccountId)?.email || '';
        loadAccountMailbox(currentAccountId, email, currentLabel, true);
    }
}

window.handleSearch = function(event) {
    if (event.key === 'Enter') {
        performSearch();
    }
}

/**
 * Perform message search
 */
window.performSearch = async function(append = false, refresh = false) {
    const searchInput = document.getElementById('search-input');
    const query = searchInput ? searchInput.value.trim() : '';
    
    if (!query && !append) {
        // Clear search results and return to Inbox (or last active label)
        window.loadMailbox('INBOX');
        return;
    }

    if (!append) {
        currentLabel = 'SEARCH';
        nextPageToken = null;
        renderSkeletons();
        const viewNameEl = document.getElementById('view-name');
        if (viewNameEl) {
            viewNameEl.innerText = `Search: ${query}`;
        }
        hideMessageDetail();

        // Remove active state from sidebar
        document.querySelectorAll('.mailbox-item').forEach(item => item.classList.remove('active'));
    }

    let url = "";
    if (currentAccountId === null) {
        url = `/unified/search?q=${encodeURIComponent(query)}&max_results=20`;
    } else {
        url = `/accounts/${currentAccountId}/search?q=${encodeURIComponent(query)}&max_results=20`;
    }

    try {
        if (append && nextPageToken) url += `&page_token=${nextPageToken}`;
        if (refresh) url += `&refresh=true`;

        const res = await fetch(url);
        if (!res.ok) {
            const error = await res.json();
            alert("Search error: " + (error.detail || res.statusText));
            document.getElementById('message-list').innerHTML = '';
            return;
        }
        const data = await res.json();
        nextPageToken = data.nextPageToken || null;

        document.getElementById('load-more-btn').style.display = nextPageToken ? 'inline-block' : 'none';
        renderMessages(data.messages, append);
    } catch (err) {
        console.error(err);
        alert("An error occurred during search");
    }
}

window.toggleAdvancedSearch = function(event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById('advanced-search-dropdown');
    if (dropdown) dropdown.classList.toggle('open');
}

window.performAdvancedSearch = function() {
    const from = document.getElementById('adv-from').value;
    const subject = document.getElementById('adv-subject').value;
    const body = document.getElementById('adv-body').value;
    const after = document.getElementById('adv-date-after').value;
    const before = document.getElementById('adv-date-before').value;
    const hasAttachment = document.getElementById('adv-has-attachment').checked;

    let queryParts = [];
    if (from) queryParts.push(`from:${from}`);
    if (subject) queryParts.push(`subject:${subject}`);
    if (body) queryParts.push(body);
    if (after) queryParts.push(`after:${after.replace(/-/g, '/')}`);
    if (before) queryParts.push(`before:${before.replace(/-/g, '/')}`);
    if (hasAttachment) queryParts.push(`has:attachment`);

    const query = queryParts.join(' ');
    if (query) {
        document.getElementById('search-input').value = query;
        performSearch();
    }
    window.toggleAdvancedSearch();
}

window.clearAdvancedSearch = function() {
    document.getElementById('adv-from').value = '';
    document.getElementById('adv-subject').value = '';
    document.getElementById('adv-body').value = '';
    document.getElementById('adv-date-after').value = '';
    document.getElementById('adv-date-before').value = '';
    document.getElementById('adv-has-attachment').checked = false;
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('advanced-search-dropdown');
    const toggle = document.getElementById('advanced-search-toggle');
    if (dropdown && dropdown.classList.contains('open') && !dropdown.contains(e.target) && !toggle.contains(e.target)) {
        dropdown.classList.remove('open');
    }
});

/**
 * Render skeleton loaders for message list
 */
function renderSkeletons() {
    const list = document.getElementById('message-list');
    if (!list) return;
    
    list.innerHTML = '';
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < 10; i++) {
        const row = document.createElement('div');
        row.className = 'skeleton-row';
        row.innerHTML = `
            <div class="skeleton-dot"></div>
            <div class="skeleton-check"></div>
            <div class="skeleton-sender"></div>
            <div class="skeleton-snippet"></div>
            <div class="skeleton-date"></div>
            <div class="shimmer"></div>
        `;
        fragment.appendChild(row);
    }
    list.appendChild(fragment);
}

/**
 * Create a single message row element
 */
function createMessageRow(msg) {
    const item = document.createElement('div');
    const isUnread = msg.labelIds && msg.labelIds.includes('UNREAD');
    item.className = `message-item ${isUnread ? 'unread' : ''}`;
    item.id = `msg-${msg.id}`;
    item.tabIndex = 0;
    // Use data attributes for event delegation
    const accountId = msg.accountId || msg.account_id || currentAccountId;
    item.dataset.id = msg.id;
    item.dataset.accid = accountId;
    
    item.innerHTML = `
        <div class="unread-dot ${isUnread ? '' : 'invisible'}"></div>
        <input type="checkbox" class="message-checkbox" data-id="${msg.id}" data-accid="${accountId}">
        <div class="message-sender text-ellipsis">${msg.from || msg.accountEmail || ''}</div>
        <div class="message-snippet"><span class="message-subject">${msg.subject || '(no subject)'}</span> - ${msg.snippet}</div>
        <div class="message-date">
            <span class="message-date-text">
                ${(() => {
                    const date = new Date(msg.internalDate);
                    const today = new Date();
                    const isToday = date.getDate() === today.getDate() && 
                                    date.getMonth() === today.getMonth() && 
                                    date.getFullYear() === today.getFullYear();
                    return isToday ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : date.toLocaleDateString();
                })()}
            </span>
            <div class="message-item-actions">
                <button class="hover-action-btn toggle-read" title="${isUnread ? 'Mark as Read' : 'Mark as Unread'}">
                    <i class="fa-solid ${isUnread ? 'fa-envelope-open' : 'fa-envelope'}"></i>
                </button>
                <button class="hover-action-btn archive" title="Archive">
                    <i class="fa-solid fa-box-archive"></i>
                </button>
                <button class="hover-action-btn delete" title="Delete">
                    <i class="fa-solid fa-trash"></i>
                </button>
            </div>
        </div>
    `;
    return item;
}

/**
 * Render message list
 */
function renderMessages(messages, append = false) {
    const list = document.getElementById('message-list');
    if (!list) return;
    
    if (!append) {
        list.innerHTML = '';
        const multiSelect = document.getElementById('multi-select-checkbox');
        if (multiSelect) multiSelect.checked = false;
        
        const selectionInfo = document.getElementById('selection-info');
        if (selectionInfo) selectionInfo.style.display = 'none';
        
        currentMessages = [];
    }
    if (!messages || messages.length === 0) {
        if (!append) list.innerHTML = '<div class="no-results">No messages found.</div>';
        return;
    }
    
    if (append) {
        currentMessages = currentMessages.concat(messages);
    } else {
        currentMessages = messages;
    }

    // Apply current sort
    const sorted = [...currentMessages].sort((a, b) => {
        let valA = a[sortColumn] || '';
        let valB = b[sortColumn] || '';
        if (typeof valA === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();
        
        if (valA < valB) return sortOrder === 'asc' ? -1 : 1;
        if (valA > valB) return sortOrder === 'asc' ? 1 : -1;
        return 0;
    });

    const fragment = document.createDocumentFragment();
    sorted.forEach(msg => {
        fragment.appendChild(createMessageRow(msg));
    });

    if (!append) list.innerHTML = '';
    list.appendChild(fragment);

    updateSortIcons();
    if (!append) initResizers();
}

function updateSortIcons() {
    document.querySelectorAll('.sort-icon').forEach(el => el.innerHTML = '');
    const icon = sortOrder === 'asc' ? '<i class="fa-solid fa-chevron-up"></i>' : '<i class="fa-solid fa-chevron-down"></i>';
    const target = document.getElementById(`sort-${sortColumn}`);
    if (target) target.innerHTML = icon;
}

function sortMessages(column) {
    if (sortColumn === column) {
        sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = column;
        sortOrder = 'asc';
    }
    renderMessages(currentMessages, false);
}

let isResizing = false;
function initResizers() {
    const resizers = document.querySelectorAll('.resizer');
    resizers.forEach(resizer => {
        resizer.onmousedown = (e) => {
            e.stopPropagation();
            isResizing = true;
            const column = resizer.getAttribute('data-column');
            const startX = e.pageX;
            const startWidth = columnWidths[column];
            
            document.body.classList.add('resizing');

            const onMouseMove = (e) => {
                if (!isResizing) return;
                const width = startWidth + (e.pageX - startX);
                if (width > 50) {
                    columnWidths[column] = width;
                    document.documentElement.style.setProperty(`--${column}-width`, `${width}px`);
                }
            };

            const onMouseUp = () => {
                isResizing = false;
                document.body.classList.remove('resizing');
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            };

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        };
    });
}

function toggleAllMessages(checkbox) {
    const checkboxes = document.querySelectorAll('.message-checkbox');
    checkboxes.forEach(cb => cb.checked = checkbox.checked);
    updateSelectionCount();
}

function updateSelectionCount() {
    const checkboxes = document.querySelectorAll('.message-checkbox:checked');
    const count = checkboxes.length;
    const info = document.getElementById('selection-info');
    const span = document.getElementById('selection-count');
    
    if (count > 0) {
        info.style.display = 'flex';
        span.innerText = count;
    } else {
        info.style.display = 'none';
        document.getElementById('multi-select-checkbox').checked = false;
    }
}

/**
 * Perform batch action on selected messages
 */
async function performBatchAction(action) {
    if (!action) return;
    const checkboxes = document.querySelectorAll('.message-checkbox:checked');
    const messageIdsByAccount = {};
    
    checkboxes.forEach(cb => {
        const accId = cb.getAttribute('data-accid');
        const msgId = cb.getAttribute('data-id');
        if (!messageIdsByAccount[accId]) messageIdsByAccount[accId] = [];
        messageIdsByAccount[accId].push(msgId);
    });
    
    let addLabelIds = [];
    let removeLabelIds = [];
    
    if (action === 'mark-read') removeLabelIds.push('UNREAD');
    else if (action === 'mark-unread') addLabelIds.push('UNREAD');
    else if (action === 'archive') removeLabelIds.push('INBOX');
    else if (action === 'trash') {
        addLabelIds.push('TRASH');
        removeLabelIds.push('INBOX');
    } else if (action === 'delete') {
        const count = checkboxes.length;
        if (globalSettings.WARN_BEFORE_DELETE !== 'false') {
            if (!confirm(`Are you sure you want to permanently delete ${count} message${count > 1 ? 's' : ''}?`)) {
                document.getElementById('action-dropdown').value = '';
                return;
            }
        }
        
        for (const accId in messageIdsByAccount) {
            const ids = messageIdsByAccount[accId];
            const res = await fetch(`/accounts/${accId}/messages/batch-delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: ids })
            });
            if (!res.ok) {
                const error = await res.json();
                alert("Error: " + (error.detail || res.statusText));
                return;
            }
        }
        
        refreshMailbox();
        document.getElementById('action-dropdown').value = '';
        return;
    }
    
    for (const accId in messageIdsByAccount) {
        const ids = messageIdsByAccount[accId];
        const res = await fetch(`/accounts/${accId}/messages/batch-modify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: ids,
                addLabelIds: addLabelIds,
                removeLabelIds: removeLabelIds
            })
        });

        if (res.status === 403) {
            const error = await res.json();
            alert(error.detail || "Insufficient permissions. Please re-authenticate.");
            return;
        } else if (!res.ok) {
            const error = await res.json();
            alert("Error: " + (error.detail || res.statusText));
            return;
        }
    }
    
    if (currentLabel === 'SEARCH') performSearch();
    else if (currentAccountId === null) loadMailbox(currentLabel);
    else {
        const acc = accounts.find(a => a.id == currentAccountId);
        loadAccountMailbox(currentAccountId, acc ? acc.email : '', currentLabel);
    }
    
    document.getElementById('action-dropdown').value = '';
}

/**
 * Refresh current mailbox view
 */
window.refreshMailbox = function() {
    const btn = document.querySelector('#refresh-btn i');
    if (btn) btn.classList.add('fa-spin');
    
    const callback = () => {
        setTimeout(() => {
            if (btn) btn.classList.remove('fa-spin');
        }, 500);
    };

    if (currentLabel === 'SEARCH') performSearch(false, true).then(callback);
    else if (currentAccountId === null) loadMailbox(currentLabel, false, true).then(callback);
    else {
        const acc = accounts.find(a => a.id == currentAccountId);
        loadAccountMailbox(currentAccountId, acc ? acc.email : '', currentLabel, false, true).then(callback);
    }
}

/**
 * Handle messages from the detail iframe for auto-resizing or from standalone windows for refresh
 */
window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'detail_iframe_height') {
        try {
            const iframe = document.getElementById('detail-html-frame');
            if (!iframe || iframe.contentWindow !== e.source) return;

            const measured = parseInt(e.data.height, 10) || 0;
            const panel = document.getElementById('message-detail-panel');

            let maxHeight = Math.max(window.innerHeight - 120, 400);
            if (panel) {
                const header = panel.querySelector('.detail-header');
                const headerH = header ? header.getBoundingClientRect().height : 0;
                const panelH = panel.getBoundingClientRect().height || window.innerHeight;
                maxHeight = Math.max(200, panelH - headerH - 40);
            }

            const desired = Math.min(measured + 20, maxHeight);
            const last = parseInt(iframe.dataset.lastHeight || '0', 10);

            if (Math.abs(desired - last) > 30) {
                iframe.style.height = desired + 'px';
                iframe.style.width = '100%';
                iframe.style.display = 'block';
                iframe.dataset.lastHeight = String(desired);
            }

            if (measured > desired) {
                if (panel) panel.style.overflowY = 'auto';
            } else {
                if (panel) panel.style.overflowY = 'visible';
            }
        } catch (err) {
            console.error('Failed to resize iframe from message', err);
        }
    } else if (e.data && e.data.type === 'prosciutto_refresh') {
        // Apply data immediately for low latency if provided
        if (e.data.data) {
            globalSettings = { ...globalSettings, ...e.data.data };
            
            // Re-apply immediate UI changes
            if (globalSettings.THEME) applyTheme(globalSettings.THEME);
            
            setSidebarCollapsed(globalSettings.ALWAYS_COLLAPSE_SIDEBAR === 'true');
            
            updateStarredMailboxVisibility();
            updateDisclosureTriangles();
        }

        // Still refresh from server to be 100% sure and update accounts
        loadSettings();
        loadAccounts().then(() => {
            if (currentAccountId === null) {
                loadMailbox(currentLabel);
            }
        });
    }
});

function notifyOpener(data = null) {
    if (window.opener && !window.opener.closed) {
        window.opener.postMessage({ type: 'prosciutto_refresh', data: data }, window.location.origin);
    }
}

/**
 * Render an existing draft in the composer
 */
async function renderDraftInComposer(id, accId) {
    const panel = document.getElementById('message-detail-panel');
    panel.classList.add('open');
    panel.innerHTML = '<div class="loading-indicator">Loading draft...</div>';

    try {
        const res = await fetch(`/accounts/${accId}/messages/${id}`);
        const msg = await res.json();
        
        renderNewComposerInPanel(accId);
        
        // Fill the fields
        document.getElementById('panel-compose-to').value = msg.to || '';
        document.getElementById('panel-compose-subject').value = msg.subject || '';
        document.getElementById('panel-compose-cc').value = msg.cc || '';
        document.getElementById('panel-compose-bcc').value = msg.bcc || '';
        document.getElementById('panel-compose-draft-id').value = id;
        document.getElementById('panel-compose-thread-id').value = msg.threadId || '';
        document.getElementById('panel-compose-in-reply-to').value = msg.inReplyTo || '';
        document.getElementById('panel-compose-references').value = msg.references || '';
        
        if (msg.cc) toggleComposeField('cc');
        if (msg.bcc) toggleComposeField('bcc');

        const useHtml = msg.html_body ? true : false;
        const checkbox = document.getElementById('panel-compose-is-html');
        checkbox.checked = useHtml;
        
        const htmlDiv = document.getElementById('panel-compose-body-html');
        const textArea = document.getElementById('panel-compose-body');
        const markupBtns = document.getElementById('panel-compose-markup-btns');

        if (useHtml) {
            htmlDiv.innerHTML = msg.html_body;
            htmlDiv.classList.remove('display-none');
            textArea.classList.add('display-none');
        } else {
            textArea.value = msg.body;
            textArea.classList.remove('display-none');
            htmlDiv.classList.add('display-none');
        }
        
    } catch (err) {
        console.error(err);
        panel.innerHTML = '<div class="error-indicator">Failed to load draft.</div>';
    }
}

/**
 * Show message details in the side panel
 */
window.showMessage = async function(id, accId, isSingleView = false) {
    if (currentLabel === 'DRAFT') {
        renderDraftInComposer(id, accId);
        return;
    }
    
    if (!accId) {
        const activeAcc = accounts.find(a => a.is_active) || accounts[0];
        accId = activeAcc ? activeAcc.id : null;
    }
    
    // Highlight selected
    document.querySelectorAll('.message-item').forEach(item => item.classList.remove('selected'));
    const selectedItem = document.getElementById(`msg-${id}`);
    if (selectedItem) selectedItem.classList.add('selected');

    const panel = document.getElementById('message-detail-panel');
    panel.classList.add('open');
    if (isSingleView) panel.classList.add('single-view');
    panel.innerHTML = '<div class="loading-indicator">Loading message...</div>';

    try {
        const res = await fetch(`/accounts/${accId}/messages/${id}`);
        const msg = await res.json();
        
        const isUnread = msg.labelIds && msg.labelIds.includes('UNREAD');
        
        // Auto-mark as read after 3 seconds if setting enabled
        if (isUnread && globalSettings.MARK_READ_AUTOMATICALLY !== 'false') {
            setTimeout(async () => {
                const currentPanel = document.getElementById('message-detail-panel');
                if (currentPanel && currentPanel.classList.contains('open')) {
                    await fetch(`/accounts/${accId}/messages/batch-modify`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            ids: [id],
                            removeLabelIds: ['UNREAD']
                        })
                    });
                    const msgRow = document.getElementById(`msg-${id}`);
                    if (msgRow) msgRow.classList.remove('unread');
                    const listDot = document.querySelector(`#msg-${id} .unread-dot`);
                    if (listDot) listDot.classList.add('invisible');
                    const detailDot = document.getElementById('detail-unread-dot');
                    if (detailDot) detailDot.classList.add('invisible');
                    const toggleIcon = document.getElementById('toggle-read-btn');
                    if (toggleIcon) {
                        toggleIcon.innerHTML = '<i class="fa-solid fa-envelope"></i>';
                        toggleIcon.title = 'Mark as Unread';
                        toggleIcon.setAttribute('onclick', `toggleReadStatus('${id}', ${accId}, false)`);
                    }
                }
            }, 3000);
        }

        let hasRemoteImages = false;
        let sanitizedHtml = msg.html_body;
        
        if (msg.html_body && globalSettings.LOAD_REMOTE_IMAGES !== 'true') {
            if (/<img[^>]+src=["'](?!data:)[^"']+["']/i.test(msg.html_body)) {
                hasRemoteImages = true;
                const csp = '<meta http-equiv="Content-Security-Policy" content="img-src \'self\' data:;">';
                sanitizedHtml = csp + msg.html_body;
            }
        }

        const isTrashOrSpam = currentLabel === 'TRASH' || currentLabel === 'SPAM';
        const trashIcon = isTrashOrSpam ? 'fa-trash-can' : 'fa-trash';
        let trashTitle = isTrashOrSpam ? 'Permanently Delete' : 'Trash';
        let trashDisabledAttr = '';
        let trashStyleAttr = '';
        
        if (isTrashOrSpam && !globalSettings.CAN_PERMANENTLY_DELETE) {
            trashTitle = 'Permanently Delete (Disabled in .env)';
            trashDisabledAttr = 'disabled="disabled"';
            trashStyleAttr = 'class="trash-disabled"';
        }

        panel.innerHTML = `
            <div class="detail-header">
                <div class="panel-actions display-flex gap-10 mb-15 align-center">
                    <button class="action-btn" onclick="window.renderComposerInPanel('${msg.id}', ${accId}, 'reply')" title="Reply"><i class="fa-solid fa-reply"></i></button>
                    <button class="action-btn" onclick="window.renderComposerInPanel('${msg.id}', ${accId}, 'replyAll')" title="Reply All"><i class="fa-solid fa-reply-all"></i></button>
                    <button class="action-btn" onclick="window.renderComposerInPanel('${msg.id}', ${accId}, 'forward')" title="Forward"><i class="fa-solid fa-share"></i></button>
                    <div class="panel-spacer"></div>
                    <button class="action-btn" id="toggle-read-btn" onclick="window.toggleReadStatus('${msg.id}', ${accId}, ${isUnread})" title="${isUnread ? 'Mark as Read' : 'Mark as Unread'}">
                        <i class="fa-solid ${isUnread ? 'fa-envelope-open' : 'fa-envelope'}"></i>
                    </button>
                    <button class="action-btn" onclick="window.archiveMessage('${msg.id}', ${accId})" title="Archive"><i class="fa-solid fa-box-archive"></i></button>
                    <button class="action-btn" id="label-picker-btn" onclick="window.showLabelPicker(event, '${msg.id}', ${accId})" title="Label"><i class="fa-solid fa-tag"></i></button>
                    <button class="action-btn" onclick="window.trashMessage('${msg.id}', ${accId})" title="${trashTitle}" ${trashDisabledAttr} ${trashStyleAttr}><i class="fa-solid ${trashIcon}"></i></button>
                    ${isSingleView ? '' : '<span class="back-btn ml-10" onclick="hideMessageDetail()"><i class="fa-solid fa-xmark"></i></span>'}
                </div>
                <div class="detail-subject">
                    <div id="detail-unread-dot" class="unread-dot ${isUnread ? '' : 'invisible'} display-inline-block mr-10 v-middle"></div>
                    <span class="v-middle">${msg.subject || '(no subject)'}</span>
                </div>
                <div class="detail-meta">
                    <div class="meta-row">
                        <span class="meta-label">From:</span>
                        <span class="meta-value"><strong>${msg.from || ''}</strong></span>
                    </div>
                    <div class="meta-row">
                        <span class="meta-label">To:</span>
                        <span class="meta-value">${msg.to || ''}</span>
                    </div>
                    <div class="meta-row">
                        <span class="meta-label">Date:</span>
                        <span class="meta-value">${new Date(msg.internalDate).toLocaleString()}</span>
                    </div>
                </div>
            </div>
            ${hasRemoteImages ? `
                <div id="remote-images-banner" class="remote-images-banner">
                    <span>External images are blocked for your privacy.</span>
                    <button class="compose-btn btn-inline font-12" onclick="loadImages()">Display images below</button>
                </div>
            ` : ''}
            <div class="detail-body-container">
                ${msg.html_body ? 
                    '<iframe id="detail-html-frame"></iframe>' : 
                    `<div class="detail-body">${msg.body || msg.snippet || ''}</div>`
                }
            </div>
        `;

        if (msg.html_body) {
            const iframe = document.getElementById('detail-html-frame');
            const doc = iframe.contentDocument || iframe.contentWindow.document;
            const resizeScript = '\n<script>\n(function(){\n  function sendHeight(){\n    try{\n      var h = Math.max(document.body.scrollHeight||0, document.documentElement.scrollHeight||0, document.body.offsetHeight||0, document.documentElement.offsetHeight||0, document.body.getBoundingClientRect().height||0);\n      parent.postMessage({type: "detail_iframe_height", height: Math.ceil(h)}, "*");\n    }catch(e){/*ignore*/}\n  }\n  if (document.readyState === "complete") sendHeight(); else window.addEventListener("load", sendHeight);\n  if (window.ResizeObserver) {\n    try{ new ResizeObserver(sendHeight).observe(document.body); }catch(e){}\n  }\n  setTimeout(sendHeight, 200); setTimeout(sendHeight, 1000);\n})();\n<\/script>\n';
            doc.open();
            doc.write(sanitizedHtml + resizeScript);
            doc.close();
            iframe.style.width = '100%';
            iframe.style.display = 'block';
        }
        
        panel.dataset.currentMessage = JSON.stringify(msg);
        panel.dataset.currentAccountId = accId;

    } catch (err) {
        console.error(err);
        panel.innerHTML = '<div class="error-indicator">Failed to load message.</div>';
    }
}

window.loadImages = function() {
    const panel = document.getElementById('message-detail-panel');
    const msg = JSON.parse(panel.dataset.currentMessage);
    const banner = document.getElementById('remote-images-banner');
    if (banner) banner.style.display = 'none';

    const iframe = document.getElementById('detail-html-frame');
    if (iframe && msg.html_body) {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        const resizeScript = '\n<script>\n(function(){\n  function sendHeight(){\n    try{\n      var h = Math.max(document.body.scrollHeight||0, document.documentElement.scrollHeight||0, document.body.offsetHeight||0, document.documentElement.offsetHeight||0, document.body.getBoundingClientRect().height||0);\n      parent.postMessage({type: "detail_iframe_height", height: Math.ceil(h)}, "*");\n    }catch(e){}\n  }\n  if (document.readyState === "complete") sendHeight(); else window.addEventListener("load", sendHeight);\n  if (window.ResizeObserver) { try{ new ResizeObserver(sendHeight).observe(document.body); }catch(e){} }\n  setTimeout(sendHeight, 200); setTimeout(sendHeight, 1000);\n})();\n<\/script>\n';
        doc.open();
        doc.write(msg.html_body + resizeScript);
        doc.close();
    }
}

window.toggleComposeField = function(field) {
    
    const group = document.getElementById('group-' + field);
    const toggle = document.getElementById('toggle-' + field);
    if (group) {
        
        group.classList.remove('display-none');
        group.style.display = 'flex';
    }
    if (toggle) toggle.style.display = 'none';
}

window.doComposerAction = function(command, value = null) {
    const isHtml = document.getElementById('panel-compose-is-html');
    const textArea = document.getElementById('panel-compose-body');
    const htmlDiv = document.getElementById('panel-compose-body-html');
    
    const useHtml = isHtml && isHtml.checked;
    
    
    if (useHtml) {
        if (htmlDiv) {
            htmlDiv.focus();
            document.execCommand(command, false, value);
        }
    } else if (textArea) {
        // 1. Capture current selection state
        const start = textArea.selectionStart;
        const end = textArea.selectionEnd;
        const text = textArea.value;
        const selectedText = text.substring(start, end);
        
        
        let replacement = '';
        let startOffset = 0;

        switch (command) {
            case 'bold':
                replacement = `**${selectedText || 'bold text'}**`;
                startOffset = 2;
                break;
            case 'italic':
                replacement = `*${selectedText || 'italic text'}*`;
                startOffset = 1;
                break;
            case 'underline':
                replacement = `_${selectedText || 'underline text'}_`;
                startOffset = 1;
                break;
            case 'insertUnorderedList':
                replacement = `- ${selectedText || 'list item'}`;
                startOffset = 2;
                break;
            case 'insertOrderedList':
                replacement = `1. ${selectedText || 'list item'}`;
                startOffset = 3;
                break;
            case 'outdent':
                replacement = selectedText.replace(/^ {1,4}/gm, '');
                break;
            case 'indent':
                replacement = selectedText ? selectedText.replace(/^/gm, '    ') : '    ';
                break;
            case 'createLink':
                replacement = `[${selectedText || 'link text'}](${value || 'http://url'})`;
                startOffset = 1;
                break;
            case 'unlink':
                replacement = selectedText.replace(/^\[(.*?)\]\(.*?\)$/, '$1');
                break;
            default:
                replacement = selectedText;
        }


        // 2. Update the text area value
        const beforeText = text.substring(0, start);
        const afterText = text.substring(end);
        textArea.value = beforeText + replacement + afterText;
        
        // 3. Restore focus and selection explicitly
        textArea.focus();
        if (selectedText || command === 'indent' || command === 'outdent') {
            textArea.setSelectionRange(start, start + replacement.length);
        } else {
            const innerStart = start + startOffset;
            const innerEnd = start + replacement.length - (command === 'bold' || command === 'italic' || command === 'underline' ? startOffset : 0);
            textArea.setSelectionRange(innerStart, innerEnd);
        }
    }
}

window.createLink = function() {
    const textArea = document.getElementById('panel-compose-body');
    const isHtml = document.getElementById('panel-compose-is-html');
    
    if (isHtml && isHtml.checked) {
        const url = prompt("Enter the URL:");
        if (url) {
            document.execCommand('createLink', false, url);
        }
    } else if (textArea) {
        const start = textArea.selectionStart;
        const end = textArea.selectionEnd;
        const url = prompt("Enter the URL:");
        if (url) {
            textArea.focus();
            textArea.setSelectionRange(start, end);
            window.doComposerAction('createLink', url);
        }
    }
}

window.toggleComposeFormat = function(checkbox) {
    const htmlDiv = document.getElementById('panel-compose-body-html');
    const textArea = document.getElementById('panel-compose-body');
    
    if (checkbox.checked) {
        // Switch to HTML
        htmlDiv.innerHTML = textArea.value.replace(/\n/g, '<br>');
        
        htmlDiv.classList.remove('display-none');
        textArea.classList.add('display-none');
    } else {
        // Switch to Text
        let text = htmlDiv.innerHTML.replace(/<br\s*[\/]?>/gi, '\n').replace(/<\/p>/gi, '\n\n').replace(/<\/div>/gi, '\n');
        let tempDiv = document.createElement('div');
        tempDiv.innerHTML = text;
        textArea.value = tempDiv.innerText || tempDiv.textContent;
        
        textArea.classList.remove('display-none');
        htmlDiv.classList.add('display-none');
    }
}

/**
 * Render composer in the side panel (for replies/forwards)
 */
window.renderComposerInPanel = function(id, accId, action) {
    const panel = document.getElementById('message-detail-panel');
    const msg = JSON.parse(panel.dataset.currentMessage);
    const isSingleView = panel.classList.contains('single-view');
    
    let to = '';
    let subject = '';
    let bodyText = '';
    let bodyHtml = '';
    let threadId = '';
    let inReplyTo = '';
    let references = '';

    const dateStr = new Date(msg.internalDate).toLocaleString();
    const quoteHtml = msg.html_body || msg.body.replace(/\n/g, '<br>');
    const quoteText = msg.body;

    if (action === 'reply') {
        to = msg.from;
        subject = msg.subject.toLowerCase().startsWith('re:') ? msg.subject : `Re: ${msg.subject}`;
        bodyText = `\n\nOn ${dateStr}, ${msg.from} wrote:\n> ${quoteText.replace(/\n/g, '\n> ')}`;
        bodyHtml = `<br><br>On ${dateStr}, ${msg.from} wrote:<br><blockquote class="blockquote-styled">${quoteHtml}</blockquote>`;
        threadId = msg.threadId;
        inReplyTo = msg.messageId;
        references = (msg.references ? msg.references + " " : "") + msg.messageId;
    } else if (action === 'replyAll') {
        to = [msg.from, msg.to, msg.cc].filter(Boolean).join(', ');
        const currentAccount = accounts.find(a => a.id == accId);
        if (currentAccount) {
            to = to.split(', ').filter(email => !email.includes(currentAccount.email)).join(', ');
        }
        subject = msg.subject.toLowerCase().startsWith('re:') ? msg.subject : `Re: ${msg.subject}`;
        bodyText = `\n\nOn ${dateStr}, ${msg.from} wrote:\n> ${quoteText.replace(/\n/g, '\n> ')}`;
        bodyHtml = `<br><br>On ${dateStr}, ${msg.from} wrote:<br><blockquote class="blockquote-styled">${quoteHtml}</blockquote>`;
        threadId = msg.threadId;
        inReplyTo = msg.messageId;
        references = (msg.references ? msg.references + " " : "") + msg.messageId;
    } else if (action === 'forward') {
        to = '';
        subject = msg.subject.toLowerCase().startsWith('fwd:') ? msg.subject : `Fwd: ${msg.subject}`;
        bodyText = `\n\n---------- Forwarded message ----------\nFrom: ${msg.from}\nDate: ${dateStr}\nSubject: ${msg.subject}\nTo: ${msg.to}\n\n${quoteText}`;
        bodyHtml = `<br><br>---------- Forwarded message ----------<br>From: ${msg.from}<br>Date: ${dateStr}<br>Subject: ${msg.subject}<br>To: ${msg.to}<br><br>${quoteHtml}`;
        threadId = '';
        inReplyTo = '';
        references = '';
    }

    const useHtml = globalSettings.COMPOSE_AS_HTML !== 'false';

    panel.innerHTML = `
        <div class="composer-in-panel">
            <div class="composer-header-styled display-flex justify-between">
                <span class="font-18 bold">${action.charAt(0).toUpperCase() + action.slice(1).replace(/([A-Z])/g, ' $1')}</span>
                <span class="cursor-pointer" onclick="window.showMessage('${id}', ${accId}, ${isSingleView})">✖</span>
            </div>
            <div class="composer-body-styled">
                <div class="form-group">
                    <label>From</label>
                    <select id="panel-compose-from"></select>
                </div>
                <div class="form-group position-relative">
                    <label>To</label>
                    <div class="display-flex align-center flex-1">
                        <input type="text" id="panel-compose-to" value="${to.replace(/"/g, '&quot;')}" class="flex-1">
                        <div class="font-12 text-gray cursor-pointer user-select-none ml-10 text-nowrap">
                            <span id="toggle-cc" onclick="window.toggleComposeField('cc')" class="ml-10">Cc</span>
                            <span id="toggle-bcc" onclick="window.toggleComposeField('bcc')" class="ml-10">Bcc</span>
                        </div>
                    </div>
                </div>
                <div id="group-cc" class="form-group display-none">
                    <label>Cc</label>
                    <input type="text" id="panel-compose-cc" placeholder="Cc" value="">
                </div>
                <div id="group-bcc" class="form-group display-none">
                    <label>Bcc</label>
                    <input type="text" id="panel-compose-bcc" placeholder="Bcc" value="">
                </div>
                <div class="form-group">
                    <label>Subject</label>
                    <input type="text" id="panel-compose-subject" value="${subject.replace(/"/g, '&quot;')}">
                </div>
                <div id="panel-compose-toolbar" class="composer-toolbar justify-between">
                    <div id="panel-compose-markup-btns" class="display-flex gap-5">
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('bold'); return false;" title="Bold"><i class="fa-solid fa-bold"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('italic'); return false;" title="Italic"><i class="fa-solid fa-italic"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('underline'); return false;" title="Underline"><i class="fa-solid fa-underline"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('insertUnorderedList'); return false;" title="Bullet List"><i class="fa-solid fa-list-ul"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('insertOrderedList'); return false;" title="Numbered List"><i class="fa-solid fa-list-ol"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('outdent'); return false;" title="Outdent"><i class="fa-solid fa-outdent"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('indent'); return false;" title="Indent"><i class="fa-solid fa-indent"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.createLink(); return false;" title="Insert Link"><i class="fa-solid fa-link"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('unlink'); return false;" title="Remove Link"><i class="fa-solid fa-link-slash"></i></button>
                    </div>
                    <div class="display-flex align-center">
                        <label class="switch">
                            <input type="checkbox" id="panel-compose-is-html" onchange="window.toggleComposeFormat(this)" ${useHtml ? 'checked' : ''}>
                            <span class="slider"></span>
                        </label>
                        <span class="font-13 text-gray ml-5">HTML Mode</span>
                    </div>
                </div>
                <div class="form-group flex-1 display-flex flex-column border-none mt-5">
                    <div id="panel-compose-body-html" contenteditable="true" class="${useHtml ? '' : 'display-none'} composer-body-editable">${bodyHtml}</div>
                    <textarea id="panel-compose-body" placeholder="Body" class="${useHtml ? 'display-none' : ''} composer-body-textarea">${bodyText}</textarea>
                </div>
                <input type="hidden" id="panel-compose-thread-id" value="${threadId}">
                <input type="hidden" id="panel-compose-in-reply-to" value="${inReplyTo}">
                <input type="hidden" id="panel-compose-references" value="${references}">
                <input type="hidden" id="panel-compose-draft-id" value="">
                <div class="display-flex justify-between mt-20 pb-20">
                    <button class="compose-btn btn-fixed-120 bg-light-gray" onclick="saveDraftFromPanel(event, ${accId})">Save</button>
                    <button class="compose-btn btn-fixed-120" onclick="sendEmailFromPanel(event, ${accId})">Send</button>
                </div>
            </div>
        </div>
    `;

    const select = document.getElementById('panel-compose-from');
    accounts.filter(a => a.is_active).forEach(acc => {
        const opt = document.createElement('option');
        opt.value = acc.id;
        opt.innerText = acc.email;
        if (acc.id == accId) opt.selected = true;
        select.appendChild(opt);
    });

    const textArea = document.getElementById('panel-compose-body');
    textArea.focus();
    textArea.setSelectionRange(0, 0);

    setupAutocomplete('panel-compose-to');
    setupAutocomplete('panel-compose-cc');
    setupAutocomplete('panel-compose-bcc');
}

/**
 * Send email from the side panel composer
 */
async function sendEmailFromPanel(event, accId) {
    const fromAccId = document.getElementById('panel-compose-from').value;
    const isHtml = document.getElementById('panel-compose-is-html').checked;
    const body = isHtml ? document.getElementById('panel-compose-body-html').innerHTML : document.getElementById('panel-compose-body').value;
    const cc = document.getElementById('panel-compose-cc').value;
    const bcc = document.getElementById('panel-compose-bcc').value;
    const draftId = document.getElementById('panel-compose-draft-id').value;

    const data = {
        to: document.getElementById('panel-compose-to').value,
        subject: document.getElementById('panel-compose-subject').value,
        body: body,
        cc: cc || null,
        bcc: bcc || null,
        isHtml: isHtml,
        threadId: document.getElementById('panel-compose-thread-id').value || null,
        inReplyTo: document.getElementById('panel-compose-in-reply-to').value || null,
        references: document.getElementById('panel-compose-references').value || null,
        draftId: draftId || null
    };
    
    const btn = event.target;
    const originalText = btn.innerText;
    btn.innerText = 'Sending...';
    btn.disabled = true;

    try {
        const res = await fetch(`/accounts/${fromAccId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        
        if (res.ok) {
            alert(result.message);
            const panel = document.getElementById('message-detail-panel');
            if (panel.classList.contains('single-view')) {
                if (panel.dataset.currentMessage) {
                    const msg = JSON.parse(panel.dataset.currentMessage);
                    window.showMessage(msg.id, accId, true);
                } else {
                    window.close();
                }
            } else {
                hideMessageDetail();
                refreshMailbox();
            }
        } else {
            alert("Error sending email: " + (result.detail || "Unknown error"));
            btn.innerText = originalText;
            btn.disabled = false;
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while sending the email");
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

/**
 * Save draft from the side panel composer
 */
async function saveDraftFromPanel(event, accId) {
    const fromAccId = document.getElementById('panel-compose-from').value;
    const isHtml = document.getElementById('panel-compose-is-html').checked;
    const body = isHtml ? document.getElementById('panel-compose-body-html').innerHTML : document.getElementById('panel-compose-body').value;
    const cc = document.getElementById('panel-compose-cc').value;
    const bcc = document.getElementById('panel-compose-bcc').value;
    const draftIdInput = document.getElementById('panel-compose-draft-id');

    const data = {
        to: document.getElementById('panel-compose-to').value,
        subject: document.getElementById('panel-compose-subject').value,
        body: body,
        cc: cc || null,
        bcc: bcc || null,
        isHtml: isHtml,
        threadId: document.getElementById('panel-compose-thread-id').value || null,
        inReplyTo: document.getElementById('panel-compose-in-reply-to').value || null,
        references: document.getElementById('panel-compose-references').value || null,
        draftId: draftIdInput.value || null
    };
    
    const btn = event.target;
    const originalText = btn.innerText;
    btn.innerText = 'Saving...';
    btn.disabled = true;

    try {
        const res = await fetch(`/accounts/${fromAccId}/drafts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        
        if (res.ok) {
            // Update draft ID for future saves
            if (result.draft && result.draft.id) {
                draftIdInput.value = result.draft.id;
            }
            btn.innerText = 'Saved';
            setTimeout(() => {
                btn.innerText = 'Save';
                btn.disabled = false;
            }, 2000);
            
            // Refresh mailbox if in DRAFT view
            if (currentLabel === 'DRAFT') {
                refreshMailbox();
            }
        } else {
            alert("Error saving draft: " + (result.detail || "Unknown error"));
            btn.innerText = originalText;
            btn.disabled = false;
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while saving the draft");
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

function replyMessage(id, accId) {
    renderComposerInPanel(id, accId, 'reply');
}

function replyAllMessage(id, accId) {
    renderComposerInPanel(id, accId, 'replyAll');
}

function forwardMessage(id, accId) {
    renderComposerInPanel(id, accId, 'forward');
}

window.toggleReadStatus = async function(id, accId, currentlyUnread) {
    const addLabelIds = currentlyUnread ? [] : ['UNREAD'];
    const removeLabelIds = currentlyUnread ? ['UNREAD'] : [];
    
    const res = await fetch(`/accounts/${accId}/messages/batch-modify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ids: [id],
            addLabelIds: addLabelIds,
            removeLabelIds: removeLabelIds
        })
    });
    
    if (res.ok) {
        notifyOpener();
        const isNowUnread = !currentlyUnread;
        const msgRow = document.getElementById(`msg-${id}`);
        if (msgRow) {
            if (isNowUnread) msgRow.classList.add('unread');
            else msgRow.classList.remove('unread');
        }
        const listDot = document.querySelector(`#msg-${id} .unread-dot`);
        if (listDot) {
            if (isNowUnread) listDot.classList.remove('invisible');
            else listDot.classList.add('invisible');
        }
        const detailDot = document.getElementById('detail-unread-dot');
        if (detailDot) {
            if (isNowUnread) detailDot.classList.remove('invisible');
            else detailDot.classList.add('invisible');
        }
        const toggleIcon = document.getElementById('toggle-read-btn');
        if (toggleIcon) {
            toggleIcon.innerHTML = `<i class="fa-solid ${isNowUnread ? 'fa-envelope-open' : 'fa-envelope'}"></i>`;
            toggleIcon.title = isNowUnread ? 'Mark as Read' : 'Mark as Unread';
            toggleIcon.setAttribute('onclick', `window.toggleReadStatus('${id}', ${accId}, ${isNowUnread})`);
        }
        const hoverToggleBtn = document.querySelector(`#msg-${id} .toggle-read`);
        if (hoverToggleBtn) {
            hoverToggleBtn.innerHTML = `<i class="fa-solid ${isNowUnread ? 'fa-envelope-open' : 'fa-envelope'}"></i>`;
            hoverToggleBtn.title = isNowUnread ? 'Mark as Read' : 'Mark as Unread';
            hoverToggleBtn.setAttribute('onclick', `event.stopPropagation(); window.toggleReadStatus('${id}', ${accId}, ${isNowUnread})`);
        }
    } else {
        alert("Failed to update status");
    }
}

window.archiveMessage = async function(id, accId) {
    const res = await fetch(`/accounts/${accId}/messages/batch-modify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ids: [id],
            removeLabelIds: ['INBOX']
        })
    });
    if (res.ok) {
        notifyOpener();
        const panel = document.getElementById('message-detail-panel');
        if (panel && panel.classList.contains('single-view')) {
            window.close();
        } else {
            hideMessageDetail();
            window.refreshMailbox();
        }
    } else {
        alert("Failed to archive");
    }
}

window.trashMessage = async function(id, accId) {
    const isTrashOrSpam = currentLabel === 'TRASH' || currentLabel === 'SPAM';
    
    if (isTrashOrSpam) {
        if (globalSettings.WARN_BEFORE_DELETE !== 'false') {
            if (!confirm("Are you sure you want to permanently delete this message?")) return;
        }
        
        const res = await fetch(`/accounts/${accId}/messages/${id}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            notifyOpener();
            const panel = document.getElementById('message-detail-panel');
            if (panel && panel.classList.contains('single-view')) {
                window.close();
            } else {
                hideMessageDetail();
                window.refreshMailbox();
            }
        } else {
            alert("Failed to permanently delete message");
        }
        return;
    }

    const res = await fetch(`/accounts/${accId}/messages/batch-modify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ids: [id],
            addLabelIds: ['TRASH']
        })
    });
    if (res.ok) {
        notifyOpener();
        const panel = document.getElementById('message-detail-panel');
        if (panel && panel.classList.contains('single-view')) {
            window.close();
        } else {
            hideMessageDetail();
            window.refreshMailbox();
        }
    } else {
        alert("Failed to move to trash");
    }
}

window.showLabelPicker = async function(event, msgId, accId) {
    event.stopPropagation();
    
    const existing = document.getElementById('label-picker-dropdown');
    if (existing) {
        existing.remove();
        if (existing.dataset.msgId === msgId) return;
    }

    const btn = event.currentTarget;
    const rect = btn.getBoundingClientRect();

    const dropdown = document.createElement('div');
    dropdown.id = 'label-picker-dropdown';
    dropdown.className = 'label-picker-dropdown';
    dropdown.dataset.msgId = msgId;
    dropdown.style.top = `${rect.bottom + 5}px`;
    dropdown.style.left = `${rect.left}px`;
    dropdown.innerHTML = '<div class="p-10 font-12 text-gray">Loading labels...</div>';
    
    document.body.appendChild(dropdown);

    const closeHandler = (e) => {
        if (!dropdown.contains(e.target) && e.target !== btn) {
            dropdown.remove();
            document.removeEventListener('click', closeHandler);
        }
    };
    setTimeout(() => document.addEventListener('click', closeHandler), 0);

    try {
        const res = await fetch(`/accounts/${accId}/labels`);
        const labels = await res.json();
        
        if (labels.length === 0) {
            dropdown.innerHTML = '<div class="p-10 font-12 text-gray">No labels found</div>';
        } else {
            dropdown.innerHTML = '';
            labels.forEach(l => {
                const item = document.createElement('div');
                item.className = 'label-picker-item cursor-pointer font-13 p-8-15';
                item.innerText = l.name;
                item.onclick = async () => {
                    dropdown.innerHTML = '<div class="p-10 font-12 text-gray">Applying...</div>';
                    await applyLabel(msgId, accId, l.id);
                    dropdown.remove();
                    document.removeEventListener('click', closeHandler);
                };
                item.onmouseover = () => item.className = 'label-picker-item cursor-pointer font-13 bg-light-gray';
                item.onmouseout = () => item.className = 'label-picker-item cursor-pointer font-13';
                dropdown.appendChild(item);
            });
        }
    } catch (err) {
        console.error(err);
        dropdown.innerHTML = '<div class="p-10 font-12 error-indicator">Error loading labels</div>';
    }
}

window.applyLabel = async function(msgId, accId, labelId) {
    try {
        const res = await fetch(`/accounts/${accId}/messages/batch-modify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: [msgId],
                addLabelIds: [labelId]
            })
        });
        
        if (res.ok) {
            notifyOpener();
            window.refreshMailbox();
        } else {
            const error = await res.json();
            alert("Error applying label: " + (error.detail || "Unknown error"));
        }
    } catch (err) {
        console.error(err);
        alert("An error occurred while applying the label");
    }
}

function openMessageInNewWindow(id, accId) {
    if (!accId) {
        const activeAcc = accounts.find(a => a.is_active) || accounts[0];
        accId = activeAcc ? activeAcc.id : null;
    }
    const url = `?messageId=${id}&accountId=${accId}`;
    window.open(url, '_blank', 'width=800,height=680');
}

function hideMessageDetail() {
    const panel = document.getElementById('message-detail-panel');
    if (panel) {
        panel.classList.remove('open');
    }
    document.querySelectorAll('.message-item').forEach(item => item.classList.remove('selected'));
}

/**
 * Render new message composer in the side panel
 */
function renderNewComposerInPanel(accId) {
    if (!accId) {
        const defaultId = globalSettings.DEFAULT_COMPOSE_ACCOUNT;
        const defaultAcc = accounts.find(a => String(a.id) === String(defaultId) && a.is_active);
        const activeAcc = defaultAcc || accounts.find(a => a.is_active) || accounts[0];
        if (activeAcc) accId = activeAcc.id;
    }
    
    const panel = document.getElementById('message-detail-panel');
    delete panel.dataset.currentMessage;
    delete panel.dataset.currentAccountId;
    
    panel.classList.add('open');
    const isSingleView = panel.classList.contains('single-view');
    const discardAction = isSingleView ? 'window.close()' : 'hideMessageDetail()';
    
    const useHtml = globalSettings.COMPOSE_AS_HTML !== 'false';

    panel.innerHTML = `
        <div class="composer-in-panel">
            <div class="composer-header-styled display-flex justify-between">
                <span class="font-18 bold">New Message</span>
            </div>
            <div class="composer-body-styled">
                <div class="form-group">
                    <label>From</label>
                    <select id="panel-compose-from"></select>
                </div>
                <div class="form-group position-relative">
                    <label>To</label>
                    <div class="display-flex align-center flex-1">
                        <input type="text" id="panel-compose-to" value="" class="flex-1">
                        <div class="font-12 text-gray cursor-pointer user-select-none ml-10 text-nowrap">
                            <span id="toggle-cc" onclick="window.toggleComposeField('cc')" class="ml-10">Cc</span>
                            <span id="toggle-bcc" onclick="window.toggleComposeField('bcc')" class="ml-10">Bcc</span>
                        </div>
                    </div>
                </div>
                <div id="group-cc" class="form-group display-none">
                    <label>Cc</label>
                    <input type="text" id="panel-compose-cc" placeholder="Cc" value="">
                </div>
                <div id="group-bcc" class="form-group display-none">
                    <label>Bcc</label>
                    <input type="text" id="panel-compose-bcc" placeholder="Bcc" value="">
                </div>
                <div class="form-group">
                    <label>Subject</label>
                    <input type="text" id="panel-compose-subject" value="">
                </div>
                <div id="panel-compose-toolbar" class="composer-toolbar justify-between">
                    <div id="panel-compose-markup-btns" class="display-flex gap-5">
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('bold'); return false;" title="Bold"><i class="fa-solid fa-bold"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('italic'); return false;" title="Italic"><i class="fa-solid fa-italic"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('underline'); return false;" title="Underline"><i class="fa-solid fa-underline"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('insertUnorderedList'); return false;" title="Bullet List"><i class="fa-solid fa-list-ul"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('insertOrderedList'); return false;" title="Numbered List"><i class="fa-solid fa-list-ol"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('outdent'); return false;" title="Outdent"><i class="fa-solid fa-outdent"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('indent'); return false;" title="Indent"><i class="fa-solid fa-indent"></i></button>
                        <div class="toolbar-divider"></div>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.createLink(); return false;" title="Insert Link"><i class="fa-solid fa-link"></i></button>
                        <button type="button" tabindex="-1" class="toolbar-btn" onmousedown="event.preventDefault(); window.doComposerAction('unlink'); return false;" title="Remove Link"><i class="fa-solid fa-link-slash"></i></button>
                    </div>
                    <div class="display-flex align-center">
                        <label class="switch">
                            <input type="checkbox" id="panel-compose-is-html" onchange="window.toggleComposeFormat(this)" ${useHtml ? 'checked' : ''}>
                            <span class="slider"></span>
                        </label>
                        <span class="font-13 text-gray ml-5">HTML Mode</span>
                    </div>
                </div>
                <div class="form-group flex-1 display-flex flex-column border-none mt-5">
                    <div id="panel-compose-body-html" contenteditable="true" class="${useHtml ? '' : 'display-none'} composer-body-editable"></div>
                    <textarea id="panel-compose-body" placeholder="Body" class="${useHtml ? 'display-none' : ''} composer-body-textarea"></textarea>
                </div>
                <input type="hidden" id="panel-compose-thread-id" value="">
                <input type="hidden" id="panel-compose-in-reply-to" value="">
                <input type="hidden" id="panel-compose-references" value="">
                <input type="hidden" id="panel-compose-draft-id" value="">
                <div class="display-flex justify-between mt-20 pb-20">
                    <button class="compose-btn btn-fixed-120 bg-light-gray" onclick="saveDraftFromPanel(event, ${accId})">Save</button>
                    <button class="compose-btn btn-fixed-120" onclick="sendEmailFromPanel(event, ${accId})">Send</button>
                </div>
            </div>
        </div>
    `;

    const select = document.getElementById('panel-compose-from');
    accounts.filter(a => a.is_active).forEach(acc => {
        const opt = document.createElement('option');
        opt.value = acc.id;
        opt.innerText = acc.email;
        if (acc.id == accId) opt.selected = true;
        select.appendChild(opt);
    });

    setupAutocomplete('panel-compose-to');
    setupAutocomplete('panel-compose-cc');
    setupAutocomplete('panel-compose-bcc');
}

/**
 * Open composer (either in a new window or side panel)
 */
window.showComposer = function() {
    const isNewWindow = globalSettings.COMPOSE_NEW_WINDOW !== 'false';
    if (isNewWindow) {
        const defaultId = globalSettings.DEFAULT_COMPOSE_ACCOUNT;
        const defaultAcc = accounts.find(a => String(a.id) === String(defaultId) && a.is_active);
        const activeAcc = defaultAcc || accounts.find(a => a.is_active) || accounts[0];
        const accId = currentAccountId || (activeAcc ? activeAcc.id : '');
        const url = `?compose=true&accountId=${accId}`;
        window.open(url, '_blank', 'width=800,height=900');
    } else {
        renderNewComposerInPanel(currentAccountId);
    }
}

window.addAccount = function() {
    location.href = '/auth/login';
}

// Listen for system theme changes if set to automatic
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (globalSettings.THEME === 'automatic') {
        applyTheme('automatic');
    }
});

function setSidebarCollapsed(collapsed) {
    const sidebar = document.getElementById('sidebar');
    const icon = document.getElementById('sidebar-toggle-icon');
    if (!sidebar || !icon) return;

    if (collapsed) {
        sidebar.classList.add('collapsed');
        icon.classList.remove('fa-chevron-left');
        icon.classList.add('fa-chevron-right');
    } else {
        sidebar.classList.remove('collapsed');
        icon.classList.remove('fa-chevron-right');
        icon.classList.add('fa-chevron-left');
    }
}

function toggleSidebarCollapse() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    setSidebarCollapsed(!sidebar.classList.contains('collapsed'));
}

function expandSidebarIfCollapsed(event) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar.classList.contains('collapsed')) {
        event.stopPropagation();
        toggleSidebarCollapse();
    }
}

/**
 * Autocomplete logic
 */
let autocompleteTimeout = null;

function setupAutocomplete(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    input.addEventListener('input', () => {
        clearTimeout(autocompleteTimeout);
        const val = input.value;
        const lastComma = val.lastIndexOf(',');
        const q = (lastComma === -1 ? val : val.substring(lastComma + 1)).trim();
        
        if (q.length < 1) {
            closeAutocomplete();
            return;
        }

        autocompleteTimeout = setTimeout(() => {
            fetchAutocomplete(q, input);
        }, 200);
    });

    input.addEventListener('keydown', (e) => {
        const dropdown = document.getElementById('autocomplete-dropdown');
        if (!dropdown) return;

        const items = dropdown.querySelectorAll('.autocomplete-item');
        let selectedIndex = Array.from(items).findIndex(item => item.classList.contains('selected'));

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (selectedIndex < items.length - 1) {
                if (selectedIndex >= 0) items[selectedIndex].classList.remove('selected');
                items[selectedIndex + 1].classList.add('selected');
                items[selectedIndex + 1].scrollIntoView({ block: 'nearest' });
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (selectedIndex > 0) {
                items[selectedIndex].classList.remove('selected');
                items[selectedIndex - 1].classList.add('selected');
                items[selectedIndex - 1].scrollIntoView({ block: 'nearest' });
            }
        } else if (e.key === 'Enter' || e.key === 'Tab') {
            const selected = dropdown.querySelector('.autocomplete-item.selected');
            if (selected) {
                e.preventDefault();
                selectAutocompleteItem(selected.dataset.email, selected.dataset.name, input);
            }
        } else if (e.key === 'Escape') {
            closeAutocomplete();
        }
    });

    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('autocomplete-dropdown');
        if (dropdown && !dropdown.contains(e.target) && e.target !== input) {
            closeAutocomplete();
        }
    });
}

async function fetchAutocomplete(q, input) {
    const includeRecents = globalSettings.AUTOCOMPLETE_RECENTS !== 'false';
    const enabledAccounts = globalSettings.AUTOCOMPLETE_ENABLED_ACCOUNTS || "";
    
    if (!includeRecents && !enabledAccounts) {
        closeAutocomplete();
        return;
    }

    try {
        const url = `/autocomplete?q=${encodeURIComponent(q)}&include_recents=${includeRecents}&account_ids=${enabledAccounts}`;
        const res = await fetch(url);
        const results = await res.json();
        
        if (results.length === 0) {
            closeAutocomplete();
            return;
        }

        showAutocompleteDropdown(results, input);
    } catch (e) {
        console.error("Autocomplete fetch failed", e);
    }
}

function showAutocompleteDropdown(results, input) {
    closeAutocomplete();
    
    const rect = input.getBoundingClientRect();
    const dropdown = document.createElement('div');
    dropdown.id = 'autocomplete-dropdown';
    dropdown.className = 'autocomplete-dropdown';
    dropdown.style.top = `${rect.bottom + window.scrollY}px`;
    dropdown.style.left = `${rect.left + window.scrollX}px`;
    dropdown.style.width = `${rect.width}px`;

    results.forEach((r, index) => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item' + (index === 0 ? ' selected' : '');
        item.dataset.email = r.email;
        item.dataset.name = r.name || '';
        
        const avatar = r.photo_url 
            ? `<img src="${r.photo_url}" alt="">`
            : `<i class="fa-solid fa-user"></i>`;
            
        item.innerHTML = `
            <div class="avatar">${avatar}</div>
            <div class="info">
                <div class="name">${r.name || r.email}</div>
                <div class="email">${r.email}</div>
            </div>
            <div class="type-badge ${r.type}">${r.type}</div>
        `;
        
        item.onclick = () => selectAutocompleteItem(r.email, r.name, input);
        dropdown.appendChild(item);
    });

    document.body.appendChild(dropdown);
}

function selectAutocompleteItem(email, name, input) {
    const val = input.value;
    const lastComma = val.lastIndexOf(',');
    const base = lastComma === -1 ? '' : val.substring(0, lastComma + 1).trim() + ' ';
    
    const formatted = name ? `"${name}" <${email}>` : email;
    input.value = base + formatted + ', ';
    input.focus();
    closeAutocomplete();
}

function closeAutocomplete() {
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (dropdown) dropdown.remove();
}

/**
 * Keyboard shortcuts and navigation
 */
function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Only proceed if keyboard shortcuts are enabled in settings
        if (globalSettings.KEYBOARD_SHORTCUTS_ENABLED !== 'true') return;

        // Protection against keyboard shortcuts invocation when text entry elements are in focus
        const isTextEntry = (e.target.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'color'].includes(e.target.type)) ||
                            e.target.tagName === 'TEXTAREA' ||
                            e.target.isContentEditable === true ||
                            e.target.contentEditable === 'true';
        if (isTextEntry) return;
        // Keyboard navigation
        if (e.key === 'ArrowDown' || e.key === 'j') {
            navigateFocus(1);
            e.preventDefault();
        } else if (e.key === 'ArrowUp' || e.key === 'k') {
            navigateFocus(-1);
            e.preventDefault();
        } else if (e.key === 'ArrowRight' || e.key === 'l') {
            if (document.activeElement && document.activeElement.classList.contains('modal-tab')) {
                navigateFocus(1);
                e.preventDefault();
            } else if (document.activeElement && document.activeElement.classList.contains('mailbox-item')) {
                // Right arrow expands unified mailbox
                const triangle = document.activeElement.querySelector('.disclosure-triangle');
                if (triangle && !triangle.classList.contains('expanded')) {
                    triangle.click();
                }
                e.preventDefault();
            }
        } else if (e.key === 'ArrowLeft' || e.key === 'h') {
            if (document.activeElement && document.activeElement.classList.contains('modal-tab')) {
                navigateFocus(-1);
                e.preventDefault();
            } else if (document.activeElement && document.activeElement.classList.contains('mailbox-item')) {
                // Left arrow collapses unified mailbox
                const triangle = document.activeElement.querySelector('.disclosure-triangle');
                if (triangle && triangle.classList.contains('expanded')) {
                    triangle.click();
                }
                e.preventDefault();
            }
        } else if (e.key === 'Enter' || e.key === ' ') {
            const active = document.activeElement;
            if (active && (
                active.classList.contains('message-item') ||
                active.classList.contains('mailbox-item') ||
                active.classList.contains('account-subset-item') ||
                active.classList.contains('action-btn') ||
                active.classList.contains('compose-btn') ||
                active.classList.contains('modal-tab') ||
                active.classList.contains('column-header') ||
                active.id === 'refresh-btn' ||
                active.id === 'settings-trigger' ||
                active.id === 'sidebar-toggle-widget' ||
                active.id === 'search-execute-btn' ||
                active.id === 'advanced-search-toggle' ||
                active.id === 'load-more-btn' ||
                active.id === 'empty-mailbox-btn'
            )) {
                if (e.key === ' ' && active.classList.contains('message-item')) {
                    // Space on message item toggles checkbox
                    const cb = active.querySelector('.message-checkbox');
                    if (cb) {
                        cb.checked = !cb.checked;
                        updateSelectionCount();
                    }
                } else {
                    if (typeof active.click === 'function') {
                        active.click();
                    } else {
                        const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                        active.dispatchEvent(evt);
                    }
                }
                e.preventDefault();
            }
        }

        // Shortcuts
        if (e.key === '1') {
            window.loadMailbox('INBOX');
        } else if (e.key === '2') {
            window.loadMailbox('SENT');
        } else if (e.key === '3') {
            window.loadMailbox('DRAFT');
        } else if (e.key === '4') {
            window.loadMailbox('TRASH');
        } else if (e.key === '5') {
            window.loadMailbox('');
        } else if (e.key === 'b') {
            toggleSidebarCollapse();
        } else if (e.key === 'c') {
            window.showComposer();
        } else if (e.key === 'r') {
            window.refreshMailbox();
        } else if (e.key === ',') {
            window.openSettings();
        } else if (e.key === 'x') {
            // Keyboard selection of checkboxes
            const active = document.activeElement;
            if (active) {
                if (active.classList.contains('message-item')) {
                    const cb = active.querySelector('.message-checkbox');
                    if (cb) {
                        cb.checked = !cb.checked;
                        updateSelectionCount();
                    }
                } else if (active.id === 'multi-select-checkbox') {
                    active.checked = !active.checked;
                    toggleAllMessages(active);
                }
            }
        }

    });
}

function navigateFocus(direction) {
    const focusableSelector = '.mailbox-item, .account-subset-item, .message-item, .compose-btn, .action-btn, .modal-tab, .column-header, #search-execute-btn, #advanced-search-toggle, #settings-trigger, #refresh-btn, #sidebar-toggle-widget, #multi-select-checkbox, #load-more-btn, #empty-mailbox-btn';
    const items = Array.from(document.querySelectorAll(focusableSelector)).filter(el => {
        // Filter out items that are not visible or have display: none
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
    });
    
    if (items.length === 0) return;

    let index = items.indexOf(document.activeElement);
    
    if (index === -1) {
        if (direction > 0) items[0].focus();
        else items[items.length - 1].focus();
    } else {
        let nextIndex = index + direction;
        // Wrap around or stay at boundaries? Gmail stays at boundaries for j/k.
        if (nextIndex >= 0 && nextIndex < items.length) {
            items[nextIndex].focus();
        }
    }
}

// Start the app
init();
