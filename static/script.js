// =====================================================
// GLOBAL STATE
// =====================================================
const chatBox = document.querySelector('#chat-box');
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');
const uploadBtn = document.querySelector('#upload-btn');
const pdfInput = document.querySelector('#pdf-input');
const analyzeBtn = document.querySelector('#analyze-btn');
const draftBtn = document.querySelector('#draft-btn');
const draftGenerateBtn = document.querySelector('#draft-generate-btn');
const sessionIdEl = document.querySelector('#session-id');

let clarifyMode = false;
let clarificationAnswers = [];
let clarifyAttempts = 0;
let contextId = null;
let currentAnalysis = {};
let currentCases = [];

// =====================================================
// INITIALIZATION
// =====================================================
document.addEventListener('DOMContentLoaded', async () => {
    // Load context on page load
    await loadContext();
    
    // Setup tab switching
    setupTabs();
    
    // Setup event listeners
    setupEventListeners();
});

// =====================================================
// TAB SWITCHING
// =====================================================
function setupTabs() {
    const tabs = document.querySelectorAll('.panel-tab');
    const tabContents = document.querySelectorAll('.panel-tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTab = tab.getAttribute('data-tab');
            
            // Update active states
            tabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(tc => tc.classList.remove('active'));
            
            tab.classList.add('active');
            document.getElementById(`tab-${targetTab}`).classList.add('active');
        });
    });
}

// =====================================================
// EVENT LISTENERS
// =====================================================
function setupEventListeners() {
    // PDF Upload
    uploadBtn.addEventListener('click', () => pdfInput.click());
    pdfInput.addEventListener('change', handlePDFUpload);
    
    // Analyze button
    analyzeBtn.addEventListener('click', handleAnalyze);
    
    // Draft button
    draftBtn.addEventListener('click', () => {
        // Switch to draft tab
        document.querySelector('[data-tab="draft"]').click();
    });
    
    // Draft generate button
    draftGenerateBtn.addEventListener('click', handleDraftGenerate);
    
    // Chat form
    chatForm.addEventListener('submit', handleChatSubmit);
}

// =====================================================
// CONTEXT MANAGEMENT
// =====================================================
async function loadContext() {
    try {
        const res = await fetch('/context');
        const data = await res.json();
        contextId = data.context_id;
        updateSessionIndicator(contextId);
        
        if (data.context && data.context.analysis) {
            currentAnalysis = data.context.analysis;
            updateAnalysisPanel(data.context.analysis);
        }
        
        if (data.context && data.context.cases) {
            currentCases = data.context.cases;
            updateCasesPanel(data.context.cases);
        }
    } catch (err) {
        console.error('Error loading context:', err);
    }
}

function updateSessionIndicator(id) {
    if (sessionIdEl && id) {
        sessionIdEl.textContent = id.substring(0, 8) + '...';
        sessionIdEl.title = id;
    }
}

// =====================================================
// PDF UPLOAD
// =====================================================
async function handlePDFUpload() {
    if (!pdfInput.files.length) return;
    const file = pdfInput.files[0];
    
    appendMessage('bot', `📄 Uploading <b>${file.name}</b>...`);
    
    const formData = new FormData();
    formData.append('pdf', file);
    
    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        
        if (data.error) {
            appendMessage('bot', `⚠️ Error: ${data.error}`);
            return;
        }
        
        appendMessage('bot', `✅ Uploaded: <b>${data.filename}</b>`);
        appendMessage('bot', `<i>Extracted text preview:</i><br>${data.text.substring(0, 300)}...`);
        
        contextId = data.context_id;
        updateSessionIndicator(contextId);
        
        if (data.analysis) {
            currentAnalysis = data.analysis;
            updateAnalysisPanel(data.analysis);
            // Switch to analysis tab
            document.querySelector('[data-tab="analysis"]').click();
        }
    } catch (err) {
        appendMessage('bot', '⚠️ Upload failed.');
        console.error(err);
    }
}

// =====================================================
// ANALYZE
// =====================================================
async function handleAnalyze() {
    if (!contextId) {
        appendMessage('bot', '⚠️ Please upload a PDF or describe your case first.');
        return;
    }
    
    appendMessage('bot', '⚖️ Analyzing case...');
    
    try {
        const res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ context_id: contextId })
        });
        
        const data = await res.json();
        
        if (data.error) {
            appendMessage('bot', `⚠️ Error: ${data.error}`);
            return;
        }
        
        if (data.analysis) {
            currentAnalysis = data.analysis;
            updateAnalysisPanel(data.analysis);
            appendMessage('bot', '✅ Analysis complete! Check the Analysis panel.');
            // Switch to analysis tab
            document.querySelector('[data-tab="analysis"]').click();
        }
    } catch (err) {
        appendMessage('bot', '⚠️ Analysis failed.');
        console.error(err);
    }
}

// =====================================================
// CHAT SUBMIT
// =====================================================
async function handleChatSubmit(e) {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    
    appendMessage('user', message);
    chatInput.value = '';
    
    const thinking = appendMessage('bot', '💬 Thinking...');
    
    try {
        // Always send the message - backend will extract answers if in clarification mode
        const body = {
            message,
            clarify_attempts: clarifyAttempts,
            context_id: contextId
        };
        
        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        
        const data = await res.json();
        thinking.remove();
        
        // Handle clarifying
        if (data.status === 'clarifying') {
            clarifyMode = true;
            clarifyAttempts = data.clarify_attempts;
            contextId = data.context_id;
            updateSessionIndicator(contextId);
            clarificationAnswers = [];
            
            let questionsText = '<b>I need a bit more information:</b><br><br>';
            data.questions.forEach((q, idx) => {
                questionsText += `${idx + 1}. ${q}<br>`;
            });
            questionsText += '<br>Please provide answers to these questions in your next message.';
            appendMessage('bot', questionsText);
            
            if (data.analysis) {
                currentAnalysis = data.analysis;
                updateAnalysisPanel(data.analysis);
            }
            return;
        }
        
        // Handle results
        if (data.status === 'results') {
            clarifyMode = false;
            clarifyAttempts = 0;
            clarificationAnswers = [];
            contextId = data.context_id;
            updateSessionIndicator(contextId);
            
            if (data.analysis) {
                currentAnalysis = data.analysis;
                updateAnalysisPanel(data.analysis);
            }
            
            if (data.summary) {
                appendMessage('bot', `<b>Summary:</b> ${data.summary}`);
            }
            
            appendMessage('bot', `<b>Search query:</b> ${data.query}`);
            
            if (data.cases && data.cases.length > 0) {
                currentCases = data.cases;
                updateCasesPanel(data.cases);
                appendMessage('bot', `📚 Found ${data.cases.length} relevant cases. Check the Cases panel.`);
                // Switch to cases tab
                document.querySelector('[data-tab="cases"]').click();
            } else {
                appendMessage('bot', 'No relevant cases found.');
            }
            
            appendMessage('bot', '💡 You can add more information to refine the search or generate a document.');
            return;
        }
        
        if (data.status === 'error') {
            appendMessage('bot', `⚠️ ${data.message}`);
        }
    } catch (err) {
        thinking.remove();
        appendMessage('bot', '⚠️ Server error.');
        console.error(err);
    }
}

// =====================================================
// DRAFT GENERATION
// =====================================================
async function handleDraftGenerate() {
    if (!contextId) {
        appendMessage('bot', '⚠️ Please upload a PDF or describe your case first.');
        return;
    }
    
    const docType = document.getElementById('draft-type').value;
    const draftContent = document.getElementById('draft-content');
    
    draftContent.innerHTML = '<p class="empty-state">Generating document... ⏳</p>';
    
    try {
        const res = await fetch('/draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ context_id: contextId, doc_type: docType })
        });
        
        const data = await res.json();
        
        if (data.error) {
            draftContent.innerHTML = `<p class="empty-state">⚠️ Error: ${data.error}</p>`;
            return;
        }
        
        if (data.document) {
            displayDraft(data.document);
            appendMessage('bot', `✅ Generated ${docType}! Check the Draft panel.`);
        }
    } catch (err) {
        draftContent.innerHTML = '<p class="empty-state">⚠️ Draft generation failed.</p>';
        console.error(err);
    }
}

// =====================================================
// PANEL UPDATES
// =====================================================
function updateAnalysisPanel(analysis) {
    const content = document.getElementById('analysis-content');
    
    if (!analysis || Object.keys(analysis).length === 0) {
        content.innerHTML = '<p class="empty-state">No analysis available yet. Upload a PDF or describe your case to begin.</p>';
        return;
    }
    
    let html = '';
    
    // Facts
    if (analysis.facts && analysis.facts.length > 0) {
        html += '<div class="analysis-section"><h4>📋 Facts</h4><ul>';
        analysis.facts.forEach(fact => {
            html += `<li>${escapeHtml(fact)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Parties
    if (analysis.parties && analysis.parties.length > 0) {
        html += '<div class="analysis-section"><h4>👥 Parties</h4>';
        analysis.parties.forEach(party => {
            const name = party.name || party;
            const role = party.role || 'Unknown';
            html += `<div class="party-item"><span>${escapeHtml(name)}</span><span style="color: #888;">${escapeHtml(role)}</span></div>`;
        });
        html += '</div>';
    }
    
    // Jurisdictions
    if (analysis.jurisdictions && analysis.jurisdictions.length > 0) {
        html += '<div class="analysis-section"><h4>⚖️ Jurisdictions</h4><ul>';
        analysis.jurisdictions.forEach(jur => {
            html += `<li>${escapeHtml(jur)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Legal Issues
    if (analysis.legal_issues && analysis.legal_issues.length > 0) {
        html += '<div class="analysis-section"><h4>⚖️ Legal Issues</h4><ul>';
        analysis.legal_issues.forEach(issue => {
            html += `<li>${escapeHtml(issue)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Causes of Action
    if (analysis.causes_of_action && analysis.causes_of_action.length > 0) {
        html += '<div class="analysis-section"><h4>📜 Causes of Action</h4><ul>';
        analysis.causes_of_action.forEach(cause => {
            html += `<li>${escapeHtml(cause)}</li>`;
        });
        html += '</ul></div>';
    }
    
    if (!html) {
        html = '<p class="empty-state">Analysis in progress...</p>';
    }
    
    content.innerHTML = html;
}

function updateCasesPanel(cases) {
    const content = document.getElementById('cases-content');
    
    if (!cases || cases.length === 0) {
        content.innerHTML = '<p class="empty-state">No case law results yet. Start a search to see relevant cases.</p>';
        return;
    }
    
    let html = '';
    cases.forEach(c => {
        html += `
            <div class="case-item">
                <div class="case-title">${escapeHtml(c.title || 'Untitled')}</div>
                ${c.citation ? `<div class="case-citation">${escapeHtml(c.citation)}</div>` : ''}
                <div class="case-relevance">
                    <span class="relevance-score">Relevance: ${c.relevance_score || 0}%</span>
                </div>
                ${c.relevance_reason ? `<div class="relevance-reason">${escapeHtml(c.relevance_reason)}</div>` : ''}
                ${c.snippet ? `<div class="case-snippet">${escapeHtml(c.snippet.substring(0, 200))}...</div>` : ''}
                ${c.pdf_link ? `<a href="${c.pdf_link}" target="_blank" class="case-link">View Case →</a>` : ''}
            </div>
        `;
    });
    
    content.innerHTML = html;
}

function displayDraft(docText) {
    const content = document.getElementById('draft-content');
    
    // Convert markdown-style sections to HTML
    let html = '<div class="draft-document">';
    
    // Split by ** sections (markdown bold headers)
    const lines = docText.split('\n');
    let currentSection = '';
    let inSection = false;
    
    lines.forEach((line, idx) => {
        const trimmed = line.trim();
        
        // Check if this is a section header (starts with **)
        if (trimmed.startsWith('**') && trimmed.endsWith('**')) {
            // Close previous section
            if (currentSection) {
                html += `<p>${escapeHtml(currentSection).replace(/\n/g, '<br>')}</p>`;
                currentSection = '';
            }
            
            // Start new section
            const title = trimmed.replace(/\*\*/g, '').trim();
            html += `<h3>${escapeHtml(title)}</h3>`;
            inSection = true;
        } else if (trimmed === '' && currentSection) {
            // Empty line - add paragraph break
            if (currentSection) {
                html += `<p>${escapeHtml(currentSection).replace(/\n/g, '<br>')}</p>`;
                currentSection = '';
            }
        } else {
            // Add to current section
            if (currentSection) {
                currentSection += '\n' + line;
            } else {
                currentSection = line;
            }
        }
    });
    
    // Add remaining content
    if (currentSection) {
        html += `<p>${escapeHtml(currentSection).replace(/\n/g, '<br>')}</p>`;
    }
    
    html += '</div>';
    content.innerHTML = html;
}

// =====================================================
// UTILITIES
// =====================================================
function appendMessage(sender, text) {
    const div = document.createElement('div');
    div.classList.add('message', sender, 'fade-in');
    div.innerHTML = text;
    chatBox.appendChild(div);
    chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: 'smooth' });
    return div;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
