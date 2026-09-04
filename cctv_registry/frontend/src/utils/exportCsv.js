export function exportCamerasToCsv(cameras, filename = 'camera-registry-export.csv') {
  if (!cameras.length) return

  const headers = Object.keys(cameras[0])
  const rows = cameras.map((cam) =>
    headers.map((h) => `"${String(cam[h]).replace(/"/g, '""')}"`).join(',')
  )
  const csv = [headers.join(','), ...rows].join('\n')

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
