const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // 告诉本地壳子：我们要修改快捷键
    updateShortcuts: (settings) => ipcRenderer.send('update-shortcuts', settings),
    // 获取当前本地存储的快捷键
    getShortcuts: () => ipcRenderer.invoke('get-shortcuts'),
    // 检查是否有本地桌面环境（用于判断是否显示设置项）
    isDesktop: true
});