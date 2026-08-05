/* Golden-retriever blob cache for cross-reload upload resume (Issue 395).
 *
 * Classic-script adaptation of @uppy/golden-retriever/lib/ServiceWorker.js
 * (v5 — the shipped file ends with an ESM `export {}` marker, so it cannot be
 * registered as a classic worker directly; keep this in sync on upgrades).
 * Holds added File blobs in worker memory so a page reload can restore and
 * resume in-flight uploads without re-selecting files. If the worker is
 * evicted, resume degrades to ghost-mode (re-select the file; already-uploaded
 * parts are skipped via listParts).
 */
const fileCache = Object.create(null)

function getCache(name) {
  fileCache[name] ??= Object.create(null)
  return fileCache[name]
}

self.addEventListener('install', (event) => {
  event.waitUntil(Promise.resolve().then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

function sendMessageToAllClients(msg) {
  self.clients.matchAll().then((clientList) => {
    clientList.forEach((client) => {
      client.postMessage(msg)
    })
  })
}

function addFile(store, file) {
  getCache(store)[file.id] = file.data
}

function removeFile(store, fileID) {
  delete getCache(store)[fileID]
}

function getFiles(store) {
  sendMessageToAllClients({
    type: 'uppy/ALL_FILES',
    store,
    files: getCache(store),
  })
}

self.addEventListener('message', (event) => {
  const data = event.data
  switch (data?.type) {
    case 'uppy/ADD_FILE':
      addFile(data.store, data.file)
      break
    case 'uppy/REMOVE_FILE':
      removeFile(data.store, data.fileID)
      break
    case 'uppy/GET_FILES':
      getFiles(data.store)
      break
    default:
      throw new Error(`[ServiceWorker] Unsupported event.data.type. Got: ${data?.type}`)
  }
})
