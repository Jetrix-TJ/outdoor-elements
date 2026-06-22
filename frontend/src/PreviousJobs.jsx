import { useEffect, useState } from "react";
import { listJobs, deleteJob } from "./api";

function relativeTime(isoString) {
  if (!isoString) return "";
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function PreviousJobs({ onResume }) {
  const [jobs, setJobs] = useState(null);
  const [deleting, setDeleting] = useState(null);

  useEffect(() => {
    listJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  async function handleDelete(e, jobId) {
    e.stopPropagation();
    setDeleting(jobId);
    try {
      await deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
    } catch {
      // silent — job may have already been deleted
    } finally {
      setDeleting(null);
    }
  }

  if (!jobs || jobs.length === 0) return null;

  return (
    <div className="prev-jobs">
      <h3 className="prev-jobs-title">Previous jobs</h3>
      <ul className="prev-jobs-list">
        {jobs.map((j) => (
          <li
            key={j.job_id}
            className="prev-job-row"
            onClick={() => onResume(j.job_id)}
            title="Click to resume this job"
          >
            <span className="prev-job-icon material-symbols-outlined">
              {j.status === "done" ? "check_circle" : "hourglass_empty"}
            </span>
            <span className="prev-job-name">{j.filename ?? j.job_id}</span>
            <span className="prev-job-meta">
              {j.page_count != null ? `${j.kept_count ?? "?"} / ${j.page_count} pages` : ""}
            </span>
            <span className="prev-job-time">{relativeTime(j.created_at)}</span>
            <button
              className="prev-job-delete"
              onClick={(e) => handleDelete(e, j.job_id)}
              disabled={deleting === j.job_id}
              title="Delete this job"
            >
              {deleting === j.job_id ? "…" : "✕"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
