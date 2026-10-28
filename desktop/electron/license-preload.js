// Мост для окна «Лицензия»: состояние лицензии и ввод ключа (запросы идут через main к сервисам).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("licenseApi", {
  get: () => ipcRenderer.invoke("license:get"),
  submit: (key) => ipcRenderer.invoke("license:submit", key),
});
