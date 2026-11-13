import { useEffect, useRef, useState } from "react";
import { c } from "./theme.js";
import { panelBox, primaryButton, uiText } from "./styles.js";
import { clipUrl, errorText, getClips, setClipLength, takeClip } from "./api.js";
import { timeZone } from "./api/client.js";

// Clips: the last 5 seconds to 5 minutes of what went to YouTube, saved as an MP4 in the clips folder (the NAS's
// "YouTube clips", beside the stream folder) at the press of a button. The length is set here and kept by the plugin.
const length = (s) => (s < 60 ? `${s} s` : s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`);
const size = (bytes) => (bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`);
const STEPS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 300]; // (the slider's stops: 5 s to 5 min)

export function ClipsPanel() {
  const [clips, setClips] = useState(null);
  const [seconds, setSeconds] = useState(null); // while the slider is moved, before it's saved
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null); // { text, tone }
  const saving = useRef(null);

  const load = () => getClips().then(setClips).catch((e) => setSaid({ text: errorText(e), tone: "bad" }));
  useEffect(() => {
    load();
    const every = setInterval(load, 15000);
    return () => clearInterval(every);
  }, []);
  if (!clips) return null;

  const shown = seconds ?? clips.seconds;
  const step = Math.max(0, STEPS.findIndex((s) => s >= shown));
  const choose = (s) => {
    setSeconds(s);
    clearTimeout(saving.current);
    saving.current = setTimeout(() => {
      setClipLength(s)
        .then((r) => {
          setClips(r);
          setSeconds(null);
        })
        .catch((e) => setSaid({ text: errorText(e), tone: "bad" }));
    }, 400);
  };
  const take = async () => {
    setBusy(true);
    setSaid(null);
    try {
      const got = await takeClip(timeZone);
      setSaid({ text: `Saved “${got.name}” (${size(got.size)}) in ${clips.folder.shownAs || clips.folder.path}.`, tone: "good" });
      await load();
    } catch (e) {
      setSaid({ text: errorText(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };
  const problem = clips.folder.problem;
  const nothing = clips.held <= 0;

  return (
    <div data-testid="stream-clips" style={panelBox({ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 })}>
      <div style={uiText(13, c.heading, { fontWeight: 600 })}>Clips</div>
      <div style={uiText(12, c.textMuted, { lineHeight: 1.5 })}>
        Save the last stretch of what went to YouTube as a video file, in{" "}
        <span style={{ color: c.textPrimary }}>{clips.folder.shownAs || clips.folder.path}</span>.
      </div>
      <label style={uiText(12, c.textMuted, { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" })}>
        Length
        <input
          data-testid="stream-clip-length"
          type="range"
          min={0}
          max={STEPS.length - 1}
          step={1}
          value={step}
          onChange={(e) => choose(STEPS[Number(e.target.value)])}
          style={{ flex: "1 1 160px", accentColor: c.amber }}
          aria-label="How long a clip is"
        />
        <span style={uiText(12, c.textPrimary, { minWidth: 70 })}>{length(shown)}</span>
      </label>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="stream-clip-save"
          onClick={take}
          disabled={busy || !!problem || nothing}
          style={primaryButton(busy || !!problem || nothing)}
          title={problem || (nothing ? "Go live first: a clip is taken from what has gone to YouTube" : `Save the last ${length(shown)} that went to YouTube`)}
        >
          {busy ? "Saving the clip…" : `Save clip (last ${length(shown)})`}
        </button>
        {!problem && nothing && <span style={uiText(12, c.textFaint)}>Nothing has gone to YouTube yet.</span>}
        {!problem && !nothing && clips.held < shown && (
          <span style={uiText(12, c.textFaint)}>Only {length(Math.floor(clips.held))} has gone out so far: the clip is that long.</span>
        )}
      </div>
      {problem && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{problem}</div>}
      {said && <div style={uiText(12, said.tone === "bad" ? c.danger : c.green, { lineHeight: 1.5 })}>{said.text}</div>}
      {clips.clips.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={uiText(11, c.textFaint)}>Latest clips</div>
          {clips.clips.map((x) => (
            <a key={x.name} href={clipUrl(x.name)} download={x.name} data-testid="stream-clip" style={uiText(12, c.fileName, { textDecoration: "none" })}>
              {x.name} <span style={{ color: c.textFaint }}>· {size(x.size)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
