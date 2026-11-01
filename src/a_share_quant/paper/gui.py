"""Small local Tkinter console for the offline ETF paper-trading workflow."""

import ctypes
import json
import os
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

APP_TITLE = "A股ETF量化 · 模拟盘"


def _activate_existing_window() -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        window_handle = user32.FindWindowW(None, APP_TITLE)
        if not window_handle:
            return False
        user32.ShowWindow(window_handle, 9)
        user32.SetForegroundWindow(window_handle)
        return True
    except Exception:
        return False


class PaperConsole:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.uv_executable = shutil.which("uv")
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("860x620")
        self.root.minsize(760, 520)
        self.root.configure(bg="#f4f6f8")
        self.running = False
        self._build()
        self.refresh_status()

    def _build(self) -> None:
        tk.Label(
            self.root,
            text="A股ETF量化 · 离线模拟盘",
            font=("Microsoft YaHei UI", 20, "bold"),
            bg="#f4f6f8",
            fg="#162b3d",
        ).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Label(
            self.root,
            text="只读取本地数据，不连接券商，不发送真实委托",
            font=("Microsoft YaHei UI", 10),
            bg="#f4f6f8",
            fg="#65727d",
        ).pack(anchor="w", padx=26)

        status = tk.LabelFrame(
            self.root,
            text="当前状态",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="white",
            fg="#253746",
            padx=16,
            pady=12,
        )
        status.pack(fill="x", padx=24, pady=16)
        self.status_text = tk.Label(
            status,
            text="正在读取…",
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 10),
            bg="white",
            fg="#253746",
        )
        self.status_text.pack(fill="x")

        buttons = tk.Frame(self.root, bg="#f4f6f8")
        buttons.pack(fill="x", padx=24)
        self.primary_button = self._button(
            buttons,
            "一键每日运行",
            self.run_daily,
            background="#176b87",
            foreground="white",
        )
        self.primary_button.pack(side="left", padx=(0, 10))
        self._button(buttons, "只更新数据", self.update_data).pack(side="left", padx=5)
        self._button(buttons, "只运行模拟盘", self.run_paper).pack(side="left", padx=5)
        self._button(buttons, "刷新状态", self.refresh_status).pack(side="left", padx=5)
        self._button(buttons, "打开日报", self.open_report).pack(side="left", padx=5)
        self._button(buttons, "项目文件夹", self.open_project).pack(side="left", padx=5)

        output_frame = tk.LabelFrame(
            self.root,
            text="运行记录",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg="white",
            fg="#253746",
        )
        output_frame.pack(fill="both", expand=True, padx=24, pady=16)
        self.output = scrolledtext.ScrolledText(
            output_frame,
            wrap="word",
            font=("Consolas", 9),
            bg="#101820",
            fg="#d9e2ec",
            insertbackground="white",
            padx=10,
            pady=10,
        )
        self.output.pack(fill="both", expand=True, padx=8, pady=8)
        self._log("程序已就绪。")
        self._log("每天收盘并发布最终行情后，点击“一键每日运行”。")
        if self.uv_executable is None:
            self._log("错误：未找到uv命令。请先在终端运行 uv sync --dev。")

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: object,
        *,
        background: str = "#e5e9ed",
        foreground: str = "#17242e",
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=background,
            fg=foreground,
            activebackground="#cbd5dc",
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
        )

    def _log(self, text: str) -> None:
        self.output.insert("end", text.rstrip() + "\n")
        self.output.see("end")

    def _run_background(self, commands: list[list[str]], description: str) -> None:
        if self.uv_executable is None:
            messagebox.showerror("缺少uv", "未找到uv命令，请先安装或修复开发环境。")
            return
        if self.running:
            messagebox.showinfo("正在运行", "上一个任务尚未完成。")
            return
        self.running = True
        self.primary_button.configure(state="disabled")
        self._log(f"\n=== {description} ===")

        def worker() -> None:
            return_code = 0
            for command in commands:
                command = [self.uv_executable, *command[1:]]
                self.root.after(0, self._log, "$ " + " ".join(command))
                process = subprocess.Popen(
                    command,
                    cwd=self.project_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if os.name == "nt"
                    else 0,
                )
                if process.stdout is not None:
                    for line in process.stdout:
                        self.root.after(0, self._log, line.rstrip())
                return_code = process.wait()
                if return_code != 0:
                    break
            self.root.after(0, self._finish, return_code, description)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, return_code: int, description: str) -> None:
        self.running = False
        self.primary_button.configure(state="normal")
        self.refresh_status()
        if return_code == 0:
            self._log(f"{description}：成功")
            messagebox.showinfo("完成", f"{description}已完成。")
        else:
            self._log(f"{description}：失败，退出码 {return_code}")
            messagebox.showerror(
                "运行失败",
                "任务失败。请查看运行记录或最新错误信息；数据未通过校验时不会推进模拟盘。",
            )

    def run_daily(self) -> None:
        self._run_background(
            [
                ["uv", "run", "quant-daily-update", "run"],
                ["uv", "run", "quant-paper", "run-latest"],
            ],
            "每日数据更新与模拟盘",
        )

    def update_data(self) -> None:
        self._run_background(
            [["uv", "run", "quant-daily-update", "run"]],
            "每日数据更新",
        )

    def run_paper(self) -> None:
        self._run_background(
            [["uv", "run", "quant-paper", "run-latest"]],
            "模拟盘推进",
        )

    def refresh_status(self) -> None:
        state_path = self.project_root / "data" / "paper" / "state.json"
        if not state_path.is_file():
            self.status_text.configure(text="尚未初始化模拟盘。")
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            report = state.get("last_report", {})
            text = (
                f"模式：模拟盘　真实交易：关闭\n"
                f"策略：{state.get('strategy')}　状态：{state.get('status')}\n"
                f"已处理至：{state.get('last_processed_date') or '尚未运行'}"
                f"　下一交易日：{state.get('next_trading_date') or '待定'}\n"
                f"总权益：{float(report.get('equity', state.get('initial_cash', 0))):,.2f} 元"
                f"　现金：{float(report.get('cash', 0)):,.2f} 元"
                f"　持仓：{int(report.get('holding_count', 0))} 只\n"
                f"最近对账：{'通过' if report.get('reconciled', True) else '失败'}"
                f"　告警：{', '.join(report.get('alerts', [])) or '无'}"
            )
            self.status_text.configure(text=text)
        except Exception as error:
            self.status_text.configure(text=f"读取状态失败：{error}")

    def open_report(self) -> None:
        path = self.project_root / "runs" / "paper" / "latest_report.md"
        if path.is_file():
            os.startfile(path)
        else:
            messagebox.showinfo("暂无日报", "请先运行一次模拟盘。")

    def open_project(self) -> None:
        os.startfile(self.project_root)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if _activate_existing_window():
        return
    project_root = Path(__file__).resolve().parents[3]
    PaperConsole(project_root).run()


if __name__ == "__main__":
    main()
