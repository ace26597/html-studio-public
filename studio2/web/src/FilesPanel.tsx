// Files: a floating list on the left; opening a file shows a viewer/editor that
// overlays part of the preview instead of adding a permanent pane.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, errorMessage } from "./api";
import { formatBytes, projectFileUrl } from "./format";
import { findPoster, mediaKind, type MediaKind } from "./mediaFiles";
import { findPreview, OFFICE_KIND_LABEL, officeKind, orderTreeItems, type OfficeKind } from "./office";
import { Icon, Spinner } from "./icons";
import { FileShown, SHOW_LABEL, shownAs } from "./FileShown";
import type { FileEntry } from "./types";
import { IconButton, useToast } from "./ui";
import { latestOnly, mayDiscard } from "./discard";

interface FilesPanelProps {
  projectId: string;
  previewUrl: string;
  filesRev: number;
  changedPaths: string[];
  running: boolean;
  /** Uploads to the workspace root and reports the result; resolves when all files are done. */
  onUpload: (files: File[]) => Promise<void>;
  downloadUrl: string;
  onClose: () => void;
  /** Whether the open file has unsaved edits, so a close from outside the panel can ask first. */
  onDirtyChange?: (dirty: boolean) => void;
  /** A file to open, asked for from elsewhere (a file card in the chat); `n` changes with each ask. */
  openRequest?: { path: string; n: number } | null;
}

interface DirNode {
  name: string;
  path: string;
  dirs: DirNode[];
  files: FileEntry[];
}

function buildTree(entries: FileEntry[]): DirNode {
  const root: DirNode = { name: "", path: "", dirs: [], files: [] };
  for (const entry of entries) {
    const parts = entry.path.split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join("/");
      let child = node.dirs.find((dir) => dir.path === path);
      if (!child) {
        child = { name: parts[i], path, dirs: [], files: [] };
        node.dirs.push(child);
      }
      node = child;
    }
    node.files.push(entry);
  }
  const sort = (node: DirNode) => {
    node.dirs.sort((a, b) => a.name.localeCompare(b.name));
    node.files.sort((a, b) => a.path.localeCompare(b.path));
    node.dirs.forEach(sort);
  };
  sort(root);
  return root;
}

type OpenFile =
  | { state: "loading"; path: string }
  /** A PowerPoint / Word / Excel / PDF file: shown through its rendered preview, when there is one. */
  | { state: "office"; path: string; kind: OfficeKind }
  | { state: "text"; path: string; saved: string; hash: string }
  /** A picture, video or sound file, shown with the browser's own viewer or player. */
  | { state: "media"; path: string; kind: MediaKind }
  | { state: "unsupported"; path: string; message: string }
  | { state: "error"; path: string; message: string };

export function FilesPanel({
  projectId,
  previewUrl,
  filesRev,
  changedPaths,
  running,
  onUpload,
  downloadUrl,
  onClose,
  onDirtyChange,
  openRequest,
}: FilesPanelProps) {
  const toast = useToast();
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [buffer, setBuffer] = useState("");
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [dragging, setDragging] = useState(false);
  /** Markdown, CSV and SVG open as what they are; this shows their source text instead. */
  const [showSource, setShowSource] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

  const dirty = openFile?.state === "text" && buffer !== openFile.saved;
  const dirtyRef = useRef(false);
  const openRef = useRef<OpenFile | null>(null);
  useEffect(() => {
    dirtyRef.current = Boolean(dirty);
    openRef.current = openFile;
  });
  const dirtyChange = useRef(onDirtyChange);
  dirtyChange.current = onDirtyChange;
  useEffect(() => {
    dirtyChange.current?.(Boolean(dirty));
  }, [dirty]);
  useEffect(() => () => dirtyChange.current?.(false), []);

  // Every way of closing the panel or the viewer asks the same question first.
  const discardOk = () => mayDiscard(Boolean(dirty), (question) => window.confirm(question));
  const close = () => {
    if (discardOk()) onClose();
  };

  const loads = useRef(latestOnly());
  const loadFile = useCallback(
    async (path: string, quiet = false) => {
      const ticket = loads.current.begin();
      if (!quiet) {
        setOpenFile({ state: "loading", path });
        setShowSource(false);
      }
      setStale(false);
      const media = mediaKind(path);
      if (media) {
        setOpenFile({ state: "media", path, kind: media });
        return;
      }
      const kind = officeKind(path);
      if (kind) {
        setOpenFile({ state: "office", path, kind });
        return;
      }
      try {
        const file = await api.getFile(projectId, path);
        // Another file was opened (or the viewer closed) while this one was on its way.
        if (!loads.current.isLatest(ticket)) return;
        setOpenFile({ state: "text", path, saved: file.content, hash: file.hash });
        setBuffer(file.content);
      } catch (error) {
        if (!loads.current.isLatest(ticket)) return;
        if (error instanceof ApiError && error.status === 415) {
          setOpenFile({ state: "unsupported", path, message: error.message });
        } else if (error instanceof ApiError && error.status === 404 && quiet) {
          setOpenFile(null);
        } else {
          setOpenFile({ state: "error", path, message: errorMessage(error) });
        }
      }
    },
    [projectId],
  );

  // (Re)load the list on open and whenever the workspace changed on disk. An
  // open, unedited file follows the disk; an edited one is flagged instead.
  useEffect(() => {
    let cancelled = false;
    api
      .listFiles(projectId)
      .then((list) => {
        if (cancelled) return;
        setFiles(list);
        setListError(null);
      })
      .catch((error) => {
        if (!cancelled) setListError(errorMessage(error));
      });
    const current = openRef.current;
    if (current?.state === "text") {
      api
        .getFile(projectId, current.path)
        .then((file) => {
          if (cancelled || openRef.current?.path !== file.path || openRef.current.state !== "text") return;
          if (file.hash === openRef.current.hash) return;
          if (dirtyRef.current) setStale(true);
          else {
            setOpenFile({ state: "text", path: file.path, saved: file.content, hash: file.hash });
            setBuffer(file.content);
          }
        })
        .catch(() => {
          /* the file may have been removed; the list shows the truth */
        });
    }
    return () => {
      cancelled = true;
    };
  }, [projectId, filesRev]);

  const tree = useMemo(() => buildTree(files ?? []), [files]);
  const changed = useMemo(() => new Set(changedPaths), [changedPaths]);

  const select = (path: string) => {
    if (openFile?.path === path) return;
    if (!discardOk()) return;
    void loadFile(path);
  };

  // A file card in the chat asked for this file: open it (asking first about unsaved edits) and
  // unfold the folders above it so its row is seen.
  const selectRef = useRef(select);
  selectRef.current = select;
  const openN = openRequest?.n;
  useEffect(() => {
    if (!openRequest) return;
    const parts = openRequest.path.split("/");
    const above = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
    setCollapsed((set) => {
      const next = new Set(set);
      for (const dir of above) {
        // `collapsed` means "toggled": a preview folder is folded unless toggled, others the other way.
        if (/\.preview$/i.test(dir)) next.add(dir);
        else next.delete(dir);
      }
      return next;
    });
    selectRef.current(openRequest.path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openN]);

  const closeViewer = () => {
    if (!discardOk()) return;
    loads.current.begin();
    setOpenFile(null);
    setStale(false);
  };

  const save = async (force = false) => {
    if (openFile?.state !== "text" || saving || running) return;
    setSaving(true);
    try {
      const result = await api.putFile(projectId, openFile.path, buffer, force ? undefined : openFile.hash);
      setOpenFile({ state: "text", path: openFile.path, saved: buffer, hash: result.hash });
      setStale(false);
      toast({ message: `Saved ${openFile.path}`, kind: "success" });
    } catch (error) {
      if (error instanceof ApiError && error.code === "stale_file") setStale(true);
      else toast({ message: errorMessage(error), kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  // Uploads are allowed during a turn (Step 2); they are committed with the turn's checkpoint.
  const upload = async (list: FileList | File[]) => {
    const picked = Array.from(list);
    if (!picked.length) return;
    setUploading(true);
    try {
      await onUpload(picked);
    } finally {
      setUploading(false);
    }
    api.listFiles(projectId).then(setFiles, (error) => setListError(errorMessage(error)));
  };

  const renderDir = (node: DirNode, depth: number) => (
    <>
      {orderTreeItems(node.dirs, node.files).map((item) => {
        if (item.type === "file") return renderFile(item.file, depth);
        const dir = item.dir;
        // Generated preview folders start folded and read quieter; `collapsed` means "toggled".
        const isCollapsed = item.preview ? !collapsed.has(dir.path) : collapsed.has(dir.path);
        return (
          <li key={`d:${dir.path}`} role="none">
            <button
              type="button"
              role="treeitem"
              aria-expanded={!isCollapsed}
              className={`file-row is-dir${item.preview ? " is-generated" : ""}`}
              title={item.preview ? "Made by the assistant to show the file above" : undefined}
              style={{ paddingLeft: 10 + depth * 14 }}
              onClick={() =>
                setCollapsed((set) => {
                  const next = new Set(set);
                  if (next.has(dir.path)) next.delete(dir.path);
                  else next.add(dir.path);
                  return next;
                })
              }
            >
              <Icon name={isCollapsed ? "chevron-right" : "chevron-down"} size={13} className="file-icon" />
              <span className="file-name">{dir.name}</span>
              {item.preview && <span className="file-tag">preview</span>}
            </button>
            {!isCollapsed && (
              <ul role="group" className="file-group">
                {renderDir(dir, depth + 1)}
              </ul>
            )}
          </li>
        );
      })}
    </>
  );

  function renderFile(file: FileEntry, depth: number) {
        const name = file.path.split("/").pop() ?? file.path;
        return (
          <li key={`f:${file.path}`} role="none">
            <button
              type="button"
              role="treeitem"
              aria-selected={openFile?.path === file.path}
              className={`file-row${openFile?.path === file.path ? " is-selected" : ""}`}
              style={{ paddingLeft: 10 + depth * 14 }}
              onClick={() => select(file.path)}
              title={`${file.path} · ${formatBytes(file.size)}`}
            >
              <Icon name="file" size={13} className="file-icon" />
              <span className="file-name">{name}</span>
              {changed.has(file.path) && (
                <span className="file-dot" role="img" aria-label="Changed in this turn" title="Changed in this turn" />
              )}
            </button>
          </li>
        );
  }

  return (
    <>
      <aside
        className={`panel panel-left${dragging ? " is-dragging" : ""}`}
        aria-label="Files"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !event.defaultPrevented) {
            event.stopPropagation();
            close();
          }
        }}
        onDragEnter={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          event.stopPropagation();
          dragDepth.current++;
          setDragging(true);
        }}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          event.stopPropagation();
        }}
        onDragLeave={(event) => {
          event.stopPropagation();
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (!dragDepth.current) setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          event.stopPropagation();
          dragDepth.current = 0;
          setDragging(false);
          void upload(event.dataTransfer.files);
        }}
      >
        <header className="panel-header">
          <h2>Files</h2>
          <span className="panel-spacer" />
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              if (event.target.files) void upload(event.target.files);
              event.target.value = "";
            }}
          />
          {uploading ? (
            <span className="icon-btn" role="status" aria-label="Uploading">
              <Spinner size={14} />
            </span>
          ) : (
            <IconButton
              icon="upload"
              label="Upload files"
              tip="Upload files"
              tipAlign="end"
              onClick={() => fileInput.current?.click()}
            />
          )}
          <a
            className="icon-btn"
            href={downloadUrl}
            download
            aria-label="Download the project as a zip"
            data-tip="Download as zip"
            data-tip-align="end"
          >
            <Icon name="download" size={16} />
          </a>
          <IconButton icon="x" label="Close files" tip="Close (Esc)" tipAlign="end" onClick={close} />
        </header>
        <div className="panel-body">
          {files === null && !listError && (
            <div className="panel-empty">
              <Spinner />
            </div>
          )}
          {listError && <div className="panel-empty is-error">{listError}</div>}
          {files && files.length === 0 && (
            <div className="panel-empty">
              <p>No files yet.</p>
              <p className="dim">Drop files here or use Upload to add them to the project.</p>
            </div>
          )}
          {files && files.length > 0 && (
            <ul role="tree" aria-label="Project files" className="file-tree">
              {renderDir(tree, 0)}
            </ul>
          )}
        </div>
        <footer className="panel-footer files-footer">
          <button
            type="button"
            className="btn btn-small"
            disabled={uploading}
            onClick={() => fileInput.current?.click()}
          >
            <Icon name="upload" size={14} /> Add files
          </button>
          <span className="files-drop-hint">or drop them here</span>
        </footer>
        {dragging && (
          <div className="drop-veil" aria-hidden="true">
            <Icon name="upload" size={20} />
            <span>Drop to add to the project</span>
          </div>
        )}
      </aside>

      {openFile && (
        <section
          className="viewer viewer-left"
          aria-label={`File ${openFile.path}`}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
              event.preventDefault();
              void save();
            } else if (event.key === "Escape" && !event.defaultPrevented) {
              event.stopPropagation();
              closeViewer();
            }
          }}
        >
          <header className="panel-header">
            <h2 className="viewer-path" title={openFile.path}>
              {openFile.path}
            </h2>
            {dirty && <span className="viewer-dirty">Edited</span>}
            <span className="panel-spacer" />
            {openFile.state === "text" && shownAs(openFile.path) && (
              <button
                type="button"
                className="btn btn-small"
                aria-pressed={showSource}
                onClick={() => setShowSource((value) => !value)}
              >
                {showSource ? SHOW_LABEL[shownAs(openFile.path)!] : "Show source"}
              </button>
            )}
            {(openFile.state === "office" || openFile.state === "media") && (
              <a
                className="btn btn-small"
                href={`${projectFileUrl(previewUrl, openFile.path)}?download=1`}
                download={openFile.path.split("/").pop()}
                target="_blank"
                rel="noreferrer"
              >
                <Icon name="download" size={14} /> Download
              </a>
            )}
            {openFile.state === "text" && (
              <button
                type="button"
                className="btn btn-small btn-primary"
                disabled={!dirty || saving || running}
                data-tip={running ? "Saving is paused while the agent works" : undefined}
                aria-description={running ? "Saving is paused while the agent works" : undefined}
                data-tip-align="end"
                onClick={() => void save()}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            )}
            <IconButton icon="x" label="Close file" tip="Close" tipAlign="end" onClick={closeViewer} />
          </header>
          {stale && (
            <div className="viewer-banner" role="alert">
              <span>This file changed on disk since you opened it.</span>
              <button type="button" className="btn btn-small" onClick={() => void loadFile(openFile.path, true)}>
                Reload
              </button>
              <button type="button" className="btn btn-small" disabled={running} onClick={() => void save(true)}>
                Keep mine
              </button>
            </div>
          )}
          <div className="viewer-body">
            {openFile.state === "loading" && (
              <div className="panel-empty">
                <Spinner />
              </div>
            )}
            {openFile.state === "text" && shownAs(openFile.path) && !showSource && (
              <FileShown path={openFile.path} text={buffer} as={shownAs(openFile.path)!} />
            )}
            {openFile.state === "text" && (!shownAs(openFile.path) || showSource) && (
              <textarea
                className="code-editor"
                aria-label={`Contents of ${openFile.path}`}
                value={buffer}
                spellCheck={false}
                wrap="off"
                onChange={(event) => setBuffer(event.target.value)}
              />
            )}
            {openFile.state === "office" && (
              <OfficeView
                path={openFile.path}
                kind={openFile.kind}
                size={files?.find((entry) => entry.path === openFile.path)?.size ?? null}
                preview={findPreview(openFile.path, files ?? [])}
                previewUrl={previewUrl}
              />
            )}
            {openFile.state === "media" && openFile.kind === "image" && (
              <div className="viewer-image">
                <img src={projectFileUrl(previewUrl, openFile.path)} alt={openFile.path} />
              </div>
            )}
            {openFile.state === "media" && openFile.kind === "video" && (
              <div className="viewer-image viewer-media">
                <video
                  key={openFile.path}
                  src={projectFileUrl(previewUrl, openFile.path)}
                  poster={(() => {
                    const poster = findPoster(openFile.path, files ?? []);
                    return poster ? projectFileUrl(previewUrl, poster) : undefined;
                  })()}
                  controls
                  playsInline
                  preload="metadata"
                  aria-label={`Video ${openFile.path}`}
                />
              </div>
            )}
            {openFile.state === "media" && openFile.kind === "audio" && (
              <div className="viewer-image viewer-media">
                <audio
                  key={openFile.path}
                  src={projectFileUrl(previewUrl, openFile.path)}
                  controls
                  preload="metadata"
                  aria-label={`Sound ${openFile.path}`}
                />
              </div>
            )}
            {openFile.state === "unsupported" && shownAs(openFile.path) === "picture" && (
              // Too large to open as text: still the picture, loaded as an image (which runs no script).
              <div className="viewer-image">
                <img src={projectFileUrl(previewUrl, openFile.path)} alt={openFile.path} />
              </div>
            )}
            {((openFile.state === "unsupported" && shownAs(openFile.path) !== "picture") || openFile.state === "error") && (
              <div className="panel-empty">
                <p>{openFile.state === "unsupported" ? "This file can’t be shown as text." : openFile.message}</p>
                <a
                  className="btn btn-small"
                  href={projectFileUrl(previewUrl, openFile.path)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open in a new tab
                </a>
              </div>
            )}
          </div>
        </section>
      )}
    </>
  );
}

/** An Office file: its rendered viewer (slides, pages or sheets), or a plain card when there is none yet. */
function OfficeView({
  path,
  kind,
  size,
  preview,
  previewUrl,
}: {
  path: string;
  kind: OfficeKind;
  size: number | null;
  preview: string | null;
  previewUrl: string;
}) {
  const name = path.split("/").pop() ?? path;
  if (preview) {
    return (
      <iframe
        className="office-frame"
        title={`Preview of ${name}`}
        src={projectFileUrl(previewUrl, preview)}
        // The viewer runs its own scripts and offers its own Download; it gets the project
        // page's origin (another port), never the studio's.
        sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox"
      />
    );
  }
  if (kind === "pdf") {
    // Browsers show PDFs with their own viewer, which does not run inside a sandboxed frame.
    return <iframe className="office-frame" title={`PDF ${name}`} src={projectFileUrl(previewUrl, path)} />;
  }
  return (
    <div className="panel-empty office-empty">
      <Icon name="file" size={22} />
      <p className="office-name">{name}</p>
      <p className="dim">
        {OFFICE_KIND_LABEL[kind]}
        {size !== null ? ` · ${formatBytes(size)}` : ""}
      </p>
      <p>No preview yet. Ask the assistant to render this file.</p>
      <a
        className="btn btn-small"
        href={`${projectFileUrl(previewUrl, path)}?download=1`}
        download={name}
        target="_blank"
        rel="noreferrer"
      >
        <Icon name="download" size={14} /> Download
      </a>
    </div>
  );
}
