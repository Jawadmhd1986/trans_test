// Fix for mobile viewport height
window.addEventListener('load', () => {
  let vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  let vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

// Main chatbot logic
document.addEventListener('DOMContentLoaded', () => {
  const chatBox    = document.getElementById('chat-box');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');
  const inputEl    = document.getElementById('chat-input');
  const msgsEl     = document.getElementById('chat-messages');

  chatToggle.addEventListener('click', () => chatBox.classList.toggle('open'));
  chatClose .addEventListener('click', () => chatBox.classList.remove('open'));
  sendBtn   .addEventListener('click', sendMessage);
  inputEl   .addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;
    appendMessage('user', text);
    inputEl.value = '';

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      const data = await res.json();
      const reply = data.reply ?? 'Sorry, something went wrong.';
      appendMessage('bot', reply, true); // typewriter with links
    } catch {
      appendMessage('bot', 'Sorry, something went wrong.');
    }
  }

  // --- sanitizer: escape everything, then allow only <a> tags; turn \n into <br> ---
  function renderWithLinksAndBreaks(text) {
    let out = String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    out = out
      .replace(/&lt;a\b([\s\S]*?)&gt;/gi, '<a$1>')
      .replace(/&lt;\/a&gt;/gi, '</a>');
    out = out.replace(/\n/g, '<br>');
    return out;
  }

  // --- HTML-safe typewriter: types text chars, injects tags instantly (so links stay intact) ---
  function typeHTML(html, el, speed = 15) {
    const parts = html.split(/(<[^>]+>)/g).filter(Boolean); // tags/text
    let pIndex = 0, cIndex = 0;

    function step() {
      if (pIndex >= parts.length) return;

      const part = parts[pIndex];

      if (part.startsWith('<')) {
        // append full tag immediately
        el.innerHTML += part;
        pIndex++;
        cIndex = 0;
        // keep scrolling
        msgsEl.scrollTop = msgsEl.scrollHeight;
        setTimeout(step, speed);
      } else {
        // type text content character-by-character
        if (cIndex <= part.length) {
          // build current typed HTML: existing + current text slice
          const typed = part.slice(0, cIndex);
          // replace last text chunk only by rebuilding innerHTML up to now
          // easier: append progressively
          // remove previously appended typed piece for this text part by using a span
          if (!el.lastChild || el.lastChild.nodeName !== 'SPAN' || !el.lastChild.classList.contains('tw')) {
            const span = document.createElement('span');
            span.className = 'tw';
            el.appendChild(span);
          }
          el.lastChild.textContent = typed;
          msgsEl.scrollTop = msgsEl.scrollHeight;
          cIndex++;
          setTimeout(step, speed);
        } else {
          // finish this text part: keep as plain text node (replace span with text)
          const span = el.lastChild;
          if (span && span.classList && span.classList.contains('tw')) {
            const txt = document.createTextNode(span.textContent);
            el.replaceChild(txt, span);
          }
          pIndex++;
          cIndex = 0;
          setTimeout(step, speed);
        }
      }
    }
    step();
  }

  // Append message bubble
  function appendMessage(sender, text, typewriter = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `message ${sender}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    wrapper.appendChild(bubble);
    msgsEl.appendChild(wrapper);
    msgsEl.scrollTop = msgsEl.scrollHeight;

    if (sender === 'bot' && typewriter) {
      const safeHTML = renderWithLinksAndBreaks(text);
      // start blank and type it out
      bubble.innerHTML = '';
      typeHTML(safeHTML, bubble, 15);
      return;
    }

    // user or non-typewriter bot
    bubble.textContent = text; // preserves \n for user; CSS handles it
  }
});
