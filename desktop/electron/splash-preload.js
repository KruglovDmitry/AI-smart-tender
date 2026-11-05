// Мост для окна загрузки: статусы сервисов и кнопка «Закрыть» при ошибке.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("splashApi", {
  onState: (callback) => ipcRenderer.on("splash:state", (_event, state) => callback(state)),
  close: () => ipcRenderer.send("splash:close"),
});
