const DOM = {
    connectScreen: document.getElementById('connection-screen'),
    dashboardScreen: document.getElementById('dashboard-screen'),
    connectForm: document.getElementById('connect-form'),
    dbPathInput: document.getElementById('db-path'),
    disconnectBtn: document.getElementById('disconnect-btn'),
    homeBtn: document.getElementById('home-btn'),
    
    chatHistory: document.getElementById('chat-history'),
    chatInput: document.getElementById('chat-input'),
    sendBtn: document.getElementById('send-btn'),
    
    loadingIndicator: document.getElementById('loading-indicator'),
    tableHead: document.getElementById('table-head'),
    tableBody: document.getElementById('table-body'),
    
    sqlDebugger: document.getElementById('sql-debugger'),
    sqlCodeContent: document.getElementById('sql-code-content'),
    closeSqlBtn: document.getElementById('close-sql-btn')
};

let currentDbPath = '';
const API_URL = 'http://localhost:8000/api/query';

// --- Screen Management ---
DOM.connectForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const path = DOM.dbPathInput.value.trim();
    if (path) {
        currentDbPath = path;
        DOM.connectScreen.classList.remove('active');
        DOM.dashboardScreen.classList.remove('hidden');
        DOM.dashboardScreen.classList.add('active');
    }
});

DOM.disconnectBtn.addEventListener('click', () => {
    currentDbPath = '';
    DOM.dashboardScreen.classList.remove('active');
    DOM.dashboardScreen.classList.add('hidden');
    DOM.connectScreen.classList.add('active');
});

DOM.homeBtn.addEventListener('click', () => {
    // For now, Home acts as a quick disconnect/reset since it's a single dashboard.
    currentDbPath = '';
    DOM.dashboardScreen.classList.remove('active');
    DOM.dashboardScreen.classList.add('hidden');
    DOM.connectScreen.classList.add('active');
});

// --- Chat & Query Management ---
DOM.sendBtn.addEventListener('click', handleSend);
DOM.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
});

async function handleSend() {
    const text = DOM.chatInput.value.trim();
    if (!text) return;
    
    // Add User Message
    addMessage(text, 'user');
    DOM.chatInput.value = '';
    
    // UI Loading State
    DOM.sendBtn.disabled = true;
    DOM.loadingIndicator.classList.remove('hidden');
    
    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: text,
                db_path: currentDbPath
            })
        });
        
        if (!response.ok) {
            throw new Error(`API Error: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Add System Message
        addMessage(data.answer, 'system');
        
        // Render Table Data
        renderTable(data.raw_data);
        
        // Show SQL
        if (data.generated_sql) {
            DOM.sqlCodeContent.textContent = data.generated_sql;
            DOM.sqlDebugger.classList.remove('hidden');
        }
        
    } catch (err) {
        addMessage("Sorry, I encountered an error connecting to the platform: " + err.message, 'system');
    } finally {
        DOM.sendBtn.disabled = false;
        DOM.loadingIndicator.classList.add('hidden');
    }
}

function addMessage(text, sender) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${sender}-msg`;
    
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    
    if (sender === 'user') {
        avatar.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
        </svg>`;
    } else {
        avatar.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a2 2 0 0 1 2 2 2 2 0 0 1-2 2 2 2 0 0 1-2-2 2 2 0 0 1 2-2z"></path><path d="M19 13v-2a7 7 0 0 0-14 0v2"></path><path d="M12 18v3"></path><path d="M9 21h6"></path><path d="M19 9a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-2a2 2 0 0 1 2-2h1z"></path><path d="M5 9H4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2z"></path></svg>`;
    }
    
    const content = document.createElement('div');
    content.className = 'msg-content';
    // Basic bolding parse for markdown
    content.innerHTML = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    msgDiv.appendChild(avatar);
    msgDiv.appendChild(content);
    
    DOM.chatHistory.appendChild(msgDiv);
    DOM.chatHistory.scrollTop = DOM.chatHistory.scrollHeight;
}

function renderTable(rawData) {
    DOM.tableHead.innerHTML = '';
    DOM.tableBody.innerHTML = '';
    
    if (!rawData || !rawData.columns || rawData.data.length === 0) {
        DOM.tableBody.innerHTML = `<tr><td colspan="100%" class="empty-state">No tabular data returned for this query.</td></tr>`;
        return;
    }
    
    // Render Header
    const trHead = document.createElement('tr');
    rawData.columns.forEach(col => {
        const th = document.createElement('th');
        th.textContent = col;
        trHead.appendChild(th);
    });
    DOM.tableHead.appendChild(trHead);
    
    // Render Body
    rawData.data.forEach(row => {
        const tr = document.createElement('tr');
        row.forEach(cell => {
            const td = document.createElement('td');
            td.textContent = cell !== null ? String(cell) : 'NULL';
            tr.appendChild(td);
        });
        DOM.tableBody.appendChild(tr);
    });
}

DOM.closeSqlBtn.addEventListener('click', () => {
    DOM.sqlDebugger.classList.add('hidden');
});
