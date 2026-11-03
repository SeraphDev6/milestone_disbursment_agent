/**
 * AG Grid, dressed as a ledger. The theme is built from our CSS custom properties, so it follows the
 * Paper / Carbon / Contrast themes with no JavaScript theme switch.
 *
 * Accessibility: AG Grid renders an ARIA grid with arrow-key navigation. We add a visible caption that
 * labels it, a one-line summary for screen readers, and Enter-to-follow for links inside cells.
 */
import { useCallback, useId, useMemo, useRef, type ReactNode } from "react";
import {
  CellStyleModule,
  ClientSideRowModelModule,
  ModuleRegistry,
  ValidationModule,
  themeQuartz,
  type CellKeyDownEvent,
  type ColDef,
  type GridReadyEvent,
  type GetRowIdParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import s from "./Surface.module.css";

// Only what the ledger grids use (sorting, resizing and overlays are core). ValidationModule names any
// missing module in the console during development; e2e tests fail on console errors.
ModuleRegistry.registerModules([ClientSideRowModelModule, CellStyleModule, ...(import.meta.env.DEV ? [ValidationModule] : [])]);

export const ledgerTheme = themeQuartz.withParams({
  backgroundColor: "var(--surface-sheet)",
  foregroundColor: "var(--text-primary)",
  chromeBackgroundColor: "var(--surface-sunken)",
  headerTextColor: "var(--text-primary)",
  borderColor: "var(--rule-hairline)",
  accentColor: "var(--focus-ring)",
  oddRowBackgroundColor: "var(--surface-sheet)",
  rowHoverColor: "var(--surface-sunken)",
  selectedRowBackgroundColor: "var(--highlight)",
  rangeSelectionBorderColor: "var(--focus-ring)",
  fontFamily: "var(--font-ui)",
  cellFontFamily: "var(--font-ui)",
  fontSize: 15,
  dataFontSize: 15,
  headerFontWeight: 700,
  spacing: 7,
  rowHeight: 44,
  headerHeight: 44,
  iconSize: 14,
  borderRadius: 2,
  wrapperBorderRadius: 2,
  wrapperBorder: { color: "var(--rule-strong)", width: 1, style: "solid" },
  headerRowBorder: { color: "var(--rule-strong)", width: 1, style: "solid" },
  rowBorder: { color: "var(--rule-hairline)", width: 1, style: "solid" },
  columnBorder: false,
  focusShadow: "inset 0 0 0 2px var(--focus-ring)",
  inputFocusShadow: "var(--focus-shadow)",
  browserColorScheme: "inherit",
});

type Props<T> = {
  caption: ReactNode;
  /** Read by screen readers before the grid, e.g. "3 agreements, 1 needs attention." */
  summary: string;
  rows: T[] | undefined;
  columns: ColDef<T>[];
  rowId: (row: T) => string;
  /** Empty state text inside the grid. */
  emptyText?: string;
  loading?: boolean;
  height?: number | "auto";
  actions?: ReactNode;
  onRowActivate?: (row: T) => void;
  csvName?: string;
};

export function DataGrid<T>({ caption, summary, rows, columns, rowId, emptyText = "Nothing here yet.", loading, height = "auto", actions, onRowActivate }: Props<T>) {
  const captionId = useId();
  const summaryId = useId();
  const ref = useRef<AgGridReact<T>>(null);

  const defaultColDef = useMemo<ColDef<T>>(() => ({ sortable: true, resizable: true, filter: false, suppressHeaderMenuButton: true, minWidth: 90 }), []);

  const onGridReady = useCallback(
    (e: GridReadyEvent<T>) => {
      // Name the grid by its visible caption (AG Grid has no prop for this).
      const root = (e.api as unknown as { getGui?: () => HTMLElement }).getGui?.() ?? null;
      const grid = (root ?? document).querySelector?.(`[data-grid-for="${captionId}"] [role="grid"], [data-grid-for="${captionId}"] [role="treegrid"]`);
      grid?.setAttribute("aria-labelledby", captionId);
      grid?.setAttribute("aria-describedby", summaryId);
    },
    [captionId, summaryId],
  );

  const onCellKeyDown = useCallback(
    (e: CellKeyDownEvent<T>) => {
      const ev = e.event as KeyboardEvent | null;
      if (!ev || ev.key !== "Enter") return;
      const cell = (ev.target as HTMLElement).closest(".ag-cell");
      const link = cell?.querySelector<HTMLElement>("a[href], button");
      if (link) {
        ev.preventDefault();
        link.click();
      } else if (onRowActivate && e.data) {
        onRowActivate(e.data);
      }
    },
    [onRowActivate],
  );

  return (
    <section className={s.stack} aria-labelledby={captionId} style={{ gap: "var(--space-2)" }}>
      <div className={s.row} style={{ justifyContent: "space-between" }}>
        <h3 id={captionId} style={{ margin: 0, fontSize: "var(--text-lg)" }}>{caption}</h3>
        {actions && <div className={s.row}>{actions}</div>}
      </div>
      <p id={summaryId} className="sr-only">{summary}</p>
      <div data-grid-for={captionId} style={{ width: "100%", minWidth: 0, ...(height === "auto" ? {} : { height }) }}>
        <AgGridReact<T>
          ref={ref}
          theme={ledgerTheme}
          rowData={rows}
          columnDefs={columns}
          defaultColDef={defaultColDef}
          getRowId={(p: GetRowIdParams<T>) => rowId(p.data)}
          domLayout={height === "auto" ? "autoHeight" : "normal"}
          loading={loading}
          overlayNoRowsTemplate={`<span style="padding:1rem;color:var(--text-secondary)">${emptyText.replace(/</g, "&lt;")}</span>`}
          ensureDomOrder
          suppressColumnVirtualisation
          enableCellTextSelection
          onGridReady={onGridReady}
          onCellKeyDown={onCellKeyDown}
          onRowDoubleClicked={(e) => e.data && onRowActivate?.(e.data)}
          animateRows={false}
          suppressDragLeaveHidesColumns
        />
      </div>
    </section>
  );
}

export function exportCsv(filename: string, header: string[], rows: (string | number | null | undefined)[][]) {
  const esc = (v: string | number | null | undefined) => {
    const t = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  const csv = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
