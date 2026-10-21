// Мост для верхней панели окна: переключение «чат / дашборд» и статус сервисов.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("shellApi", {
  show: (view) => ipcRenderer.send("shell:show", view),
  onState: (callback) => ipcRenderer.on("shell:state", (_event, state) => callback(state)),
});
