import React, { useEffect, useState } from 'react';
import { fetchMediaUrl } from '../dataStore.js';

// Teacher Notes photos and videos taken with the phone (QR on the recorder).
// Photos carry AI notes (no names were sent to the AI); videos have no AI notes in this version.
// Review only: none of this goes into the parent email.
function MediaItem({ recordingId, item, reviewerId }) {
  const [url, setUrl] = useState(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let live = true;
    let made = null;
    fetchMediaUrl(recordingId, item.media_id, reviewerId)
      .then((u) => { made = u; if (live) setUrl(u); else URL.revokeObjectURL(u); })
      .catch(() => live && setErr(true));
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [recordingId, item.media_id, reviewerId]);
  const isVideo = item.kind === 'video';
  return (
    <div className="media-item" data-testid="media-item" style={{ marginBottom: 16 }}>
      {err && <p className="muted">Could not load this {isVideo ? 'video' : 'photo'}.</p>}
      {!err && !url && <p className="muted">Loading…</p>}
      {url && (isVideo
        ? <video src={url} controls playsInline style={{ maxWidth: '100%', maxHeight: 360, borderRadius: 8 }} />
        : <img src={url} alt="Teacher Notes photo" style={{ maxWidth: '100%', maxHeight: 360, borderRadius: 8 }} />)}
      {isVideo
        ? <p className="muted">Video (AI notes on video are not available yet).</p>
        : (
          <div>
            <strong>AI photo notes</strong>
            <p>{item.insights || 'No AI notes for this photo yet.'}</p>
            {item.related_goal_ids && item.related_goal_ids.length > 0 && (
              <p className="muted">Related goals: {item.related_goal_ids.join(', ')}</p>
            )}
          </div>
        )}
    </div>
  );
}

export default function MediaPanel({ recordingId, media, reviewerId }) {
  if (!media || media.length === 0) return null;
  return (
    <section className="panel media-panel">
      <h3>Teacher Notes photos and videos</h3>
      <p className="muted">Taken with the QR code on the recorder. Shown here only, never in the parent email.</p>
      {media.map((m) => <MediaItem key={m.media_id} recordingId={recordingId} item={m} reviewerId={reviewerId} />)}
    </section>
  );
}
