import { useEffect, useState } from "react";
import { c } from "./theme.js";
import { panelBox, primaryButton, uiText } from "./styles.js";
import { clipUrl, errorText, getClips, takeClip } from "./api.js";
import { ClipLength, length } from "./ClipLength.jsx";
import { timeZone } from "./api/client.js";

// Clips: the last 1 second to 5 minutes of what went to YouTube, saved as an MP4 in the clips folder (the NAS's
// "YouTube clips", beside the stream folder) at the press of a button. The length is typed in here (1 s to 5 min)
// and kept by the plugin.
const size = (bytes) => (bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`);
export function ClipsPanel() {
  const [clips, setClips] = useState(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null); // { text, tone }

  const load = () => getClips().then(setClips).catch((e) => setSaid({ text: errorText(e), tone: "bad" }));
  useEffect(() => {
    load();
    const every = setInterval(load, 15000);
    return () => clearInterval(every);
  }, []);
  if (!clips) return null;

  const shown = clips.seconds;
  const where = clips.folder.shownAs || clips.folder.path;
  const problem = clips.folder.problem;
  const nothing = clips.held <= 0;
  const blocked = busy || !!problem || nothing;
  const take = async () => {
    setBusy(true);
    setSaid(null);
    try {
      const got = await takeClip(timeZone);
      setSaid({ text: `Saved “${got.name}” (${size(got.size)}) in ${where}.`, tone: "good" });
      await load();
    } catch (e) {
      setSaid({ text: errorText(e), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="stream-clips" style={panelBox({ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 })}>
      <div style={uiText(13, c.heading, { fontWeight: 600 })}>Clips</div>
      <div style={uiText(12, c.textMuted, { lineHeight: 1.5 })}>
        Save the last stretch of what went to YouTube as a video file, in{" "}
        <span style={{ color: c.textPrimary }}>{where}</span>.
      </div>
      <ClipLength clips={clips} onSaved={setClips} />
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="stream-clip-save"
          onClick={take}
          disabled={blocked}
          style={primaryButton(blocked)}
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
