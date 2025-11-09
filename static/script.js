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
const draftDownloadBtn = document.querySelector('#draft-download-btn');
const sessionIdEl = document.querySelector('#session-id');
let currentDraftText = '';

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
    
    // Draft download button
    draftDownloadBtn.addEventListener('click', handleDraftDownload);
    
    // Chat form
    chatForm.addEventListener('submit', handleChatSubmit);
    
    // Handle Enter/Shift+Enter in textarea
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit'));
        }
    });
    
    // Auto-resize textarea
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = `${Math.min(chatInput.scrollHeight, 200)}px`;
    });
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
    
    appendMessage('bot', `Uploading <b>${file.name}</b>...`);
    
    const formData = new FormData();
    formData.append('pdf', file);
    
    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        
        if (data.error) {
            appendMessage('bot', `Error: ${data.error}`);
            return;
        }
        
        appendMessage('bot', `Uploaded: <b>${data.filename}</b>`);
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
        appendMessage('bot', 'Upload failed.');
        console.error(err);
    }
}

// =====================================================
// ANALYZE
// =====================================================
async function handleAnalyze() {
    if (!contextId) {
        appendMessage('bot', 'Please upload a PDF or describe your case first.');
        return;
    }
    
    appendMessage('bot', 'Analyzing case...');
    
    try {
        const res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ context_id: contextId })
        });
        
        const data = await res.json();
        
        if (data.error) {
            appendMessage('bot', `Error: ${data.error}`);
            return;
        }
        
        if (data.analysis) {
            currentAnalysis = data.analysis;
            updateAnalysisPanel(data.analysis);
            appendMessage('bot', 'Analysis complete! Check the Analysis panel.');
            // Switch to analysis tab
            document.querySelector('[data-tab="analysis"]').click();
        }
    } catch (err) {
        appendMessage('bot', 'Analysis failed.');
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
    chatInput.style.height = 'auto';
    
    const thinking = appendMessage('bot', 'Thinking...');
    
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
            
            if (data.cases && data.cases.length > 0) {
                currentCases = data.cases;
                updateCasesPanel(data.cases);
                appendMessage('bot', `Found ${data.cases.length} relevant cases. Check the Cases panel.`);
                // Switch to cases tab
                document.querySelector('[data-tab="cases"]').click();
            } else {
                appendMessage('bot', 'No relevant cases found.');
            }
            
            appendMessage('bot', 'You can add more information to refine the search or generate a document.');
            return;
        }
        
        if (data.status === 'error') {
            appendMessage('bot', `${data.message}`);
        }
    } catch (err) {
        thinking.remove();
        appendMessage('bot', 'Server error.');
        console.error(err);
    }
}

// =====================================================
// DRAFT GENERATION
// =====================================================
async function handleDraftGenerate() {
    if (!contextId) {
        appendMessage('bot', 'Please upload a PDF or describe your case first.');
        return;
    }
    
    const docType = document.getElementById('draft-type').value;
    const draftContent = document.getElementById('draft-content');
    
    draftContent.innerHTML = '<p class="empty-state">Generating document...</p>';
    draftDownloadBtn.style.display = 'none';
    
    try {
        const res = await fetch('/draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ context_id: contextId, doc_type: docType })
        });
        
        const data = await res.json();
        
        if (data.error) {
            draftContent.innerHTML = `<p class="empty-state">Error: ${data.error}</p>`;
            return;
        }
        
        if (data.document) {
            currentDraftText = data.document;
            displayDraft(data.document);
            draftDownloadBtn.style.display = 'flex';
            appendMessage('bot', `Generated ${docType}! Check the Draft panel.`);
        }
    } catch (err) {
        draftContent.innerHTML = '<p class="empty-state">Draft generation failed.</p>';
        console.error(err);
    }
}

function handleDraftDownload() {
    if (!currentDraftText) {
        return;
    }
    
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    
    // Set font
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(12);
    
    // Split text into lines that fit the page width
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const margin = 20;
    const maxWidth = pageWidth - (margin * 2);
    
    let y = margin;
    const lineHeight = 7;
    
    // Process the document text
    const lines = currentDraftText.split('\n');
    
    lines.forEach((line) => {
        const trimmed = line.trim();
        
        // Check if it's a section header (starts with **)
        if (trimmed.startsWith('**') && trimmed.endsWith('**')) {
            const title = trimmed.replace(/\*\*/g, '').trim();
            if (y > pageHeight - 30) {
                doc.addPage();
                y = margin;
            }
            doc.setFont('helvetica', 'bold');
            doc.setFontSize(14);
            doc.text(title, margin, y);
            y += lineHeight + 3;
            doc.setFont('helvetica', 'normal');
            doc.setFontSize(12);
        } else if (trimmed === '') {
            // Empty line
            y += lineHeight / 2;
        } else {
            // Regular text
            if (y > pageHeight - 20) {
                doc.addPage();
                y = margin;
            }
            
            // Split long lines
            const splitLines = doc.splitTextToSize(trimmed, maxWidth);
            splitLines.forEach((splitLine) => {
                if (y > pageHeight - 20) {
                    doc.addPage();
                    y = margin;
                }
                doc.text(splitLine, margin, y);
                y += lineHeight;
            });
        }
    });
    
    // Save the PDF
    const docType = document.getElementById('draft-type').value;
    doc.save(`legal-${docType}-${Date.now()}.pdf`);
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
        html += '<div class="analysis-section"><h4>Facts</h4><ul>';
        analysis.facts.forEach(fact => {
            html += `<li>${escapeHtml(fact)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Parties
    if (analysis.parties && analysis.parties.length > 0) {
        html += '<div class="analysis-section"><h4>Parties</h4>';
        analysis.parties.forEach(party => {
            const name = party.name || party;
            const role = party.role || 'Unknown';
            const details = party.details || '';
            html += `<div class="party-item">`;
            html += `<div><strong>${escapeHtml(name)}</strong> <span style="color: #8b7d6b;">(${escapeHtml(role)})</span></div>`;
            if (details) {
                html += `<div style="font-size: 12px; color: #6b5d4f; margin-top: 4px;">${escapeHtml(details)}</div>`;
            }
            html += `</div>`;
        });
        html += '</div>';
    }
    
    // Jurisdictions
    if (analysis.jurisdictions && analysis.jurisdictions.length > 0) {
        html += '<div class="analysis-section"><h4>Jurisdictions</h4><ul>';
        analysis.jurisdictions.forEach(jur => {
            html += `<li>${escapeHtml(jur)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Legal Issues
    if (analysis.legal_issues && analysis.legal_issues.length > 0) {
        html += '<div class="analysis-section"><h4>Legal Issues</h4><ul>';
        analysis.legal_issues.forEach(issue => {
            html += `<li style="margin-bottom: 10px;">${escapeHtml(issue)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Causes of Action
    if (analysis.causes_of_action && analysis.causes_of_action.length > 0) {
        html += '<div class="analysis-section"><h4>Causes of Action</h4><ul>';
        analysis.causes_of_action.forEach(cause => {
            html += `<li style="margin-bottom: 10px;">${escapeHtml(cause)}</li>`;
        });
        html += '</ul></div>';
    }
    
    // Penal Codes
    if (analysis.penal_codes && analysis.penal_codes.length > 0) {
        html += '<div class="analysis-section"><h4>Penal Codes</h4>';
        analysis.penal_codes.forEach(code => {
            const codeText = code.code || code;
            const description = code.description || '';
            const relevance = code.relevance || '';
            html += '<div style="margin-bottom: 16px; padding: 12px; background-color: #fafafa; border-left: 3px solid #7f5539; border-radius: 4px;">';
            html += `<div style="font-weight: 600; color: #5c4033; margin-bottom: 6px;">${escapeHtml(codeText)}</div>`;
            if (description) {
                html += `<div style="font-size: 13px; color: #3e2f24; margin-bottom: 6px;">${escapeHtml(description)}</div>`;
            }
            if (relevance) {
                html += `<div style="font-size: 12px; color: #6b5d4f; font-style: italic;">Relevance: ${escapeHtml(relevance)}</div>`;
            }
            html += '</div>';
        });
        html += '</div>';
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
    
    // Convert markdown-style sections to HTML with better formatting
    let html = '<div class="draft-document">';
    
    const lines = docText.split('\n');
    let currentParagraph = [];
    let listType = null; // 'ul' or 'ol'
    
    lines.forEach((line, idx) => {
        const trimmed = line.trim();
        
        // Section header (starts with **)
        if (trimmed.startsWith('**') && trimmed.endsWith('**')) {
            // Close previous paragraph/list
            if (currentParagraph.length > 0) {
                html += `<p>${currentParagraph.join(' ')}</p>`;
                currentParagraph = [];
            }
            if (listType) {
                html += `</${listType}>`;
                listType = null;
            }
            
            const title = trimmed.replace(/\*\*/g, '').trim();
            html += `<h3>${escapeHtml(title)}</h3>`;
        }
        // List item (starts with - or *)
        else if (trimmed.match(/^[-*]\s+/)) {
            if (currentParagraph.length > 0) {
                html += `<p>${currentParagraph.join(' ')}</p>`;
                currentParagraph = [];
            }
            if (listType !== 'ul') {
                if (listType) {
                    html += `</${listType}>`;
                }
                html += '<ul>';
                listType = 'ul';
            }
            const listItem = trimmed.replace(/^[-*]\s+/, '');
            html += `<li>${escapeHtml(listItem)}</li>`;
        }
        // Numbered list item
        else if (trimmed.match(/^\d+\.\s+/)) {
            if (currentParagraph.length > 0) {
                html += `<p>${currentParagraph.join(' ')}</p>`;
                currentParagraph = [];
            }
            if (listType !== 'ol') {
                if (listType) {
                    html += `</${listType}>`;
                }
                html += '<ol>';
                listType = 'ol';
            }
            const listItem = trimmed.replace(/^\d+\.\s+/, '');
            html += `<li>${escapeHtml(listItem)}</li>`;
        }
        // Empty line
        else if (trimmed === '') {
            if (currentParagraph.length > 0) {
                html += `<p>${currentParagraph.join(' ')}</p>`;
                currentParagraph = [];
            }
            if (listType) {
                html += `</${listType}>`;
                listType = null;
            }
        }
        // Regular text
        else {
            if (listType) {
                html += `</${listType}>`;
                listType = null;
            }
            // Handle bold text (**text**)
            let processedLine = trimmed;
            processedLine = processedLine.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            // Handle italic text (*text*)
            processedLine = processedLine.replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, '<em>$1</em>');
            currentParagraph.push(processedLine);
        }
    });
    
    // Close any remaining paragraph/list
    if (currentParagraph.length > 0) {
        html += `<p>${currentParagraph.join(' ')}</p>`;
    }
    if (listType) {
        html += `</${listType}>`;
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
