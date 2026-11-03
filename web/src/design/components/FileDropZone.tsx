import { DropZone, FileTrigger, Text } from "react-aria-components";
import { Button } from "./Button";
import { Glyph } from "./Glyph";
import s from "./Surface.module.css";

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Drop files or choose them. Fully keyboard operable: the button opens the picker, and the drop zone
 * itself accepts paste. Accepted types are stated in words, not only enforced.
 */
export function FileDropZone({
  label, hint, accept, multiple, files, onFiles, onRemove, id, isDisabled,
}: { label: string; hint: string; accept: string[]; multiple?: boolean; files: File[]; onFiles: (f: File[]) => void; onRemove?: (i: number) => void; id?: string; isDisabled?: boolean }) {
  async function fromDrop(items: { kind: string; getFile?: () => Promise<File> }[]) {
    const got = await Promise.all(items.filter((i) => i.kind === "file" && i.getFile).map((i) => i.getFile!()));
    if (got.length) onFiles(multiple ? got : got.slice(0, 1));
  }
  return (
    <div className={s.stack}>
      <DropZone className={s.drop} aria-label={label} isDisabled={isDisabled} onDrop={(e) => fromDrop(e.items as never)}>
        <Glyph name="upload" size={24} />
        <Text slot="label" style={{ fontWeight: 700 }}>{label}</Text>
        <p className={s.dropHint}>{hint}</p>
        <FileTrigger acceptedFileTypes={accept} allowsMultiple={multiple} onSelect={(list) => list && onFiles(Array.from(list))}>
          <Button id={id} variant="secondary" icon="file" isDisabled={isDisabled}>Choose {multiple ? "files" : "a file"}</Button>
        </FileTrigger>
      </DropZone>
      {files.length > 0 && (
        <ul className={s.fileList} aria-label="Chosen files">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className={s.fileItem}>
              <span className={s.fileName}><Glyph name="file" />{f.name}</span>
              <span className={s.row}>
                <span className={s.fileSize}>{fileSize(f.size)}</span>
                {onRemove && <Button variant="quiet" size="sm" icon="close" aria-label={`Remove ${f.name}`} onPress={() => onRemove(i)} />}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
