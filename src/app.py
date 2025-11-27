import sys
import os
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QListWidgetItem
from PyQt6.QtCore import Qt, QMimeData, QUrl
from PyQt6.QtGui import QDrag
from searcher import SampleSearcher

class SampleList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)

    def wsl_to_windows_path(self, wsl_path):
        if wsl_path.startswith("/mnt/"):
            parts = wsl_path.split('/')
            drive_letter = parts[2]    # 'c'
            rest_of_path = "\\".join(parts[3:])
            windows_path = f"{drive_letter.upper()}:\\{rest_of_path}"
            return windows_path
        return wsl_path
    
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return   
        
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        print(f"Ruta Linux: {raw_path}")
        win_path_backslashes = self.wsl_to_windows_path(raw_path)
        win_path_forward_slashes = win_path_backslashes.replace("\\", "/")
        final_url_string = f"file:///{win_path_forward_slashes}"
        url = QUrl(final_url_string)
        print(f"URL Final: {url.toString()}")

        mime_data  = QMimeData()
        mime_data.setUrls([url])

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Sample Searcher")
        self.resize(400, 600)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)

        print("Initializing IA motor...")
        self.engine = SampleSearcher()

        layout = QVBoxLayout()

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Describe Sound: ")
        self.search_bar.returnPressed.connect(self.do_search)
        layout.addWidget(self.search_bar)

        self.result_list = SampleList()
        layout.addWidget(self.result_list)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def do_search(self):
        query = self.search_bar.text()
        results = self.engine.search(query, top_k=10)
        self.result_list.clear()

        for item in results:
            filename = item['filename']
            full_path = item['route']
            list_item = QListWidgetItem(filename)
            list_item.setData(Qt.ItemDataRole.UserRole, full_path)
            self.result_list.addItem(list_item)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())