/**
 * education.js
 * Shows approved education material on the mother's dashboard (task 10.2).
 * The server decides what a mother may see: approved items for the chosen stage only.
 */

'use strict';

function _educationMessage(text) {
  const p = document.createElement('p');
  p.className = 'education-empty';
  p.textContent = text;
  return p;
}

async function loadEducation(stage, button) {
  const list = document.getElementById('educationList');
  if (!list) return;

  if (button) {
    document.querySelectorAll('.education-stage-btn').forEach(btn => {
      const selected = btn === button;
      btn.classList.toggle('active', selected);
      btn.setAttribute('aria-pressed', String(selected));
    });
  }

  list.replaceChildren(_educationMessage('Loading information…'));
  try {
    const response = await fetch('/api/education?stage=' + encodeURIComponent(stage), { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load information.');
    const { items } = await response.json();
    if (!items.length) {
      list.replaceChildren(_educationMessage('No information for this stage yet.'));
      return;
    }
    list.replaceChildren(...items.map(item => {
      const article = document.createElement('article');
      article.className = 'education-item';
      const heading = document.createElement('h3');
      heading.textContent = item.title;
      const body = document.createElement('p');
      body.textContent = item.body;
      const approved = document.createElement('small');
      approved.textContent = 'Approved by ' + item.approvedBy;
      article.append(heading, body, approved);
      return article;
    }));
  } catch (error) {
    list.replaceChildren(_educationMessage("Couldn't load information. Check that the server is running."));
  }
}

window.loadEducation = loadEducation;
