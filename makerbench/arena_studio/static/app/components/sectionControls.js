import { html } from "../html.js";
import { clampOffset, SECTION_AXES, sectionActive } from "../lib/section.js";

// Section-plane controls for the 3D orbit viewer (#973). Form controls are
// typing targets, so the vote stage's single-key shortcuts leave them alone.
export function SectionControls({ section, onChange, idPrefix = "section" }) {
  const active = sectionActive(section);
  const update = (patch) => onChange({ ...section, ...patch });
  return html`
    <fieldset class="section-controls" aria-describedby=${`${idPrefix}-note`}>
      <legend>Section</legend>
      <label class="section-field">
        <span>Cut along</span>
        <select
          name=${`${idPrefix}-axis`}
          value=${section.axis}
          onChange=${(event) => update({ axis: event.currentTarget.value })}
        >
          <option value="">Off</option>
          ${SECTION_AXES.map((axis) => html`<option key=${axis} value=${axis}>${axis.toUpperCase()} axis</option>`)}
        </select>
      </label>
      <label class="section-field section-offset">
        <span>Position</span>
        <input
          type="range"
          name=${`${idPrefix}-offset`}
          min="0"
          max="100"
          step="1"
          value=${section.offset}
          disabled=${!active}
          aria-valuetext=${`${section.offset}% along ${section.axis ? section.axis.toUpperCase() : "the axis"}`}
          onInput=${(event) => update({ offset: clampOffset(event.currentTarget.value) })}
        />
        <output>${section.offset}%</output>
      </label>
      <label class="section-field section-flip">
        <input
          type="checkbox"
          name=${`${idPrefix}-flip`}
          checked=${section.flip}
          disabled=${!active}
          onChange=${(event) => update({ flip: event.currentTarget.checked })}
        />
        <span>Keep the other side</span>
      </label>
      <p id=${`${idPrefix}-note`} class="section-note">
        ${active
          ? `Cutting each model at ${section.offset}% of its ${section.axis.toUpperCase()} extent; walls and bores show in the cut.`
          : "Choose an axis to cut the model open."}
      </p>
    </fieldset>
  `;
}
