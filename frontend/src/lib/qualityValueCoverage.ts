/* The coverage table's row model, extracted so it can be tested without a DOM.
 *
 * WHY IT LIVES HERE. The table showed "0 ≠ 200" against Catalysts and Portfolio fit — rows that
 * reconciled to nothing because those optional gates were never evaluated, which read as 200
 * companies having gone missing. A source-inspection test cannot catch that: the page code was
 * structurally correct and the DATA was shaped in a way the model mishandled. So the model is a
 * function, and the test feeds it mixed statuses including an unassessed optional gate.
 */

export type StatusCatalog = Record<string, {
  label: string; remedy: string; is_work_remaining: boolean; is_pass: boolean;
}>;

export type CoverageInput = {
  evaluated: number;
  gate_coverage: Record<string, Record<string, number>>;
  status_catalog?: StatusCatalog;
  gate_claims?: Record<string, { label: string }>;
  required_for_entry: string[];
};

export type CoverageRow = {
  gate: string; label: string; counts: Record<string, number>;
  total: number; reconciles: boolean; required: boolean;
};

/** Every status any gate reports, in catalog order, with unknown states appended — never dropped. */
export function statusColumns(data: CoverageInput): string[] {
  const present = new Set<string>();
  Object.values(data.gate_coverage).forEach(c =>
    Object.entries(c).forEach(([s, n]) => { if ((n ?? 0) > 0) present.add(s); }));
  const catalog = data.status_catalog ?? {};
  const ordered = Object.keys(catalog).filter(s => present.has(s));
  // A state this build's catalog does not know MUST still be a column. Dropping it would make
  // the row reconcile by hiding the thing that broke it.
  const unknown = [...present].filter(s => !catalog[s]);
  return [...ordered, ...unknown];
}

export function coverageRows(data: CoverageInput): CoverageRow[] {
  const cols = statusColumns(data);
  return Object.entries(data.gate_coverage).map(([gate, counts]) => {
    // Summed over EVERY key in the data, not only the rendered columns: a count under a status
    // that did not make the column list must still be caught by the reconciliation, otherwise
    // the check validates the rendering rather than the data.
    const total = Object.values(counts).reduce((n, v) => n + (v ?? 0), 0);
    return {
      gate,
      label: data.gate_claims?.[gate]?.label ?? gate,
      counts: Object.fromEntries(cols.map(c => [c, counts[c] ?? 0])),
      total,
      reconciles: total === data.evaluated,
      required: data.required_for_entry.includes(gate),
    };
  });
}

/** What a badge should say. An unknown status is an error, never a quiet default. */
export function badgeLabel(status: string, catalog?: StatusCatalog): string {
  const entry = catalog?.[status];
  return entry ? entry.label : `UNKNOWN STATE: ${status}`;
}

export function badgeRemedy(status: string, catalog?: StatusCatalog): string | null {
  return catalog?.[status]?.remedy || null;
}
