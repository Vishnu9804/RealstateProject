import { useRef, useState } from "react";
import { fileToPropertyImage } from "../lib/imageProcessing";
import { IconImage, IconPlus, IconX } from "./ui/Icons";

/**
 * The Add/Edit dialog's "Property photos" field — same spirit as
 * instagram_reel_url (optional, human-only, never asked of the LLM), just a
 * list instead of a single string. Click-to-browse or drag-and-drop, any
 * number of images in any format the browser can read (and even one it
 * can't — see fileToPropertyImage's fallback), reorderable by dragging a
 * thumbnail. The first photo is the cover.
 *
 * Fully controlled: PropertyFormDialog owns `images` as part of its form
 * state and passes back onAdd/onRemove/onReorder rather than this component
 * holding its own copy — so a fast double-drop can never race a stale
 * closure the way "images + [...new]" computed in here could.
 */
export default function PropertyImagesField({
  images,
  onAdd,
  onRemove,
  onReorder,
}: {
  images: string[];
  onAdd: (dataUrls: string[]) => void;
  onRemove: (index: number) => void;
  onReorder: (fromIndex: number, toIndex: number) => void;
}) {
  const [pendingCount, setPendingCount] = useState(0);
  const [isFileOver, setIsFileOver] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [removingIndex, setRemovingIndex] = useState<number | null>(null);
  const dragCounter = useRef(0);

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setPendingCount((n) => n + list.length);
    try {
      const results = await Promise.all(list.map((file) => fileToPropertyImage(file)));
      onAdd(results);
    } finally {
      setPendingCount((n) => Math.max(0, n - list.length));
    }
  }

  function handleInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    if (event.target.files?.length) addFiles(event.target.files);
    event.target.value = "";
  }

  function isFileDrag(event: React.DragEvent) {
    return event.dataTransfer.types.includes("Files");
  }

  function handleContainerDragEnter(event: React.DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragCounter.current += 1;
    setIsFileOver(true);
  }
  function handleContainerDragOver(event: React.DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
  }
  function handleContainerDragLeave(event: React.DragEvent) {
    if (!isFileDrag(event)) return;
    dragCounter.current = Math.max(0, dragCounter.current - 1);
    if (dragCounter.current === 0) setIsFileOver(false);
  }
  function handleContainerDrop(event: React.DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragCounter.current = 0;
    setIsFileOver(false);
    if (event.dataTransfer.files?.length) addFiles(event.dataTransfer.files);
  }

  function handleRemove(index: number) {
    setRemovingIndex(index);
    window.setTimeout(() => {
      onRemove(index);
      setRemovingIndex(null);
    }, 160);
  }

  function handleTileDragStart(index: number) {
    return (event: React.DragEvent) => {
      setDragIndex(index);
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(index));
    };
  }
  function handleTileDragEnter(index: number) {
    return (event: React.DragEvent) => {
      if (isFileDrag(event) || dragIndex === null) return;
      event.preventDefault();
      if (dragIndex !== index) setOverIndex(index);
    };
  }
  function handleTileDragOver(event: React.DragEvent) {
    if (isFileDrag(event) || dragIndex === null) return;
    event.preventDefault();
  }
  function handleTileDrop(index: number) {
    return (event: React.DragEvent) => {
      if (isFileDrag(event)) return;
      event.preventDefault();
      event.stopPropagation();
      if (dragIndex !== null && dragIndex !== index) onReorder(dragIndex, index);
      setDragIndex(null);
      setOverIndex(null);
    };
  }
  function handleTileDragEnd() {
    setDragIndex(null);
    setOverIndex(null);
  }

  const hasContent = images.length > 0 || pendingCount > 0;

  return (
    <div
      className="photo-field"
      onDragEnter={handleContainerDragEnter}
      onDragOver={handleContainerDragOver}
      onDragLeave={handleContainerDragLeave}
      onDrop={handleContainerDrop}
    >
      {!hasContent ? (
        <label className={`photo-drop${isFileOver ? " photo-drop--active" : ""}`}>
          <span className="photo-drop__icon">
            <IconImage size={20} />
          </span>
          <span className="photo-drop__title">Drag photos here, or click to add</span>
          <span className="photo-drop__hint">Any image format — add as many as you like</span>
          <input type="file" accept="image/*" multiple onChange={handleInputChange} />
        </label>
      ) : (
        <div className="photo-grid">
          {images.map((src, index) => (
            <div
              key={`${index}-${src.slice(0, 24)}`}
              className={[
                "photo-tile",
                dragIndex === index && "photo-tile--dragging",
                overIndex === index && dragIndex !== null && dragIndex !== index && "photo-tile--over",
                removingIndex === index && "photo-tile--removing",
              ]
                .filter(Boolean)
                .join(" ")}
              draggable
              onDragStart={handleTileDragStart(index)}
              onDragEnter={handleTileDragEnter(index)}
              onDragOver={handleTileDragOver}
              onDrop={handleTileDrop(index)}
              onDragEnd={handleTileDragEnd}
              title="Drag to reorder"
            >
              <img src={src} alt={`Property photo ${index + 1}`} draggable={false} />
              {index === 0 && <span className="photo-tile__cover">Cover</span>}
              <button
                type="button"
                className="photo-tile__remove"
                aria-label={`Remove photo ${index + 1}`}
                onClick={() => handleRemove(index)}
              >
                <IconX size={12} />
              </button>
            </div>
          ))}
          {Array.from({ length: pendingCount }).map((_, index) => (
            <div key={`pending-${index}`} className="photo-tile photo-tile--pending" aria-label="Adding photo">
              <span className="spinner" />
            </div>
          ))}
          <label className={`photo-tile photo-tile--add${isFileOver ? " photo-drop--active" : ""}`}>
            <IconPlus size={18} />
            Add
            <input type="file" accept="image/*" multiple onChange={handleInputChange} />
          </label>
        </div>
      )}
      {images.length > 1 && <p className="photo-field__hint">Drag a photo to change the order of the images.</p>}
    </div>
  );
}
