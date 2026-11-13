import { useEffect, useRef, useState } from "react";
import { ChevronUp, Folder, FolderPlus, HardDrive } from "lucide-react";
import { c } from "./theme.js";
import { cancelButton, fieldBox, linkButton, monoText, panelBox, primaryButton, uiText } from "./styles.js";
import {
  browseSaveLocation, errorText, forgetSaveLocation, getClips, getSaveLocation, makeSaveFolder, moveSaveFolder, setClipLength, setSaveLocation,
} from "./api.js";
import { length, parseLength } from "./ClipLength.jsx";

// The plugin's settings as Farmhand's Settings page shows them, under the plugin's name (the manifest's settings_page,
// ?view=settings), laid out as Farmhand's own: its save location on the NAS (typed as \\server\share\folder with its
// login; the plugin connects to it itself), the folders inside it (the stream folder and YouTube clips), each as people
// reach it on the network and with its own Change…, and how long a clip is.
//
// The address, login and clip length are typed here and kept with Save: inside Farmhand, the Save on the plugin's
// heading row (farmhand-plugin.js: onSave, and changed() so it lights up); opened on its own, a Save at the bottom. A
// folder's Change… is kept at once.
const fh = () => window.farmhand;
const BLANK = { address: null, username: null, password: "", length: null };

export function SettingsView() {
  const [where, setWhere] = useState(null);
  const [clips, setClips] = useState(null);
  const [problem, setProblem] = useState(null);
  const [draft, setDraft] = useState(BLANK); // what's typed here and not saved yet (null: as saved)
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState(null); // { text, tone }
  const saveNow = useRef(null); // (the latest save, for Farmhand's Save button)
  useEffect(() => {
    Promise.all([getSaveLocation(), getClips()])
      .then(([w, k]) => {
        setWhere(w);
        setClips(k);
      })
      .catch((e) => setProblem(errorText(e)));
    // Farmhand's Save, on the plugin's heading row: what it answers is shown beside the button
    return fh()?.onSave?.(() => saveNow.current());
  }, []);

  const ready = !!(where && clips);
  const address = draft.address ?? (where?.address || "");
  const username = draft.username ?? (where?.username || "");
  const typedLength = draft.length ?? String(clips?.seconds ?? "");
  const locationChanged = ready && (address.trim() !== (where.address || "") || username.trim() !== (where.username || "") || !!draft.password);
  const lengthChanged = ready && typedLength.trim() !== String(clips.seconds);
  const changed = locationChanged || lengthChanged;
  useEffect(() => {
    fh()?.changed?.(changed);
  }, [changed]);

  // Save: the save location (connected first: a wrong login or address isn't saved), then the clip length. Answers
  // { ok, text } (and shows it here when opened on its own).
  saveNow.current = async () => {
    const answer = (ok, text) => {
      setSaid({ text, tone: ok ? "good" : "bad" });
      return { ok, text };
    };
    if (!changed) return answer(true, "Nothing to save.");
    let seconds = null;
    if (lengthChanged) {
      seconds = parseLength(typedLength);
      if (seconds === null || seconds < clips.least || seconds > clips.most) {
        return answer(false, `The clip length: type ${clips.least} second to ${clips.most / 60} minutes, e.g. 3, 45, 90 s, 2:30 or 3 min.`);
      }
    }
    if (locationChanged && !address.trim()) return answer(false, "Type the save location's address first.");
    setBusy(true);
    try {
      if (locationChanged) {
        setWhere(await setSaveLocation({ address: address.trim(), username: username.trim(), password: draft.password, domain: where.domain, version: where.version }));
        setDraft((d) => ({ ...d, address: null, username: null, password: "" }));
      }
      if (lengthChanged) {
        setClips(await setClipLength(seconds));
        setDraft((d) => ({ ...d, length: null }));
      } else if (locationChanged) {
        setClips(await getClips());
      }
      return answer(true, locationChanged ? "Saved. The plugin is connected to the save location." : "Saved.");
    } catch (e) {
      return answer(false, errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (problem) return <div style={uiText(13, c.amber, { lineHeight: 1.5 })}>Couldn't read the plugin's settings: {problem}</div>;
  if (!ready) return <div style={uiText(13, c.textFaint)}>Reading the plugin's settings…</div>;

  const change = (part) => {
    setDraft((d) => ({ ...d, ...part }));
    setSaid(null);
  };
  const forget = async () => {
    if (!window.confirm("Stop using this save location? The plugin goes back to the folders its container was given. Nothing on the NAS is deleted.")) return;
    try {
      setWhere(await forgetSaveLocation());
      setClips(await getClips());
      setDraft(BLANK);
    } catch (e) {
      setSaid({ text: errorText(e), tone: "bad" });
    }
  };
  const inFarmhand = !!fh()?.inFarmhand && !!fh()?.onSave;

  return (
    <div data-testid="plugin-settings" style={panelBox({ padding: 16, display: "flex", flexDirection: "column", gap: 14 })}>
      <SaveLocation
        where={where}
        address={address}
        username={username}
        password={draft.password}
        onChange={change}
        onForget={forget}
        onFolderMoved={async (w) => {
          setWhere(w);
          setClips(await getClips());
        }}
      />
      <div style={{ borderTop: `1px solid ${c.border}`, paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={uiText(15, c.heading)}>Clips</div>
        <label style={uiText(12, c.textMuted, { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" })}>
          Length
          <input
            data-testid="settings-clip-length"
            type="text"
            inputMode="numeric"
            value={typedLength}
            onChange={(e) => change({ length: e.target.value })}
            style={{ ...fieldBox(), width: 80 }}
            aria-label="How long a clip is, in seconds"
            title="Seconds (1 to 300), or minutes and seconds like 2:30"
          />
          <span style={uiText(12, c.textFaint)}>
            seconds{!lengthChanged && clips.seconds >= 60 ? ` (${length(clips.seconds)})` : ""} · {clips.least} s to {clips.most / 60} min
          </span>
        </label>
      </div>
      {inFarmhand ? (
        said && <div style={uiText(12, said.tone === "bad" ? c.danger : c.green, { lineHeight: 1.5 })}>{said.text}</div>
      ) : (
        <div style={{ borderTop: `1px solid ${c.border}`, paddingTop: 12, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button data-testid="plugin-settings-save" onClick={() => saveNow.current()} disabled={busy || !changed} style={primaryButton(busy || !changed)}>
            {busy ? "Saving…" : "Save"}
          </button>
          <span style={uiText(12, said ? (said.tone === "bad" ? c.danger : c.green) : changed ? c.amber : c.textFaint, { lineHeight: 1.5 })}>
            {said ? said.text : changed ? "Not saved yet." : "Saved."}
          </span>
        </div>
      )}
    </div>
  );
}

const field = { ...fieldBox(), padding: "7px 10px" };
const fieldLabel = (extra) => uiText(12, c.textMuted, { display: "flex", flexDirection: "column", gap: 4, ...extra });

// The save location's address and login (kept with Save) and its folders, each with a Change… of its own
function SaveLocation({ where, address, username, password, onChange, onForget, onFolderMoved }) {
  const [choosing, setChoosing] = useState(null); // the folder being changed: its key
  const set = !!where.address;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <div style={uiText(15, c.heading, { marginBottom: 4 })}>Save location</div>
        <div style={uiText(12, c.textFaint, { lineHeight: 1.6 })}>
          The place on your NAS where the plugin keeps everything. Type its address and login and press Save: the plugin
          connects to it, and uses a folder inside it for each kind of file, making the ones that aren't there yet.
        </div>
      </div>
      <label style={fieldLabel()}>
        NAS address
        <input data-testid="save-location-address" value={address} onChange={(e) => onChange({ address: e.target.value })}
          placeholder="\\192.168.10.240\3d_Printing_Elves\Farmhand" style={field} />
      </label>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <label style={fieldLabel({ flex: "1 1 200px" })}>
          Username (blank for guest)
          <input value={username} onChange={(e) => onChange({ username: e.target.value })} autoComplete="off" style={field} />
        </label>
        <label style={fieldLabel({ flex: "1 1 200px" })}>
          Password
          <input type="password" value={password} onChange={(e) => onChange({ password: e.target.value })} autoComplete="new-password"
            placeholder={where.hasPassword ? "kept (type to change)" : ""} style={field} />
        </label>
      </div>
      {!where.capability.ok && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{where.capability.message}</div>}
      {set && (
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span style={uiText(12, where.mounted ? c.green : c.amber)}>{where.mounted ? "Connected" : "Not connected"}</span>
          <button onClick={onForget} style={linkButton(12)}>Stop using it</button>
        </div>
      )}
      {set && !where.mounted && where.problem && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{where.problem}</div>}

      {/* (laid out as Farmhand's own list under its save location) */}
      <div data-testid="plugin-settings-folders" style={{ border: `1px solid ${c.border}`, borderRadius: 4, background: c.panelAlt }}>
        {where.folders.map((f, i) => (
          <div key={f.key} style={{ borderTop: i ? `1px solid ${c.border}` : "none" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 12px", flexWrap: "wrap" }}>
              <HardDrive size={13} color={c.textFaint} style={{ flexShrink: 0 }} />
              <span style={uiText(12, c.textMuted, { width: 170, flexShrink: 0 })}>{f.label}</span>
              <span style={monoText(12, c.heading, { minWidth: 0, overflowWrap: "anywhere", flex: "1 1 200px" })}>{f.path}</span>
              {f.problem && <span title={f.problem} style={uiText(12, c.amber, { flexShrink: 0 })}>not working</span>}
              {set && (
                <button
                  data-testid={`save-location-change-${f.key}`}
                  onClick={() => setChoosing(choosing === f.key ? null : f.key)}
                  disabled={!where.mounted}
                  title={where.mounted ? `Choose another folder for the ${f.label.toLowerCase()}` : "Connect the save location first"}
                  style={{ ...cancelButton(!where.mounted), padding: "3px 10px", fontSize: 12, marginLeft: "auto", flexShrink: 0 }}
                >
                  Change…
                </button>
              )}
            </div>
            {choosing === f.key && (
              <FolderChooser
                start={f.relative ? f.relative.split("/").slice(0, -1).join("/") : ""}
                label={f.label}
                onUse={async (path) => {
                  await onFolderMoved(await moveSaveFolder(f.key, path));
                  setChoosing(null);
                }}
                onClose={() => setChoosing(null)}
              />
            )}
          </div>
        ))}
      </div>
      {!set && (
        <div style={uiText(12, c.textFaint, { lineHeight: 1.6 })}>
          Until a save location is set, the plugin uses the folders its container was given where it runs.
        </div>
      )}
    </div>
  );
}

// Choosing a folder inside the save location, in place under its row: the folders in a folder (click one to go in), Up,
// a new folder, and Use this folder.
function FolderChooser({ start, label, onUse, onClose }) {
  const [here, setHere] = useState(null); // { path, shown_as, folders }
  const [name, setName] = useState("");
  const [problem, setProblem] = useState(null);
  const go = (path) => browseSaveLocation(path).then(setHere, (e) => setProblem(errorText(e)));
  useEffect(() => {
    go(start);
  }, []);
  if (!here) return <div style={uiText(12, problem ? c.amber : c.textFaint, { padding: "8px 12px" })}>{problem || "Reading the folders…"}</div>;
  const up = here.path.split("/").slice(0, -1).join("/");
  const make = async () => {
    try {
      setHere(await makeSaveFolder(here.path, name));
      setName("");
      setProblem(null);
    } catch (e) {
      setProblem(errorText(e));
    }
  };
  const use = async () => {
    try {
      await onUse(here.path);
    } catch (e) {
      setProblem(errorText(e));
    }
  };
  const row = { ...uiText(12, c.textPrimary), display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", padding: "5px 10px", background: "none", border: "none", cursor: "pointer" };
  return (
    <div data-testid="save-folder-chooser" style={{ padding: "8px 12px 12px 35px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={uiText(12, c.textMuted)}>
        The folder for the {label.toLowerCase()}: <span style={monoText(12, c.heading)}>{here.shown_as}</span>
      </div>
      <div style={{ border: `1px solid ${c.border}`, borderRadius: 4, background: c.panel, maxHeight: 180, overflowY: "auto" }}>
        {here.path && (
          <button onClick={() => go(up)} style={{ ...row, color: c.textMuted }}>
            <ChevronUp size={13} /> Up
          </button>
        )}
        {here.folders.map((f) => (
          <button key={f} onClick={() => go(here.path ? `${here.path}/${f}` : f)} style={row}>
            <Folder size={13} color={c.textFaint} /> {f}
          </button>
        ))}
        {!here.folders.length && <div style={uiText(12, c.textFaint, { padding: "6px 10px" })}>No folders in here.</div>}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New folder" style={{ ...fieldBox(), padding: "5px 8px", width: 160 }} />
        <button onClick={make} disabled={!name.trim()} style={{ ...cancelButton(!name.trim()), display: "flex", alignItems: "center", gap: 6 }}>
          <FolderPlus size={13} /> Make it
        </button>
        <span style={{ flex: 1 }} />
        <button onClick={onClose} style={cancelButton(false)}>Cancel</button>
        <button data-testid="save-folder-use" onClick={use} disabled={!here.path} style={primaryButton(!here.path)}
          title={here.path ? undefined : "Choose a folder inside the save location"}>
          Use this folder
        </button>
      </div>
      {problem && <div style={uiText(12, c.amber, { lineHeight: 1.5 })}>{problem}</div>}
    </div>
  );
}
