// Keyboard voting (#703): one pure mapping from a keydown to a stage action, so
// the stage component and the Node tests share exactly the same rules.

export const DEFECT_FLAGS = [
  { id: "missing_critical_components", label: "Missing critical parts" },
  { id: "misaligned_assembly", label: "Misaligned assembly" },
  { id: "wrong_proportions", label: "Wrong proportions" },
];

export const DISPOSITION_FLAGS = [
  { id: "save_for_later", label: "Save for later" },
  { id: "delete_immediately", label: "Delete immediately" },
];

export const WINNER_TEXT = { left: "Candidate A", right: "Candidate B", draw: "a draw" };

const TEXT_INPUTS = new Set(["INPUT", "SELECT", "TEXTAREA"]);
const TOGGLE_INPUT_TYPES = new Set(["checkbox", "radio", "button", "submit"]);

export function isTypingTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  if (!TEXT_INPUTS.has(target.tagName)) return false;
  return !(target.tagName === "INPUT" && TOGGLE_INPUT_TYPES.has(target.type));
}

export function voteActionForKey(event) {
  if (!event || event.defaultPrevented) return null;
  if (event.altKey || event.ctrlKey || event.metaKey) return null;
  if (isTypingTarget(event.target)) return null;

  const digit = /^Digit([1-3])$/.exec(event.code || "");
  if (digit) {
    return {
      type: "flag",
      side: event.shiftKey ? "right" : "left",
      flag: DEFECT_FLAGS[Number(digit[1]) - 1].id,
    };
  }
  switch (String(event.key || "").toLowerCase()) {
    case "a":
    case "l":
      return { type: "vote", winner: "left" };
    case "b":
    case "r":
      return { type: "vote", winner: "right" };
    case "t":
    case "d":
      return { type: "vote", winner: "draw" };
    case "s":
      return { type: "skip" };
    case "u":
      return { type: "undo" };
    case "v":
      return { type: "toggle3d" };
    case "?":
      return { type: "help" };
    default:
      return null;
  }
}

export function emptyFlags() {
  return { left: [], right: [] };
}

export function toggleFlag(flags, side, flag) {
  const current = new Set(flags[side] || []);
  if (current.has(flag)) current.delete(flag);
  else current.add(flag);
  return { ...flags, [side]: [...current] };
}

// Shape the API expects (VotePayload.flags): only sides with flags, sorted.
export function flagsPayload(flags) {
  const payload = {};
  for (const side of ["left", "right"]) {
    if (flags[side]?.length) payload[side] = [...flags[side]].sort();
  }
  return Object.keys(payload).length ? payload : null;
}
