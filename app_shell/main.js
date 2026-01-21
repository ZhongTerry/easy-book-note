const { app, BrowserWindow, globalShortcut, ipcMain, session, Tray, Menu } = require('electron');
const path = require('path');
const fs = require('fs');

let mainWindow = null;
let tray = null;
let isQuiting = false;
let memoWindow = null;

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
            preload: path.join(__dirname, 'preload.js'), 
            nodeIntegration: false,
            contextIsolation: true,
            partition: 'persist:notedb'  // 持久化 session，保存 Cookie 和登录状态
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
        { label: '打开备忘录', click: () => {
            if (!memoWindow || memoWindow.isDestroyed()) {
                createMemoWindow();
            } else {
                memoWindow.show();
                memoWindow.focus();
            }
        }},
        { type: 'separator' },
        { label: '彻底退出', click: () => {
            isQuiting = true;
            app.isQuitting = true;
            if (memoWindow && !memoWindow.isDestroyed()) {
                memoWindow.destroy();
            }
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.destroy();
            }
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

// 创建备忘录窗口
function createMemoWindow() {
    if (memoWindow && !memoWindow.isDestroyed()) {
        memoWindow.show();
        memoWindow.focus();
        return;
    }

    memoWindow = new BrowserWindow({
        width: 1000,
        height: 600,
        show: false,        // 初始不显示，等内容加载完成
        // alwaysOnTop: true,  // 始终置顶
        frame: false,       // 无边框（自定义标题栏）
        transparent: true,  // 背景透明（实现圆角）
        resizable: true,
        minimizable: true,
        maximizable: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            partition: 'persist:notedb'  // 使用相同的 partition，共享登录状态
        }
    });

    memoWindow.loadURL('https://book.ztrztr.top/memo');
    
    // 等内容加载完成后再显示，避免闪烁
    memoWindow.once('ready-to-show', () => {
        memoWindow.show();
        memoWindow.focus();
    });
    
    // 窗口关闭时隐藏而不是销毁（快速唤出）
    memoWindow.on('close', (e) => {
        if (!isQuiting && !app.isQuitting) {
            e.preventDefault();
            memoWindow.hide();
        }
    });
}

// 注册全局快捷键
function registerMemoShortcut() {
    const shortcut = userSettings.memoKey || 'Ctrl+Alt+N';
    try {
        globalShortcut.register(shortcut, () => {
            if (!memoWindow || memoWindow.isDestroyed()) {
                createMemoWindow();
            } else if (memoWindow.isVisible()) {
                memoWindow.hide();
            } else {
                memoWindow.show();
                memoWindow.focus();
            }
        });
    } catch (e) {
        console.error("备忘录快捷键注册失败:", e);
    }
}

app.whenReady().then(() => {
    createWindow();
    createTray();
    registerGlobalShortcuts();
    registerMemoShortcut();  // 新增

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('will-quit', () => {
    globalShortcut.unregisterAll();
});