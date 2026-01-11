const { app, BrowserWindow, globalShortcut, session, Tray, Menu } = require('electron');
const path = require('path');

let mainWindow = null;
let tray = null;
let isQuiting = false; // 标记是否真正退出

const createWindow = () => {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        title: "Smart NoteDB",
        autoHideMenuBar: true,
        icon: path.join(__dirname, '../static/icon-192.png'),
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    mainWindow.loadURL('https://book.ztrztr.top/');

    // === [功能 1] 托盘逻辑：拦截关闭事件 ===
    mainWindow.on('close', (event) => {
        if (!isQuiting) {
            event.preventDefault();
            mainWindow.hide(); // 只是隐藏窗口
        }
    });

    // 处理新窗口拦截
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        mainWindow.loadURL(url);
        return { action: 'deny' };
    });
};

// === [功能 2] 创建系统托盘 ===
const createTray = () => {
    // 确保你有一个图标文件，这里暂用你已有的 icon
    tray = new Tray(path.join(__dirname, '../static/icon-192.png'));
    
    const contextMenu = Menu.buildFromTemplate([
        { label: '打开阅读器', click: () => mainWindow.show() },
        { label: '检查更新', click: () => mainWindow.webContents.send('check-update') },
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

// === [功能 3] 注册全局快捷键 ===
const registerGlobalShortcuts = () => {
    // 老板键：Alt + Z (隐藏或唤醒)
    globalShortcut.register('Alt+Z', () => {
        if (mainWindow.isVisible()) {
            mainWindow.hide();
        } else {
            mainWindow.show();
            mainWindow.focus();
        }
    });

    // 你也可以加一个一键静音/停止朗读的全局键
    globalShortcut.register('Alt+S', () => {
        mainWindow.webContents.send('stop-tts');
    });
};

app.whenReady().then(() => {
    createWindow();
    createTray();
    registerGlobalShortcuts();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

// 退出前注销
app.on('will-quit', () => {
    globalShortcut.unregisterAll();
});