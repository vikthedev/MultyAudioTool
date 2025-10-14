import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Union, Any
import ctypes


# ------------------ Utilities ------------------
class Utils:

    class Proc:
        @staticmethod
        def handle_interrupt(*procs, process_name: str) -> int:
            """Terminate subprocesses on KeyboardInterrupt."""
            for proc in procs:
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait()
                    except (ProcessLookupError, OSError):
                        pass
            Utils.Console.cprint(f'\n\n{process_name} interrupted.', 'red')
            return 1

    class Console:
        @staticmethod
        def cprint(text: Any, color: str = "default", bg_color: Optional[str] = None) -> None:
            """ Prints colored text """
            print(f"{Utils.Format.colorize(text=text, color=color, bg_color=bg_color)}")

        @staticmethod
        def wait_press(prompt: str = "Press any key to continue...") -> None:
            """
            Cross-platform pause until user presses any key.
            Works both on Windows and Unix-like systems, safely falls back to input().
            """
            print()
            try:
                if os.name == 'nt':
                    os.system("pause")
                else:
                    # Works in most Linux/macOS shells
                    os.system(f"/bin/bash -c 'read -s -n 1 -p \"{prompt}\"'")
                    print()
            except (KeyboardInterrupt, OSError):
                # Fallback for restricted environments (IDEs, CI/CD)
                input(f"{prompt}\n")

        @staticmethod
        def cls():
            """ Clear console screen in a cross-platform way. """
            os.system('cls' if os.name == 'nt' else 'clear')

    class Format:
        @classmethod
        def to_float(cls, value: Optional[Union[int, float, str]] = None) -> Optional[float]:
            """Return float, treating very small numbers as zero."""
            if value is not None:
                value = cls.to_str(value)
                try:
                    value = (float(value) if value and abs(float(value)) > 1e-9 else 0)
                except (ValueError, TypeError):
                    value = None
            return value

        @classmethod
        def to_int(cls, value: Optional[Union[int, to_float, str]] = None) -> Optional[int]:
            """Return int or None if conversion fails."""
            if value is not None:
                value = cls.to_str(value)
                try:
                    value = int(float(value))
                except (ValueError, TypeError):
                    value = None
            return value

        @staticmethod
        def to_str(value: Optional[Union[to_int, to_float, str]] = None, strip: bool = False) -> Optional[str]:
            """Return stripped str."""
            if value is not None:
                try:
                    value = re.sub(r'[\x00-\x1F\x7F]', '', str(value))
                    if strip: value = value.strip()
                except (ValueError, TypeError):
                    value = None
            return value

        @classmethod
        def to_frequency(cls, value: Optional[Union[to_int, to_float, str]]) -> Optional[to_int]:
            """
            Parse a frequency like '48kHz', '44.1k', '44100Hz', or 44100 (int) into Hz.

            - Accepts strings or numbers.
            - Supports kHz and Hz suffixes.
            - Interprets decimals (e.g. 44.1) as kHz.
            - Returns None if malformed or outside 10–100000 Hz range.
            """
            # Try to interpret numeric first
            if (v := cls.to_float(value)) is not None:
                value = int(v) if v.is_integer() else int(v * 1000)
            else:
                value = cls.to_str(value, True)
                match = re.search(
                    r"^(?P<khz>\d{1,3}(?:\.\d{1,3})?)\s*k(?:hz)?|(?P<hz>\d{4,6})(?:\s*hz)?$",
                    value, re.IGNORECASE,
                )
                if match:
                    value = int(float(match.group("khz")) * 1000) if match.group("khz") else int(match.group("hz"))
                else:
                    value = None
            # value = value if value and 10000 <= value <= 100000 else None
            value = value if value == 44100 or value == 48000 else None
            return value

        @staticmethod
        def to_human_time(seconds: Optional[to_float] = None, with_ms: bool = False) -> str:
            """Format seconds as HH:MM:SS.ms"""
            if seconds is None:
                return 'None'
            hh = seconds // 3600
            mm = (seconds % 3600) // 60
            ss = seconds % 60
            ms = f".{int((seconds % 1) * 1000):03d}" if with_ms else ""
            return f"{hh:02d}:{mm:02d}:{ss:02d}{ms}"

        @staticmethod
        def colorize(text: Any, color: str = "default", bg_color: Optional[str] = None) -> str:
            """
            Return ANSI-colored text with optional background.
            Supported colors (foreground):
                red, yellow, green, blue, white, magenta, cyan,
                lightred, lightyellow, lightgreen, lightblue,
                lightgray, darkgray, lightmagenta, lightcyan, default
            Supported background colors:
                black, red, green, yellow, blue, magenta, cyan, white, lightgray, darkgray
            """

            reset_code = "\033[0m"

            fg_colors = {
                "black": "\033[30m",
                "red": "\033[31m",
                "yellow": "\033[33m",
                "green": "\033[32m",
                "blue": "\033[34m",
                "white": "\033[97m",
                "magenta": "\033[35m",
                "cyan": "\033[36m",
                "lightred": "\033[91m",
                "lightyellow": "\033[93m",
                "lightgreen": "\033[92m",
                "lightblue": "\033[94m",
                "lightgray": "\033[37m",
                "darkgray": "\033[90m",
                "lightmagenta": "\033[95m",
                "lightcyan": "\033[96m",
                "default": "\033[39m"
            }

            bg_colors = {
                "black": "\033[40m",
                "red": "\033[41m",
                "green": "\033[42m",
                "yellow": "\033[43m",
                "blue": "\033[44m",
                "magenta": "\033[45m",
                "cyan": "\033[46m",
                "white": "\033[47m",
                "lightgray": "\033[100m",
                "darkgray": "\033[100m",  # unsure
                "default": "\033[49m"
            }

            fg_code = fg_colors.get(color, fg_colors["default"])
            bg_code = bg_colors.get(bg_color, None) or ''

            return f"{bg_code}{fg_code}{text}{reset_code}"

    class IO:
        @staticmethod
        def put_params_to_file(file: Path, params: Optional[Dict[str, Any]] = None, **kwargs) -> None:
            """
            Save key=value pairs into a text file.
            - Accepts a dict via 'params' or arbitrary key=value pairs via kwargs.
            - Each pair is written on a separate line.
            - Ignores reserved keys: 'file', 'params'.
            """

            # ---- Centralized normalization ----
            normalized: Dict[str, Any] = {}

            if params and isinstance(params, dict):
                normalized.update(params)
            else:
                normalized.update(kwargs)
                if 'kwargs' in normalized and isinstance(normalized['kwargs'], dict):
                    nested = normalized.pop('kwargs')
                    normalized.update(nested)

            # ---- Filtering and serialization ----
            lines: List[str] = []
            for key, value in normalized.items():
                clean_key = Utils.Format.to_str(key, strip=True)
                if clean_key and clean_key not in {"cls", "file", "params"}:
                    lines.append(f"{clean_key}={value if value is not None else ''}")

            # ---- Write to file ----
            result: str = "\n".join(lines)
            if result:
                try:
                    with file.open("w", encoding="utf-8") as f:
                        f.write(result + "\n")
                except (FileNotFoundError, OSError):
                    pass

        @staticmethod
        def get_params_from_file(file: Path, names: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
            """
            Load key=value pairs from a file and optionally filter by names.
            Key names are stripped and validated; values are kept as-is.
            Returns None if file missing or contains no valid data.
            """
            result: Optional[Dict[str, str]] = None

            if file.exists():
                try:
                    with file.open("r", encoding="utf-8") as f:
                        data = {}
                        for line in f:
                            if "=" in line:
                                key, val = line.strip().split("=", 1)
                                clean_key = Utils.Format.to_str(key, strip=True)
                                if clean_key:
                                    data[clean_key] = val
                except (FileNotFoundError, OSError):
                    data = {}

                if names:
                    normalized_names = [n for n in (Utils.Format.to_str(name, strip=True) for name in names) if n]
                    result = {n: data.get(n) for n in normalized_names}
                elif data:
                    result = data

            return result

        @staticmethod
        def delete_files(files: Optional[Union[Path, List[Path]]]):
            """Delete  files in a Windows-safe way, fallback to unlink if needed."""
            if isinstance(files, Path):
                files = [files]
            for file_path in files:
                if file_path.exists():
                    try:
                        subprocess.run(
                            ['cmd', '/c', 'del', '/f', '/q', str(file_path)],
                            check=True,
                            shell=False
                        )
                    except subprocess.CalledProcessError:
                        try:
                            file_path.unlink()
                        except OSError as e:
                            print(f"Warning: failed to delete {file_path}: {e}")

        @staticmethod
        def is_executable_exists(bin_file: Union[str, Path]) -> bool:
            """
            Check whether the specified binary exists or is callable.
            Works for both absolute paths and binaries available via system PATH.
            Ensures the binary physically exists even if PATH entry is stale.
            """
            exists = False
            try:
                path = Path(bin_file)

                # Case 1: absolute or relative path to file
                if path.is_file():
                    exists = True

                # Case 2: command found in PATH (and file exists)
                elif resolved := shutil.which(str(bin_file)):
                    exists = Path(resolved).is_file()

                # Case 3: final check — try executing it directly
                if not exists:
                    process = subprocess.run(
                        [str(bin_file), '--version'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    exists = process.returncode == 0

            except (FileNotFoundError, OSError, subprocess.SubprocessError, KeyboardInterrupt):
                exists = False

            return exists

        @classmethod
        def is_file_locked(cls, file_path: str, mode: str = "delete") -> Optional[bool]:
            """
            Check if a file is locked on Windows for 'write' or 'delete'.
            Returns: True if locked, False if not, None if file does not exist.
            """
            path = Path(file_path)
            mode = mode if mode in ("write", "delete") else "delete"
            locked: Optional[bool] = None

            if not path.exists():
                return None

            try:
                if mode == "write":
                    # Try to open for writing (no creation, just access test)
                    with path.open("a+b"):
                        pass
                else:
                    # Test rename/restore for delete lock
                    tmp_path = path.with_name(f"{path.name}.tmp_lock_check")
                    path.rename(tmp_path)
                    tmp_path.rename(path)
                locked = False
            except (OSError, PermissionError):
                pass

            return locked

        @classmethod
        def is_file_locked_low(cls, file_path: str, mode: str = "delete") -> Optional[bool]:
            """
            Check with WindowsAPI if a file is locked on Windows for 'write' or 'delete'
            Returns: True if locked, False if not, None if file does not exist.
            """
            path = Path(file_path)
            mode = mode if mode in ("write", "delete") else "delete"

            if not path.exists():
                return None

            GENERIC_WRITE = 0x40000000
            DELETE = 0x00010000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3

            access = GENERIC_WRITE if mode == "write" else DELETE
            share_mode = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            handle = ctypes.windll.kernel32.CreateFileW(
                str(path),
                access,
                share_mode,
                None,
                OPEN_EXISTING,
                0,
                None
            )

            if handle == -1 or handle == ctypes.c_void_p(-1).value:
                locked = True
            else:
                ctypes.windll.kernel32.CloseHandle(handle)
                locked = False

            return locked

        @classmethod
        def is_file_locked_totally(cls,
                                   file_path: str,
                                   mode: str = "delete",
                                   retries: int = 5,
                                   delay: float = 0.4
                                   ) -> Optional[bool]:
            """
            Check if a file remains locked on Windows for 'write' or 'delete'
            after several retries with delays.
            Returns: True if locked, False if not, None if file does not exist.
            """
            locked: Optional[bool] = None
            mode = mode if mode in ("write", "delete") else "delete"

            for _ in range(max(1, retries)):
                locked = cls.is_file_locked(file_path=file_path, mode=mode)
                if locked is not True:
                    break
                time.sleep(delay)

            return locked

        @staticmethod
        def absolute_self(source: str) -> Path:
            """
            Return an absolute version of this path by prepending the current
            script directory. No normalization or symlink resolution is performed.
            """
            source_path = Path(source.strip())
            if not source_path.is_absolute() and (source.startswith('.\\') or str(source_path.parent) != '.'):
                source_path = Path(sys.argv[0]).parent / source_path
            return source_path

    # ----------------- uncategorized methods -----------------

    @staticmethod
    def update_progress_bar(start_time: float,
                            percent_done: float,
                            seconds_passed: Optional[float] = None,
                            total_duration: Optional[float] = None) -> None:
        """
        Update and print the progress bar with correct colors:
        - Processed segment
        - Processing segment
        - Unprocessed segment
        Percent is aligned at fixed position for stability.
        """
        BAR_WIDTH = 30
        PERCENT_POS = 12

        bar_fill = {
            'done': {'fg': 'lightyellow', 'fg_label': 'lightyellow', 'bg': 'blue', 'char': ' '},
            'in_progress': {'fg': 'black', 'fg_label': 'black', 'bg': 'yellow', 'char': '•'},
            'undone': {'fg': 'lightyellow', 'fg_label': 'cyan', 'bg': 'black', 'char': '•'},
        }

        bar_chars = []
        filled_len = int(BAR_WIDTH * percent_done / 100)

        # Fixed position for percent text (0-indexed)
        percent_str = "{0:{spec}}%".format(percent_done, spec=f"{bar_fill.get('undone').get('char')}>5.1f")
        # percent_str = f"{percent_done:bar_fill.get('undone').get('bg')5.1f}% "

        for i in range(BAR_WIDTH):
            # Determine segment color
            if filled_len == BAR_WIDTH:
                status = 'done'
            elif i < filled_len - 1:
                status = 'done'
            elif i == filled_len - 1:
                status = 'in_progress'
            else:
                status = 'undone'

            bg = bar_fill.get(status).get('bg')
            char = bar_fill.get(status).get('char')
            fg = bar_fill.get(status).get('fg')

            # Overlay percent text if it falls at this position
            idx_in_percent = i - PERCENT_POS
            if 0 <= idx_in_percent < len(percent_str):
                if bar_fill.get('undone').get('char') != percent_str[idx_in_percent]:
                    char = percent_str[idx_in_percent]
                    fg = bar_fill.get(status).get('fg_label')

            bar_chars.append(Utils.Format.colorize(char, color=fg, bg_color=bg))

        bar_line = f"{''.join(bar_chars)}"
        elapsed_sec = time.time() - start_time
        elapsed_fmt = Utils.Format.to_human_time(elapsed_sec)
        current_fmt = Utils.Format.to_human_time(seconds_passed)
        total_fmt = Utils.Format.to_human_time(total_duration)
        sys.stdout.write(f"{elapsed_fmt} >> {bar_line} time={current_fmt} total={total_fmt}\r")
        sys.stdout.flush()

