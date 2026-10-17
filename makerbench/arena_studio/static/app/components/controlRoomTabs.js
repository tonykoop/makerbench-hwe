import { html } from "../html.js";

// The Control Room screens, linked to each other from their headers (#1104).
const TABS = [
  ["review", "Morning Review"],
  ["newrun", "New Run"],
];

export function ControlRoomTabs({ current }) {
  return html`
    <nav class="review-nights" aria-label="Control Room screens">
      ${TABS.map(
        ([id, label]) => html`<a key=${id} class="review-night" href=${`#/${id}`} aria-current=${id === current ? "page" : undefined}>${label}</a>`,
      )}
    </nav>
  `;
}
