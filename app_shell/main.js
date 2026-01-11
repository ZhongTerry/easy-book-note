const { app, BrowserWindow, globalShortcut, ipcMain, session, Tray, Menu } = require('electron');
const path = require('path');
const fs = require('fs');

let mainWindow = null;
let tray = null;
let isQuiting = false;

// --- [核心修改] 快捷键持久化配置 ---
const settingsPath = path.join(app.getPath('userData'), 'settings.json');
let userSettings = {
    bossKey: 'Alt+Q',
    stopTtsKey: 'Alt+S'
};

// 加载本地保存的设置
if (fs.existsSync(settingsPath)) {
    try {
        const saved = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
        userSettings = { ...userSettings, ...saved };
    } catch (e) {
        console.error("加载设置失败:", e);
    }
}

// 注册/重新注册全局快捷键的函数
const registerGlobalShortcuts = () => {
    globalShortcut.unregisterAll(); // 清除旧绑定

    // 1. 注册老板键
    try {
        const res = globalShortcut.register(userSettings.bossKey, () => {
            if (mainWindow.isVisible()) {
                mainWindow.hide();
            } else {
                mainWindow.show();
                mainWindow.focus();
            }
        });
        if (!res) console.warn(`快捷键 ${userSettings.bossKey} 注册失败(可能被占用)`);
    } catch (e) {
        console.error("老板键格式错误:", e);
    }

    // 2. 注册停止朗读键
    try {
        globalShortcut.register(userSettings.stopTtsKey, () => {
            if (mainWindow) {
                mainWindow.webContents.send('stop-tts');
            }
        });
    } catch (e) {
        console.error("朗读控制键格式错误:", e);
    }
};

const createWindow = () => {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        title: "Smart NoteDB",
        autoHideMenuBar: true,
        icon: path.join(__dirname, '../static/icon-192.png'),
        webPreferences: {
            // --- [关键修改] 必须启用 preload.js ---
            preload: path.join(__dirname, 'preload.js'), 
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    mainWindow.loadURL('https://book.ztrztr.top/');

    // 托盘逻辑
    mainWindow.on('close', (event) => {
        if (!isQuiting) {
            event.preventDefault();
            mainWindow.hide();
        }
    });

    // 处理新窗口拦截
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        mainWindow.loadURL(url);
        return { action: 'deny' };
    });
};

const createTray = () => {
    tray = new Tray(path.join(__dirname, '../static/icon-192.png'));
    const contextMenu = Menu.buildFromTemplate([
        { label: '打开阅读器', click: () => mainWindow.show() },
        { type: 'separator' },
        { label: '彻底退出', click: () => {
            isQuiting = true;
            app.quit();
        }}
    ]);
    tray.setToolTip('Smart NoteDB Reading...');
    tray.setContextMenu(contextMenu);
    tray.on('double-click', () => {
        mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    });
};

// --- [核心修改] IPC 通信处理：监听来自网页的设置请求 ---

// 1. 获取当前快捷键配置
ipcMain.handle('get-shortcuts', () => {
    return userSettings;
});

// 2. 更新快捷键配置
ipcMain.on('update-shortcuts', (event, newSettings) => {
    userSettings = { ...userSettings, ...newSettings };
    // 保存到本地文件
    fs.writeFileSync(settingsPath, JSON.stringify(userSettings, null, 2));
    // 重新应用快捷键
    registerGlobalShortcuts();
    console.log("快捷键已更新:", userSettings);
});

app.whenReady().then(() => {
    createWindow();
    createTray();
    registerGlobalShortcuts();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('will-quit', () => {
    globalShortcut.unregisterAll();
});