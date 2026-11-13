import { useState } from "react";
import { c } from "./theme.js";
import { fieldBox, uiText } from "./styles.js";
import { errorText, setClipLength } from "./api.js";

// How long a clip is: typed in (1 s to 5 min), kept by the plugin on Enter or leaving the box. On the Clips panel and on
// the plugin's settings (shown on Farmhand's Settings page).
export const length = (s) => (s < 60 ? `${s} s` : s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`);
// A length as typed: seconds ("45", "45 s"), minutes and seconds ("2:30", "2 min 30 s", "2m30s") or minutes ("3 min",
// "1.5 min"); null when it isn't one
const SEC = "(?:s|secs?|seconds?)";
const MIN = "(?:m|mins?|minutes?)";
const FORMS = [
  [new RegExp(String.raw`^(\d+)\s*${SEC}?$`), (m) => Number(m[1])],
  [/^(\d+):([0-5]\d)$/, (m) => Number(m[1]) * 60 + Number(m[2])],
  [new RegExp(String.raw`^(\d+(?:\.\d+)?)\s*${MIN}$`), (m) => Math.round(Number(m[1]) * 60)],
  [new RegExp(String.raw`^(\d+)\s*${MIN}\s*(\d+)\s*${SEC}?$`), (m) => Number(m[1]) * 60 + Number(m[2])],
];
export function parseLength(text) {
  const t = String(text).trim().toLowerCase().replace(/\s+/g, " ");
  for (const [form, seconds] of FORMS) {
    const m = t.match(form);
    if (m) return seconds(m);
  }
  return null;
}

// `clips`: the plugin's GET /api/clips; `onSaved(clips)`: told what it answered once a new length is kept
export function ClipLength({ clips, onSaved }) {
  const [typed, setTyped] = useState(null); // what's in the box while it's being typed in, before it's saved
  const [wrong, setWrong] = useState(null); // why what was typed isn't a length
  const shown = clips.seconds;
  const stopTyping = () => {
    setTyped(null);
    setWrong(null);
  };
  const keep = async () => {
    if (typed === null) return;
    const s = parseLength(typed);
    if (s === null || s < clips.least || s > clips.most) {
      setWrong(`Type ${clips.least} second to ${clips.most / 60} minutes, e.g. 3, 45, 90 s, 2:30 or 3 min.`);
      return;
    }
    setWrong(null);
    try {
      onSaved(await setClipLength(s));
      stopTyping();
    } catch (e) {
      setWrong(errorText(e));
    }
  };
  return (
    <>
      <label style={uiText(12, c.textMuted, { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" })}>
        Length
        <input
          data-testid="stream-clip-length"
          type="text"
          inputMode="numeric"
          value={typed ?? String(shown)}
          onChange={(e) => {
            setTyped(e.target.value);
            setWrong(null);
          }}
          onBlur={keep}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") stopTyping();
          }}
          style={{ ...fieldBox(), width: 80 }}
          aria-label="How long a clip is, in seconds"
          title="Seconds (1 to 300), or minutes and seconds like 2:30. Enter keeps it."
        />
        <span style={uiText(12, c.textFaint)}>
          seconds{typed === null && shown >= 60 ? ` (${length(shown)})` : ""} · {clips.least} s to {clips.most / 60} min
        </span>
      </label>
      {wrong && <div style={uiText(12, c.danger, { lineHeight: 1.5 })}>{wrong}</div>}
    </>
  );
}
