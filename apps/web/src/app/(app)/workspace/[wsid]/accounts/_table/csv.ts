import { formatValue } from './cell';
import type { Column, TableRow } from './types';

/**
 * One CSV cell: RFC 4180 quoting, and a cell a spreadsheet would run as a formula gets a leading '
 * (= + - @, and a leading tab or carriage return, per OWASP's CSV injection guidance).
 * Shared by the export route and Export selected.
 */
export function csvCell(v: string): string {
  const safe = /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** A cell as text, formatted as the table shows it. */
export function cellText(row: TableRow, c: Column): string {
  const cell = row.cells[c.id];
  switch (cell?.state) {
    case 'value':
    case 'derived':
    case 'stale':
      return formatValue(cell.value, c.value_type);
    case 'no_data':
      return 'No data';
    case 'skipped':
      return 'Skipped';
    case 'error':
      return `Error: ${cell.error ?? 'error'}`;
    default:
      return '';
  }
}
