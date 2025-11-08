const chatBox = document.querySelector('.chat-box');
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = chatInput.value;
    if (!message) return;

    appendMessage('user', message);
    chatInput.value = '';

    const response = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message})
    });

    const data = await response.json();
    // Display clarifying questions
    data.questions.forEach(q => appendMessage('bot', q));
    // Display retrieved cases
    data.cases.forEach(c => {
        appendMessage('bot', `${c.title} (${c.citation}) - ${c.relevance} [PDF: ${c.pdf_link}]`);
    });
});

function appendMessage(sender, text) {
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message', sender);
    msgDiv.innerHTML = text;
    chatBox.appendChild(msgDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}
