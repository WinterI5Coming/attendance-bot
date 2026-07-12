"""Tkinter GUI for AttendanceBot database management."""

from __future__ import annotations

from pathlib import Path
import os
import tkinter as tk
from tkinter import filedialog, messagebox

from bot.manager.database_service import DatabaseManagerService


class DatabaseManagerGui:
    """Small Windows-friendly GUI for backup and restore operations."""

    def __init__(self, service: DatabaseManagerService) -> None:
        self.service = service
        self.root = tk.Tk()
        self.root.title("Discord 출석 봇 데이터 관리")
        self.root.geometry("680x360")
        self.status_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.backup_var = tk.StringVar()
        self.message_var = tk.StringVar()
        self._build()
        self.refresh_status()

    def run(self) -> None:
        """Start the Tk event loop."""

        self.root.mainloop()

    def _build(self) -> None:
        """Build GUI widgets."""

        frame = tk.Frame(self.root, padx=18, pady=18)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            frame,
            text="Discord 출석 봇 데이터 관리",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w", pady=(0, 14))

        self._add_status_row(frame, "현재 DB 상태:", self.status_var)
        self._add_status_row(frame, "현재 DB 경로:", self.path_var)
        self._add_status_row(frame, "마지막 백업:", self.backup_var)

        button_frame = tk.Frame(frame)
        button_frame.pack(anchor="w", pady=16)

        buttons = [
            ("데이터 백업하기", self.backup),
            ("백업 파일 복원하기", self.restore),
            ("백업 폴더 열기", lambda: self.open_folder(self.service.backups_directory)),
            ("현재 DB 폴더 열기", lambda: self.open_folder(self.service.data_directory)),
            ("종료", self.root.destroy),
        ]
        for text, command in buttons:
            tk.Button(button_frame, text=text, width=22, command=command).pack(
                side=tk.LEFT,
                padx=(0, 8),
                pady=4,
            )

        message = tk.Label(
            frame,
            textvariable=self.message_var,
            fg="#1f5f99",
            wraplength=620,
            justify="left",
        )
        message.pack(anchor="w", pady=(8, 0))

    def _add_status_row(
        self,
        parent: tk.Widget,
        label: str,
        variable: tk.StringVar,
    ) -> None:
        row = tk.Frame(parent)
        row.pack(fill=tk.X, anchor="w", pady=3)
        tk.Label(row, text=label, width=14, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, textvariable=variable, anchor="w", wraplength=500).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
        )

    def refresh_status(self) -> None:
        """Refresh status labels."""

        status = self.service.get_status()
        self.status_var.set(status["db_status"])
        self.path_var.set(status["db_path"])
        self.backup_var.set(status["last_backup"])

    def backup(self) -> None:
        """Create a backup and show the result."""

        try:
            result = self.service.create_backup()
        except Exception as exc:
            messagebox.showerror("백업 실패", str(exc))
            self.message_var.set(f"백업 실패: {exc}")
            return

        message = (
            "데이터 백업이 완료되었습니다.\n\n"
            f"백업 파일:\n{result.backup_path}\n\n"
            f"메타데이터:\n{result.metadata_path}"
        )
        messagebox.showinfo("백업 완료", message)
        self.message_var.set(message)
        self.refresh_status()

    def restore(self) -> None:
        """Select and restore a backup DB file."""

        path = filedialog.askopenfilename(
            title="복원할 백업 DB 파일 선택",
            filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")],
            initialdir=self.service.backups_directory,
        )
        if not path:
            return

        confirm = messagebox.askyesno(
            "복원 확인",
            "선택한 백업 파일로 현재 데이터를 복원합니다.\n\n"
            "현재 데이터는 복원 전에 자동으로 백업됩니다.\n"
            "복원 후에는 선택한 백업 시점의 데이터로 변경됩니다.\n\n"
            "계속하시겠습니까?",
        )
        if not confirm:
            return

        try:
            result = self.service.restore_backup(Path(path))
        except Exception as exc:
            messagebox.showerror(
                "복원 실패",
                f"복원에 실패했습니다.\n\n원인: {exc}\n\n"
                f"현재 데이터는 유지되었거나 자동 복구되었습니다.\n"
                f"로그 파일: {self.service.logs_directory / 'database-manager.log'}",
            )
            self.message_var.set(f"복원 실패: {exc}")
            self.refresh_status()
            return

        message = (
            "데이터베이스 복원이 완료되었습니다.\n\n"
            f"복원 파일:\n{result.restored_from}\n\n"
            f"현재 DB:\n{result.current_database}\n\n"
            "복원 전 데이터 백업:\n"
            f"{result.pre_restore_backup or '기존 DB가 없어 생성하지 않음'}\n\n"
            "AttendanceBot.exe를 실행하면 복원된 데이터를 사용할 수 있습니다."
        )
        messagebox.showinfo("복원 완료", message)
        self.message_var.set(message)
        self.refresh_status()

    def open_folder(self, path: Path) -> None:
        """Open a folder in the platform file browser."""

        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("폴더 위치", str(path))
