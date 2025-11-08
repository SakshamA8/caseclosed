const chatBox = document.querySelector('#chat-box');
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');
const uploadForm = document.querySelector('#upload-form');
const pdfInput = document.querySelector('#pdf-input');
const uploadBtn = document.querySelector('#upload-btn');

let clarifyMode = false;
let clarifyAttempts = 0;
let clarificationAnswers = [];

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
  } catch (e) {
    appendMessage('bot', '⚠️ Failed to upload the PDF.');
  }
});

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  appendMessage('user', message);
  chatInput.value = '';

  // Collect clarification answers if needed
  if (clarifyMode) {
    clarificationAnswers.push(message);
  }

  const thinking = appendMessage('bot', '💬 Thinking...');
  try {
    const body = clarifyMode
      ? { clarified: true, clarification_answers: clarificationAnswers, clarify_attempts: clarifyAttempts }
      : { message, clarify_attempts: clarifyAttempts };

    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    const data = await res.json();
    thinking.remove();

    if (data.status === 'clarifying') {
      clarifyMode = true;
      clarifyAttempts = data.clarify_attempts;
      clarificationAnswers = [];
      appendMessage('bot', '<b>Before proceeding, please answer:</b>');
      data.questions.forEach(q => appendMessage('bot', q));
      return;
    }

    if (data.status === 'results') {
      clarifyMode = false;
      clarifyAttempts = 0;
      clarificationAnswers = [];
      appendMessage('bot', `<b>Search Query:</b> ${data.query}`);
      data.cases.forEach(c => {
        appendMessage(
          'bot',
          `<b>${c.title}</b> (${c.citation || 'No citation'})<br>${c.relevance}<br><a href="${c.pdf_link}" target="_blank">View Case</a>`
        );
      });
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

function appendMessage(sender, text) {
  const div = document.createElement('div');
  div.classList.add('message', sender, 'fade-in');
  div.innerHTML = text;
  chatBox.appendChild(div);
  chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: 'smooth' });
  return div;
}
