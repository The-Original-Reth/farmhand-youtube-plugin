import { useEffect, useState } from "react";
import { HardDrive } from "lucide-react";
import { c } from "./theme.js";
import { monoText, panelBox, uiText } from "./styles.js";
import { errorText, getClips, getStream } from "./api.js";
import { ClipLength } from "./ClipLength.jsx";

// The plugin's settings as Farmhand's Settings page shows them, under the plugin's name (the manifest's settings_page,
// ?view=settings): where it saves (the stream folder and the YouTube clips folder, each with whether it's working) and
// how long a clip is. The folders are the shares mounted into the plugin's container, so they're changed where the
// plugin runs (its docker-compose.yml); the clip length is changed here.
export function SettingsView() {
  const [media, setMedia] = useState(null);
  const [clips, setClips] = useState(null);
  const [problem, setProblem] = useState(null);
  useEffect(() => {
    getStream().then((s) => setMedia(s.media)).catch((e) => setProblem(errorText(e)));
    getClips().then(setClips).catch((e) => setProblem(errorText(e)));
  }, []);
  if (problem) return <div style={uiText(13, c.amber, { lineHeight: 1.5 })}>Couldn't read the plugin's settings: {problem}</div>;
  if (!media || !clips) return <div style={uiText(13, c.textFaint)}>Reading the plugin's settings…</div>;

  return (
    <div data-testid="plugin-settings" style={panelBox({ padding: 16, display: "flex", flexDirection: "column", gap: 14 })}>
      <div>
        <div style={uiText(15, c.heading, { marginBottom: 4 })}>Where it saves</div>
        <div style={uiText(12, c.textFaint, { lineHeight: 1.6 })}>
          The folders given to the plugin's container where it runs: change them in its docker-compose.yml there.
        </div>
      </div>
      {/* (laid out as Farmhand's own list under its save location) */}
      <div data-testid="plugin-settings-folders" style={{ border: `1px solid ${c.border}`, borderRadius: 4, background: c.panelAlt }}>
        {[
          ["Stream folder", media.windows_path || media.path, media.error],
          ["YouTube clips", clips.folder.shownAs || clips.folder.path, clips.folder.problem],
        ].map(([what, where, trouble], i) => (
          <div key={what} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 12px", borderTop: i ? `1px solid ${c.border}` : "none", flexWrap: "wrap" }}>
            <HardDrive size={13} color={c.textFaint} style={{ flexShrink: 0 }} />
            <span style={uiText(12, c.textMuted, { width: 170, flexShrink: 0 })}>{what}</span>
            <span style={monoText(12, where ? c.heading : c.textFaint, { minWidth: 0, overflowWrap: "anywhere", flex: "1 1 200px" })}>{where || "not set"}</span>
            <span title={trouble || undefined} style={uiText(12, trouble ? c.amber : c.green, { marginLeft: "auto", flexShrink: 0 })}>
              {trouble ? "not working" : "ready"}
            </span>
          </div>
        ))}
      </div>
      {[media.error, clips.folder.problem].filter(Boolean).map((t) => (
        <div key={t} style={uiText(12, c.amber, { lineHeight: 1.5 })}>{t}</div>
      ))}
      <div style={{ borderTop: `1px solid ${c.border}`, paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={uiText(15, c.heading)}>Clips</div>
        <ClipLength clips={clips} onSaved={setClips} />
      </div>
    </div>
  );
}
