import { Radio } from "lucide-react";
import { c } from "../theme.js";
import { uiText } from "../styles.js";
import { heading, help, panel } from "./streamStyles.js";

// While Farmhand's Stream control takes this plugin's stream over, the stream sends the one designed there, and Stream
// control starts and stops it: this block is all the plugin's page shows. The plugin's own layout stays saved as it was,
// and the page comes back when Stream control is switched off or this stream is unticked in it.
// `sc`: page.streamControl { following, revision, savedAt, title, note }.
export function StreamControlPanel({ sc }) {
  const saved = sc.savedAt ? new Date(sc.savedAt) : null;
  return (
    <div data-testid="stream-controlled" style={panel()}>
      <div style={{ ...heading, display: "flex", alignItems: "center", gap: 8 }}>
        <Radio size={16} color={c.amber} /> Controlled by Stream control
      </div>
      <div style={uiText(13, c.textPrimary, { lineHeight: 1.6 })}>
        This stream sends the layout designed in Farmhand&apos;s <b>Stream control</b> (in Farmhand&apos;s menu): its grid,
        squares, pictures, music, holding screen, title and description. Change the layout there; the stream follows
        each time it&apos;s saved{sc.title ? <>, now as “{sc.title}”</> : null}.
      </div>
      <div style={help}>
        This plugin&apos;s own layout is kept as it was and comes back when Stream control is switched off, or this stream
        is unticked under its Streams. Going live and stopping are done there too. The stream key, the YouTube sign-in and
        the YouTube settings stay this plugin&apos;s: open them with the YouTube settings button in Stream control.
        {saved && !Number.isNaN(saved.getTime()) ? ` Last saved in Stream control ${saved.toLocaleString()}.` : ""}
      </div>
      {sc.note && (
        <div data-testid="stream-controlled-note" style={uiText(12, c.amber, { lineHeight: 1.5 })}>
          {sc.note}
        </div>
      )}
    </div>
  );
}
