import { useEffect, useState } from "react";
import { c } from "./theme.js";
import { fieldBox, panelBox, primaryButton, uiText } from "./styles.js";
import { clipUrl, errorText, getClips, takeClip } from "./api.js";
import { ClipLength, length } from "./ClipLength.jsx";
import { timeZone } from "./api/client.js";

// Clips, saved as MP4s in the clips folder (the NAS's "YouTube clips"), from what's chosen in the drop-down: Stream
// (the last stretch of what went to YouTube, saved at once) or any camera Farmhand can see (the next stretch from it,
// recorded in the background: the panel says while it is, and how it ended). The length is typed in here (1 s to
// 5 min) and kept by the plugin.
const size = (bytes) => (bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`);
const STREAM = "stream";

export function ClipsPanel() {
  const [clips, setClips] = useState(null);
  const [source, setSource] = useState(STREAM);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null); // { text, tone }
  const [, tick] = useState(0); // (the seconds left of a recording count down)

  const load = () => getClips().then(setClips).catch((e) => setSaid({ text: errorText(e), tone: "bad" }));
  const recordingNow = (clips?.recording || []).length > 0;
  useEffect(() => {
    load();
    const every = setInterval(load, recordingNow ? 2000 : 15000); // (more often while a camera is being recorded)
    const clock = recordingNow ? setInterval(() => tick((n) => n + 1), 1000) : null;
    return () => {
      clearInterval(every);
      if (clock) clearInterval(clock);
    };
  }, [recordingNow]);
  if (!clips) return null;

  const shown = clips.seconds;
  const where = clips.folder.shownAs || clips.folder.path;
  const problem = clips.folder.problem;
  const stream = source === STREAM;
  const chosen = clips.sources.find((s) => s.key === source) || clips.sources[0];
  const nothing = stream && clips.held <= 0;
  const already = !stream && clips.recording.some((r) => r.source === source);
  const blocked = busy || !!problem || nothing || already;
  const take = async () => {
    setBusy(true);
    setSaid(null);
    try {
      const got = await takeClip(timeZone, source);
      if (!got.recording) setSaid({ text: `Saved “${got.name}” (${size(got.size)}) in ${where}.`, tone: "good" });
      await load();
    } catch (e) {
      setSaid({ text: errorText(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };
  const left = (r) => Math.max(0, Math.ceil(r.until - Date.now() / 1000));

  return (
    <div data-testid="stream-clips" style={panelBox({ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 })}>
      <div style={uiText(13, c.heading, { fontWeight: 600 })}>Clips</div>
      <div style={uiText(12, c.textMuted, { lineHeight: 1.5 })}>
        Save a clip as a video file, in <span style={{ color: c.textPrimary }}>{where}</span>: the last stretch of the stream as it
        went to YouTube, or the next stretch from any camera.
      </div>
      <label style={uiText(12, c.textMuted, { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" })}>
        Clip
        <select
          data-testid="stream-clip-source"
          value={chosen.key}
          onChange={(e) => {
            setSource(e.target.value);
            setSaid(null);
          }}
          style={{ ...fieldBox(), padding: "4px 6px", maxWidth: 260 }}
        >
          {clips.sources.map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
      </label>
      <ClipLength clips={clips} onSaved={setClips} />
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="stream-clip-save"
          onClick={take}
          disabled={blocked}
          style={primaryButton(blocked)}
          title={problem || (nothing ? "Go live first: a clip of the stream is taken from what has gone to YouTube"
            : already ? `${chosen.label} is being recorded already`
            : stream ? `Save the last ${length(shown)} that went to YouTube` : `Record the next ${length(shown)} from ${chosen.label}`)}
        >
          {busy ? (stream ? "Saving the clip…" : "Starting…") : stream ? `Save clip (last ${length(shown)})` : `Record clip (next ${length(shown)})`}
        </button>
        {!problem && nothing && <span style={uiText(12, c.textFaint)}>Nothing has gone to YouTube yet.</span>}
        {!problem && stream && !nothing && clips.held < shown && (
          <span style={uiText(12, c.textFaint)}>Only {length(Math.floor(clips.held))} has gone out so far: the clip is that long.</span>
        )}
      </div>
      {problem && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{problem}</div>}
      {clips.recording.map((r) => (
        <div key={r.source} data-testid="stream-clip-recording" style={uiText(12, c.amber, { lineHeight: 1.5 })}>
          Recording {r.label}: {left(r) > 0 ? `${length(left(r))} to go` : "finishing…"}
        </div>
      ))}
      {clips.ended.slice(0, 1).map((r) => (
        <div key={`${r.source}-${r.name || r.text}`} style={uiText(12, r.ok ? c.green : c.danger, { lineHeight: 1.5 })}>
          {r.label}: {r.text}
        </div>
      ))}
      {said && (
        <div style={uiText(12, said.tone === "bad" ? c.danger : c.green, { lineHeight: 1.5 })}>{said.text}</div>
      )}
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
