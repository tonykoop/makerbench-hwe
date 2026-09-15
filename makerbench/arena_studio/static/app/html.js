import { h } from "preact";
import htm from "htm";

// htm tagged templates set text and attributes through the DOM, never by
// parsing strings as HTML, so server-provided values render as inert text.
export const html = htm.bind(h);
