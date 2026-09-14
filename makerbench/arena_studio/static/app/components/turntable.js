import { useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";

// Pre-rendered azimuth frames swapped in an <img>: pure DOM, zero WebGL, so it
// turns in any browser. setInterval, not requestAnimationFrame, which never
// fires in headless GPU-less Chromium (#722).
const FRAME_MS = 120;
const DRAG_PX_PER_FRAME = 14;

function prefersReducedMotion() {
  return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function Turntable({ frames, still, label }) {
  const hasFrames = Array.isArray(frames) && frames.length > 1;
  const frameKey = hasFrames ? frames.join("|") : "";
  const [index, setIndex] = useState(0);
  const [loaded, setLoaded] = useState(0);
  const [playing, setPlaying] = useState(() => !prefersReducedMotion());
  const [held, setHeld] = useState(false);
  const drag = useRef(null);

  useEffect(() => {
    setIndex(0);
    setLoaded(0);
    if (!hasFrames) return undefined;
    let count = 0;
    const images = frames.map((src) => {
      const image = new Image();
      image.onload = image.onerror = () => {
        count += 1;
        setLoaded(count);
      };
      image.src = src;
      return image;
    });
    return () => images.forEach((image) => (image.onload = image.onerror = null));
  }, [frameKey]);

  useEffect(() => {
    if (!hasFrames || !playing || held) return undefined;
    const timer = setInterval(() => setIndex((i) => (i + 1) % frames.length), FRAME_MS);
    return () => clearInterval(timer);
  }, [frameKey, playing, held]);

  if (!hasFrames) {
    if (!still) return html`<p class="plate-empty">No render for this candidate.</p>`;
    return html`<img class="plate-image" src=${still} alt=${`${label}, single render`} />`;
  }

  const count = frames.length;
  const step = (delta) => setIndex((i) => (i + delta + count) % count);

  const onKeyDown = (event) => {
    if (event.key === "ArrowLeft" || event.key === ",") {
      step(-1);
    } else if (event.key === "ArrowRight" || event.key === ".") {
      step(1);
    } else if (event.key === " ") {
      setPlaying((value) => !value);
    } else {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  };

  const onPointerDown = (event) => {
    drag.current = { x: event.clientX, start: index };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setHeld(true);
  };
  const onPointerMove = (event) => {
    if (!drag.current) return;
    const delta = Math.round((event.clientX - drag.current.x) / DRAG_PX_PER_FRAME);
    setIndex((((drag.current.start + delta) % count) + count) % count);
  };
  const onPointerUp = () => {
    drag.current = null;
    setHeld(false);
  };

  return html`
    <div
      class="turntable"
      tabindex="0"
      role="group"
      aria-roledescription="turntable"
      aria-label=${`${label}. Left and right arrows turn it, space ${playing ? "pauses" : "resumes"} rotation.`}
      data-frame=${index}
      onKeyDown=${onKeyDown}
      onPointerDown=${onPointerDown}
      onPointerMove=${onPointerMove}
      onPointerUp=${onPointerUp}
      onPointerCancel=${onPointerUp}
      onMouseEnter=${() => setHeld(true)}
      onMouseLeave=${() => !drag.current && setHeld(false)}
      onFocus=${() => setHeld(true)}
      onBlur=${() => setHeld(false)}
    >
      <img class="plate-image" src=${frames[index]} alt="" draggable="false" />
      ${loaded < count &&
      html`<span class="frame-progress">Loading angles ${loaded} of ${count}</span>`}
    </div>
  `;
}
