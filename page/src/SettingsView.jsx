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
      <div style={uiText(15, c.heading)}>Where it saves</div>
      <Folder
        title="Stream folder"
        blurb="The stream's pictures, slideshows and music."
        where={media.windows_path || media.path}
        problem={media.error}
      />
      <Folder
        title="YouTube clips"
        blurb="Where Save clip puts its videos."
        where={clips.folder.shownAs || clips.folder.path}
        problem={clips.folder.problem}
      />
      <div style={uiText(12, c.textFaint, { lineHeight: 1.6 })}>
        These are the folders given to the plugin's container where it runs: change them in its docker-compose.yml there.
      </div>
      <div style={{ borderTop: `1px solid ${c.border}`, paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={uiText(15, c.heading)}>Clips</div>
        <ClipLength clips={clips} onSaved={setClips} />
      </div>
    </div>
  );
}

function Folder({ title, blurb, where, problem }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span style={uiText(13, c.textPrimary, { fontWeight: 600 })}>{title}</span>
        <span style={uiText(12, problem ? c.amber : c.green)}>{problem ? "not working" : "ready"}</span>
        <span style={uiText(12, c.textFaint)}>{blurb}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", border: `1px solid ${c.border}`, borderRadius: 4, background: c.panelAlt }}>
        <HardDrive size={15} color={c.textFaint} />
        <span style={monoText(12, c.heading, { wordBreak: "break-all" })}>{where}</span>
      </div>
      {problem && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{problem}</div>}
    </div>
  );
}
