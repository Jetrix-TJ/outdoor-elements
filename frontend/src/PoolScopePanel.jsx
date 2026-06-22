import { useEffect, useState } from "react";
import { getPoolScope } from "./api.js";

// Type badge color map — colors match the SDD spec exactly.
const TYPE_COLORS = {
  labor:     "#1565c0",
  material:  "#e65100",
  equipment: "#6a1b9a",
  warranty:  "#616161",
  testing:   "#2e7d32",
};

function Badge({ type }) {
  if (!type) return null;
  const color = TYPE_COLORS[type] || "#888";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: 10,
        fontSize: 10,
        fontWeight: 600,
        color: "#fff",
        background: color,
        marginRight: 6,
        verticalAlign: "middle",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        flexShrink: 0,
      }}
    >
      {type}
    </span>
  );
}

function fmt(n) {
  return n == null ? "—" : Number(n).toLocaleString();
}

export default function PoolScopePanel({ jobId }) {
  const [scope, setScope] = useState(undefined); // undefined = loading, null = not found
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!jobId) return;
    setScope(undefined);
    getPoolScope(jobId)
      .then(setScope)
      .catch(() => setScope(null));
  }, [jobId]);

  // Loading state — skeleton lines
  if (scope === undefined) {
    return (
      <div className="pool-scope-panel loading">
        <div className="skeleton sk-line w80" />
        <div className="skeleton sk-line" />
        <div className="skeleton sk-line w60" />
      </div>
    );
  }

  // 404 or error — hide silently
  if (!scope) return null;

  return (
    <div className="pool-scope-panel">
      <button
        className="pool-scope-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="pool-scope-title">{scope.scope_type}</span>
        <span className="pool-scope-meta">
          {fmt(scope.area_sf)} SF
          {scope.total_price != null && (
            <> · ${fmt(Math.round(scope.total_price))}</>
          )}
        </span>
        <span className="pool-scope-chevron">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <ol className="pool-scope-items">
          {(scope.items || []).map((item) => (
            <li key={item.number} className="pool-scope-item">
              <div className="pool-scope-item-row">
                <span className="pool-item-num">{item.number}.</span>
                <Badge type={item.type} />
                <span className="pool-scope-item-text">{item.text}</span>
              </div>
              {item.sub_items?.length > 0 && (
                <ul className="pool-scope-subitems">
                  {item.sub_items.map((s) => (
                    <li key={s.label} className="pool-scope-subitem">
                      <span className="sub-label">{s.label})</span>
                      <span className="sub-qty">{s.qty}×</span>
                      <span className="sub-desc">{s.description}</span>
                      {s.unit && <span className="sub-unit">{s.unit}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
          {(!scope.items || scope.items.length === 0) && (
            <li className="pool-scope-empty">No line items found.</li>
          )}
        </ol>
      )}
    </div>
  );
}
