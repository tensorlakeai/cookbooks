document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const incidentsList = document.getElementById('incidents-list');
    const detailContent = document.getElementById('detail-content');
    const simulateBtn = document.getElementById('simulate-btn');
    const modal = document.getElementById('sim-modal');
    const closeModal = document.getElementById('close-modal');
    const resolvedCount = document.getElementById('resolved-count');
    const incidentCount = document.getElementById('incident-count');
    const scenarioBtns = document.querySelectorAll('.scenario-card');
    const liveIndicator = document.getElementById('processing-indicator');

    // State
    let incidents = [];
    let selectedIncident = null;

    // Navigation
    const navItems = document.querySelectorAll('.nav-item');
    const views = {
        'Dashboard': document.getElementById('dashboard-view'),
        'History': document.getElementById('history-view'),
        'Settings': document.getElementById('settings-view')
    };

    // Settings & History Elements
    const historyTableBody = document.getElementById('history-table-body');
    const apiStatusList = document.getElementById('api-status-list');
    const clearHistoryBtn = document.getElementById('clear-history-btn');

    // --- Navigation Logic ---
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const targetName = item.querySelector('span').textContent;

            // Update Active State
            navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            // Show Target View
            Object.values(views).forEach(v => v.classList.add('hidden'));
            if (views[targetName]) {
                views[targetName].classList.remove('hidden');
            }

            // Refresh data if needed
            if (targetName === 'History') renderHistoryTable();
            if (targetName === 'Settings') fetchHealthStatus();
        });
    });

    // --- Interaction Logic ---

    // Clear History
    // Clear History
    clearHistoryBtn.addEventListener('click', async () => {
        // Simple confirmation via text change instead of blocking alert for now
        const originalText = clearHistoryBtn.innerHTML;
        clearHistoryBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> Clearing...';
        clearHistoryBtn.disabled = true;

        try {
            console.log('Sending DELETE request to /incidents...');
            const res = await fetch('/incidents', { method: 'DELETE' });

            if (res.ok) {
                console.log('History purged.');
                incidents = [];
                // Update specific views
                updateDashboard();
                renderHistoryTable();

                // Show success feedback
                alert('History cleared successfully');
            } else {
                throw new Error('Server responded with error');
            }
        } catch (e) {
            console.error(e);
            alert('Failed to clear history');
        } finally {
            clearHistoryBtn.innerHTML = originalText;
            clearHistoryBtn.disabled = false;
            lucide.createIcons();
        }
    });

    // Toggle Modal
    simulateBtn.addEventListener('click', () => modal.classList.remove('hidden'));
    closeModal.addEventListener('click', () => modal.classList.add('hidden'));

    // Close modal on outside click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.add('hidden');
    });

    // Handle Scenario Selection (Trigger Alert)
    scenarioBtns.forEach(btn => {
        btn.addEventListener('click', async () => {
            const scenario = btn.dataset.scenario;
            modal.classList.add('hidden');
            await triggerSimulation(scenario);
        });
    });

    // --- API Interactions ---

    async function fetchHealthStatus() {
        apiStatusList.innerHTML = '<div class="loading-spinner">Checking connection...</div>';
        try {
            const res = await fetch('/health');
            const data = await res.json();

            const configs = [
                { name: 'Groq API (LLM)', status: data.groq_configured },
                { name: 'Exa API (Web Search)', status: data.exa_configured },
                { name: 'Browserbase (Screenshots)', status: data.browserbase_configured },
            ];

            apiStatusList.innerHTML = configs.map(c => `
                <div class="api-status-item">
                    <span>${c.name}</span>
                    <span class="status-badge ${c.status ? 'connected' : 'missing'}">
                        ${c.status ? 'Connected' : 'Not Configured'}
                    </span>
                </div>
            `).join('');

        } catch (e) {
            apiStatusList.innerHTML = '<div class="error">Failed to load status</div>';
        }
    }

    async function triggerSimulation(scenario) {
        liveIndicator.classList.remove('hidden');

        let payload = {};
        const timestamp = new Date().toISOString();

        switch (scenario) {
            case 'kafka':
                payload = {
                    service: "payments-api",
                    severity: "critical",
                    summary: "High error rate on /charge - KafkaTimeoutException",
                    timestamp,
                    labels: { env: "prod", team: "payments" }
                };
                break;
            case 'memory':
                payload = {
                    service: "user-service",
                    severity: "critical",
                    summary: "Out of memory errors - container restarting",
                    timestamp,
                    labels: { env: "prod", team: "identity" }
                };
                break;
            case 'db':
                payload = {
                    service: "inventory-api",
                    severity: "warning",
                    summary: "Connection pool exhausted",
                    timestamp,
                    labels: { env: "prod", region: "us-east-1" }
                };
                break;
            case 'deploy':
                payload = {
                    service: "checkout-service",
                    severity: "critical",
                    summary: "500 error spike after v2.4.0 deploy",
                    timestamp,
                    labels: { env: "prod", version: "v2.4.0" }
                };
                break;
        }

        try {
            const res = await fetch('/alert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            console.log('Simulation triggered:', data);

            // Immediately fetch updates
            await fetchIncidents();

            // Switch to dashboard if not already
            navItems[0].click();

            // If new incident created, select it
            if (data.incident) {
                // Find it in the updated list or select 0 index
                setTimeout(() => {
                    const cards = document.querySelectorAll('.incident-card');
                    if (cards.length > 0) cards[0].click();
                }, 500);
            }
        } catch (e) {
            console.error('Error triggering alert:', e);
            alert('Failed to trigger simulation');
        } finally {
            liveIndicator.classList.add('hidden');
        }
    }

    async function fetchIncidents() {
        try {
            const res = await fetch('/incidents');
            const data = await res.json();

            if (data.incidents) {
                incidents = data.incidents.reverse(); // Newest first
                updateDashboard();
                // Also update history table if visible
                if (!document.getElementById('history-view').classList.contains('hidden')) {
                    renderHistoryTable();
                }
            }
        } catch (e) {
            console.error('Failed to fetch incidents:', e);
        }
    }

    // --- UI Updates ---

    function renderHistoryTable() {
        if (!incidents.length) {
            historyTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#666;">No incidents recorded</td></tr>';
            return;
        }

        historyTableBody.innerHTML = incidents.map(inc => `
            <tr>
                <td>${new Date(inc.timestamp || Date.now()).toLocaleString()}</td>
                <td><span style="font-weight:600">${inc.service}</span></td>
                <td><span class="tag ${inc.severity}">${inc.severity}</span></td>
                <td>${inc.alert_summary}</td>
                <td>${inc.root_cause || inc.llm_output?.root_cause || 'N/A'}</td>
                <td>${inc.should_escalate ? '⚠️ Yes' : 'No'}</td>
            </tr>
        `).join('');
    }

    function updateDashboard() {
        // Update Stats
        resolvedCount.textContent = incidents.length;
        incidentCount.textContent = incidents.length;

        // Render List
        renderIncidentsList();
    }

    function renderIncidentsList() {
        incidentsList.innerHTML = '';

        if (incidents.length === 0) {
            incidentsList.innerHTML = `
                <div class="empty-state">
                    <i data-lucide="shield-check"></i>
                    <p>All systems operational</p>
                    <span class="sub-text">Waiting for alerts...</span>
                </div>`;
            return;
        }

        incidents.forEach((inc, index) => {
            const el = document.createElement('div');
            el.className = `incident-card ${selectedIncident === index ? 'active' : ''}`;
            el.onclick = () => selectIncident(index);

            const time = new Date(inc.timestamp || new Date()).toLocaleTimeString();
            const cause = inc.llm_output?.short_summary || inc.root_cause || 'Analyzing...';

            el.innerHTML = `
                <div class="incident-top">
                    <span class="incident-service">${inc.service}</span>
                    <span class="incident-time">${time}</span>
                </div>
                <div class="incident-summary">${inc.alert_summary}</div>
                <div class="incident-tags">
                    <span class="tag ${inc.severity}">${inc.severity}</span>
                </div>
            `;
            incidentsList.appendChild(el);
        });

        // Refresh icons for new elements
        lucide.createIcons();
    }

    function selectIncident(index) {
        selectedIncident = index;
        renderIncidentsList(); // To update active class
        renderDetailView(incidents[index]);
    }

    function renderDetailView(inc) {
        if (!inc) return;

        const output = inc.llm_output || {};
        const confidence = (inc.confidence * 100).toFixed(0);

        const actionsHtml = inc.actions_taken && inc.actions_taken.length > 0
            ? inc.actions_taken.map(a => `<div class="action-item"><i data-lucide="check"></i> ${a}</div>`).join('')
            : '<div class="action-item" style="background:#333;color:#888;">No automated actions taken</div>';

        detailContent.innerHTML = `
            <div class="incident-detail">
                <div class="detail-header">
                    <h2>${inc.service} Issue</h2>
                    <div class="detail-meta">
                        <span><i data-lucide="calendar"></i> ${new Date(inc.timestamp || Date.now()).toLocaleString()}</span>
                        <span><i data-lucide="alert-triangle"></i> ${inc.severity.toUpperCase()}</span>
                    </div>
                </div>

                <div class="section-card">
                    <div class="section-title"><i data-lucide="microscope"></i> AI Diagnosis</div>
                    <div class="root-cause-box">
                        <p>${output.root_cause || inc.root_cause}</p>
                        <div class="confidence-meter">
                            <i data-lucide="zap"></i> ${confidence}% Confidence
                        </div>
                    </div>
                </div>

                <div class="section-card">
                    <div class="section-title"><i data-lucide="activity"></i> Actions Executed</div>
                    ${actionsHtml}
                </div>

                <div class="section-card">
                    <div class="section-title"><i data-lucide="file-text"></i> Alert Payload</div>
                    <div class="code-block">${inc.alert_summary}</div>
                </div>
                
                <div class="section-card">
                    <div class="section-title"><i data-lucide="lightbulb"></i> Recommendations</div>
                    <p style="color:#9ca3af; font-size:13px; line-height:1.5;">
                        ${output.recommendations || "Analysis ongoing..."}
                    </p>
                </div>
            </div>
        `;

        lucide.createIcons();
    }

    // --- Init ---
    fetchIncidents();
    // Poll every 3 seconds
    setInterval(fetchIncidents, 3000);
});
