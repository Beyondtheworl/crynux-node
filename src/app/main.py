import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler

import psutil

_logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
dt_fmt = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{"
)
handler.setFormatter(formatter)
_logger.addHandler(handler)
_logger.setLevel(logging.DEBUG)

if getattr(sys, "frozen", False):
    app_path = os.path.dirname(sys.executable)
    system_name = platform.system()

    if system_name == "Darwin":
        resdir = os.path.join(os.path.dirname(app_path), "Resources")
        os.environ["CRYNUX_SERVER_CONFIG"] = os.path.join(resdir, "config", "config.yml")

        from crynux_server import config as crynux_config

        cfg = crynux_config.get_config()
        cfg.task_dir = os.path.join(resdir, "tasks")
        cfg.web_dist = os.path.join(resdir, "webui/dist")
        cfg.log.dir = os.path.join(resdir, "logs")
        cfg.db = f"sqlite+aiosqlite://{os.path.join(resdir, 'db/server.db')}"
        assert cfg.task_config is not None
        cfg.task_config.output_dir = os.path.join(resdir, "data/results")
        cfg.task_config.hf_cache_dir = os.path.join(resdir, "data/huggingface")
        cfg.task_config.external_cache_dir = os.path.join(resdir, "data/external")
        cfg.task_config.inference_logs_dir = os.path.join(resdir, "data/inference-logs")
        cfg.task_config.script_dir = os.path.join(resdir, "worker")
        cfg.resource_dir = os.path.join(resdir, "res")
        crynux_config.set_config(cfg)
        crynux_config.dump_config(cfg)

    elif system_name == "Windows":
        os.environ["CRYNUX_SERVER_CONFIG"] = os.path.join("config", "config.yml")

    else:
        error = RuntimeError(f"Unsupported platform: {system_name}")
        _logger.error(error)
        raise error

elif os.getenv("CRYNUX_SERVER_CONFIG") is None:
    index = __file__.rfind(os.path.sep + "src")
    root_dir = __file__[:index]

    os.environ["CRYNUX_SERVER_CONFIG"] = os.path.join(root_dir, "config", "config.yml")

assert os.environ["CRYNUX_SERVER_CONFIG"]
config_file_path = os.path.abspath(os.environ["CRYNUX_SERVER_CONFIG"])
_logger.info(f"Start Crynux Node from: {config_file_path}")

import asyncio
import sys
from PyQt6.QtGui import QDesktopServices, QIcon, QAction, QPixmap
from PyQt6.QtCore import QUrl, Qt, qInstallMessageHandler, QtMsgType
from PyQt6.QtWidgets import QWidget, QApplication, QSplashScreen, QStackedLayout, QSystemTrayIcon, QMenu
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
import qasync

from anyio import Event, create_task_group, sleep
from crynux_server.run import CrynuxRunner


def init_log(_logger, config):
    if config.log.level == "DEBUG":
        os.environ['QT_LOGGING_RULES'] = "qt.webenginecontext.debug=true"

    def qt_message_handler(mode, context, message):

        position = '[QT] line: %d, func: %s(), file: %s\n' % (context.line, context.function, context.file)
        msg = '[QT] %s\n' % message

        if mode == QtMsgType.QtInfoMsg:
            _logger.info(msg)
            _logger.info(position)
        elif mode == QtMsgType.QtWarningMsg:
            _logger.warning(msg)
            _logger.warning(position)
        elif mode == QtMsgType.QtCriticalMsg:
            _logger.error(msg)
            _logger.error(position)
        elif mode == QtMsgType.QtFatalMsg:
            _logger.fatal(msg)
            _logger.fatal(position)
        else:
            _logger.debug(msg)
            _logger.debug(position)

    qInstallMessageHandler(qt_message_handler)

    log_file = os.path.join(config.log.dir, "main.log")
    file_handler = RotatingFileHandler(
        log_file,
        encoding="utf-8",
        delay=True,
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
    ))
    _logger.addHandler(file_handler)


class CustomWebEnginePage(QWebEnginePage):

    def createWindow(self, _type):
        page = CustomWebEnginePage(self)
        page.urlChanged.connect(self.open_browser)
        return page

    def open_browser(self, url):
        page = self.sender()
        QDesktopServices.openUrl(url)
        page.deleteLater()


class CrynuxApp(QWidget):

    def __init__(self):
        super().__init__()
        _logger.debug("Initializing Application UI...")
        self.initializing = True
        self.init_ui()
        _logger.debug("Application UI initialized")

    def init_ui(self):
        stack = QStackedLayout(self)
        self.webview = QWebEngineView()
        self.webpage = CustomWebEnginePage()
        self.webview.setPage(self.webpage)

        settings = self.webpage.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            True
        )

        stack.addWidget(self.webview)
        self.setLayout(stack)
        self.setGeometry(300, 300, 1300, 800)
        self.setWindowTitle('Crynux Node')

    def delayed_show(self):
        self.webview.load(QUrl("http://localhost:7412"))
        self.show()

    def show_recreate_window(self):
        if self.initializing:
            return

        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        event.accept()


def main():
    from crynux_server import config as crynux_config

    _logger.debug("Starting Crynux node...")

    crynux_cfg = crynux_config.get_config()
    _logger.debug("Log file loaded")

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(crynux_cfg.resource_dir, "icon.ico")))
    app.setQuitOnLastWindowClosed(False)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    geometry = app.primaryScreen().availableGeometry()

    pixmap = QPixmap(os.path.join(crynux_cfg.resource_dir, "splash.png")).scaledToWidth(
        int(geometry.width() / 4),
        mode=Qt.TransformationMode.SmoothTransformation
    )

    splash_screen = QSplashScreen(
        pixmap=pixmap
    )
    splash_screen.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    _logger.debug("show splash screen")

    tray = QSystemTrayIcon()
    tray.setIcon(QIcon(os.path.join(crynux_cfg.resource_dir, "icon.ico")))
    tray.setVisible(True)

    tray_menu = QMenu()
    tray_menu_dashboard = QAction("Dashboard")
    tray_menu_discord = QAction("Crynux Discord")
    tray_menu_exit = QAction("Exit")

    tray_menu.addAction(tray_menu_dashboard)
    tray_menu.addAction(tray_menu_discord)
    tray_menu.addSeparator()
    tray_menu.addAction(tray_menu_exit)

    tray.setContextMenu(tray_menu)

    crynux_app = CrynuxApp()

    init_log(_logger, crynux_cfg)

    async def _main():

        splash_screen.show()

        _logger.debug("Creating runner and crynux_app")
        runner = CrynuxRunner()

        exit_event = Event()

        async def wait_for_exit():
            await exit_event.wait()
            _logger.debug("exit event is set")
            await runner.stop()
            _logger.debug("runner stop")

        def system_tray_action(reason):
            if (platform.system() == "Windows"
                    and reason == QSystemTrayIcon.ActivationReason.Trigger
                    and reason != QSystemTrayIcon.ActivationReason.Context):
                crynux_app.show_recreate_window()

        def go_to_discord():
            QDesktopServices.openUrl(QUrl("https://discord.gg/JRkuY9FW49"))

        tray.activated.connect(system_tray_action)
        tray_menu_dashboard.triggered.connect(crynux_app.show_recreate_window)
        tray_menu_discord.triggered.connect(go_to_discord)
        tray_menu_exit.triggered.connect(exit_event.set)

        def app_state_changed(reason):
            if reason == Qt.ApplicationState.ApplicationActive:
                if app.activeWindow() is None:
                    crynux_app.show_recreate_window()

        app.applicationStateChanged.connect(app_state_changed)

        async with create_task_group() as tg:
            _logger.debug("Starting init task")
            tg.start_soon(wait_for_exit)
            await tg.start(runner.run)
            await sleep(3.5)
            crynux_app.initializing = False
            _logger.debug("Starting the user interface")
            crynux_app.delayed_show()
            splash_screen.finish(crynux_app.activateWindow())

        _logger.debug("app _main finish")

    try:
        with loop:
            loop.run_until_complete(_main())
        _logger.debug("app quit")
    finally:
        proc = psutil.Process(os.getpid())
        for p in proc.children(recursive=True):
            try:
                _logger.info(f"Kill process: {p.ppid()}, {p.cmdline()}")
                p.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                _logger.error(e)

        proc.kill()


if __name__ == '__main__':
    main()
