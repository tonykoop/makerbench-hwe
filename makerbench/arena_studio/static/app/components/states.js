import { html } from "../html.js";

export function Loading({ label }) {
  return html`<p class="state state-loading" role="status" aria-busy="true">${label}</p>`;
}

export function Empty({ title, children }) {
  return html`
    <div class="state state-empty">
      <p class="state-title">${title}</p>
      ${children}
    </div>
  `;
}

export function ErrorState({ error, onRetry }) {
  return html`
    <div class="state state-error" role="alert">
      <p class="state-title">${error?.message || "The request failed."}</p>
      ${onRetry &&
      html`<button type="button" class="button button-quiet" onClick=${onRetry}>Try again</button>`}
    </div>
  `;
}
