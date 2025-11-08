const chatBox = document.querySelector('#chat-box');
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');
const uploadForm = document.querySelector('#upload-form');
const pdfInput = document.querySelector('#pdf-input');
const uploadBtn = document.querySelector('#upload-btn');

// open file picker
uploadBtn.addEventListener('click', () => pdfInput.click());

// when a file is selected, submit automatically
pdfInput.addEventListener('change', async () => {
    if (!pdfInput.files.length) return;

    const file = pdfInput.files[0];
    appendMessage('bot', `📄 Uploading <b>${file.name}</b>...`);

    const formData = new FormData();
    formData.append('pdf', file);

    try {
        const response = await fetch('/upload', { method: 'POST', body: formData });
        const data = await response.json();
        appendMessage('bot', `✅ Uploaded: <b>${data.filename}</b>`);
        appendMessage('bot', `<i>Extracted preview:</i><br>${data.text}`);
    } catch (err) {
        appendMessage('bot', '⚠️ Failed to upload the PDF.');
        console.error(err);
    }
});

// chat handler (same as before)
chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;

    appendMessage('user', message);
    chatInput.value = '';

    const thinking = appendMessage('bot', '...');
    try {
        const res = await fetch('/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ message })
        });
        const data = await res.json();
        thinking.remove();

        if (typeof data.questions === 'string') appendMessage('bot', data.questions);
        else if (Array.isArray(data.questions)) data.questions.forEach(q => appendMessage('bot', q));

        if (data.cases?.length)
            data.cases.forEach(c =>
                appendMessage('bot', `${c.title} (${c.citation})<br>${c.relevance}<br><a href="${c.pdf_link}" target="_blank">View PDF</a>`)
            );
    } catch (err) {
        thinking.remove();
        appendMessage('bot', '⚠️ There was an error contacting the server.');
        console.error(err);
    }
});

function appendMessage(sender, text) {
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message', sender, 'fade-in');
    msgDiv.innerHTML = text;
    chatBox.appendChild(msgDiv);
    chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: 'smooth' });
    return msgDiv;
}
