const chatBox = document.querySelector('#chat-box');
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');
const uploadBtn = document.querySelector('#upload-btn');
const pdfInput = document.querySelector('#pdf-input');

let clarifyMode = false;
let clarificationAnswers = [];
let clarifyAttempts = 0;
let contextId = null;

uploadBtn.addEventListener('click', () => pdfInput.click());

pdfInput.addEventListener('change', async () => {
  if (!pdfInput.files.length) return;
  const file = pdfInput.files[0];
  appendMessage('bot', `📄 Uploading <b>${file.name}</b>...`);
  const formData = new FormData();
  formData.append('pdf', file);
  try {
    const res = await fetch('/upload', { method: 'POST', body: formData });
    const data = await res.json();
    appendMessage('bot', `✅ Uploaded: <b>${data.filename}</b>`);
    appendMessage('bot', `<i>Extracted preview:</i><br>${data.text}`);
  } catch (err) {
    appendMessage('bot', '⚠️ Upload failed.');
  }
});

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  appendMessage('user', message);
  chatInput.value = '';

  const thinking = appendMessage('bot', '💬 Thinking...');

  try {
    const body = clarifyMode
      ? {
          clarified: true,
          clarification_answers: clarificationAnswers,
          clarify_attempts: clarifyAttempts,
          context_id: contextId
        }
      : {
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
      clarificationAnswers = [];
      appendMessage('bot', '<b>Please clarify:</b>');
      data.questions.forEach(q => appendMessage('bot', q));
      return;
    }

    // Handle results
    if (data.status === 'results') {
      clarifyMode = false;
      clarifyAttempts = 0;
      clarificationAnswers = [];
      contextId = data.context_id;

      appendMessage('bot', `<b>Search query:</b> ${data.query}`);
      data.cases.forEach(c => {
        appendMessage(
          'bot',
          `<b>${c.title}</b> (${c.citation || 'No citation'})<br>` +
            `Relevance: <b>${c.relevance_score}%</b><br>` +
            `${c.relevance_reason}<br>` +
            `<a href="${c.pdf_link}" target="_blank">View Case</a>`
        );
      });
      appendMessage('bot', '💡 You can add more information to refine the search.');
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
});

// Allow refinement: when user types “add more” message, treat it as context addition
document.querySelector('#refine-btn')?.addEventListener('click', async () => {
  const extra = prompt("Enter additional information to refine your case:");
  if (!extra) return;
  appendMessage('user', extra);
  const thinking = appendMessage('bot', '🔄 Refining search...');
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: extra, adding_info: true, context_id: contextId })
  });
  const data = await res.json();
  thinking.remove();
  if (data.status === 'results') {
    appendMessage('bot', `<b>Refined results:</b>`);
    data.cases.forEach(c => {
      appendMessage(
        'bot',
        `<b>${c.title}</b> (${c.citation || 'No citation'})<br>` +
          `Relevance: <b>${c.relevance_score}%</b><br>` +
          `${c.relevance_reason}<br>` +
          `<a href="${c.pdf_link}" target="_blank">View Case</a>`
      );
    });
  }
});

function appendMessage(sender, text) {
  const div = document.createElement('div');
  div.classList.add('message', sender, 'fade-in');
  div.innerHTML = text;
  chatBox.appendChild(div);
  chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: 'smooth' });
  return div;
}
