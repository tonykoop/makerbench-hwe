import { useEffect, useImperativeHandle, useRef } from "preact/hooks";

import { html } from "../html.js";
import { offsetOfLine } from "../lib/workbench.js";

// The workbench's editor interface (#788 W4, plan §4): value, onChange,
// onSubmit (Ctrl+Enter), goToLine and language. This first slice is a plain
// <textarea>: native undo, IME, find-in-page and screen-reader support, and
// Tab leaves the field. A richer editor (CodeMirror, W8) can replace it
// behind the same props without touching the screen. While a compile runs the
// field is read-only, not disabled: a disabled control drops keyboard focus.
export function CodeEditor({ value, onChange, onSubmit, language, label, editorRef, readOnly }) {
  const textarea = useRef(null);

  useImperativeHandle(
    editorRef,
    () => ({
      goToLine(line) {
        const node = textarea.current;
        if (!node) return;
        const offset = offsetOfLine(node.value, line);
        node.focus();
        node.setSelectionRange(offset, offset);
      },
      focus() {
        textarea.current?.focus();
      },
    }),
    [],
  );

  useEffect(() => {
    // Keep the caret where it was when the value is replaced from outside.
    const node = textarea.current;
    if (node && node.value !== value) node.value = value;
  }, [value]);

  const lineCount = Math.max(1, String(value || "").split("\n").length);
  const onKeyDown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      // A read-only editor (a compile is running) swallows the shortcut.
      if (!readOnly) onSubmit?.();
    }
  };

  return html`
    <div class="code-editor" data-language=${language || "openscad"}>
      <textarea
        ref=${textarea}
        class="code-editor-text"
        aria-label=${label}
        spellcheck="false"
        autocomplete="off"
        autocorrect="off"
        autocapitalize="off"
        wrap="off"
        rows=${Math.min(40, Math.max(12, lineCount + 1))}
        readonly=${readOnly ? true : undefined}
        aria-readonly=${readOnly ? "true" : undefined}
        value=${value}
        onInput=${(event) => onChange?.(event.currentTarget.value)}
        onKeyDown=${onKeyDown}
      ></textarea>
      <p class="hint code-editor-hint">
        ${lineCount} ${lineCount === 1 ? "line" : "lines"}. Ctrl+Enter compiles. Tab moves focus.
      </p>
    </div>
  `;
}
