import VideoTile from './VideoTile'

export default function VideoWall({ sources, workerStatuses, token, onWorkerChange, onDelete }) {
  if (sources.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 13 }}>
        No sources registered yet. Register one to start viewing a feed.
      </div>
    )
  }

  return (
    <div className="video-wall">
      {sources.map((source) => (
        <VideoTile
          key={source.id}
          source={source}
          workerStatus={workerStatuses[source.id]}
          token={token}
          onWorkerChange={onWorkerChange}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}
