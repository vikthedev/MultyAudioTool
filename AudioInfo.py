import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Union

from Utils import Utils


class AudioInfo:

    FORMAT_AC3: str = "AC3"
    FORMAT_THD: str = "THD"

    def __init__(self, mediainfo_launch: Path, eac3to_launch: Path):
        """
        Initialize AudioInfo
        """
        self.DEFAULT_DIALNORM: int = -31

        self.mediainfo_launch: Path = mediainfo_launch
        self.eac3to_launch: Path = eac3to_launch
        self.checker_launch: Optional[Path] = None

        self.audio_info: Optional[Dict[str, Union[str, int, None]]] = None

    def parse(self, input_file: Path) -> "AudioInfo":
        """
        Parse audio file and populate audio_info dictionary.
        Detects audio format using magic numbers first, then extracts detailed info
        (channels, frequency, dialnorm) using the selected checker (eac3to or mediainfo).
        """

        if not input_file.is_file():
            raise sys.exit(f'FileNotFoundError: Input file {input_file} not found')

        # Detect audio format early using magic numbers from file header

        with input_file.open('rb') as f:
            first_bytes = f.read(10)
            if first_bytes.startswith(0x0B77.to_bytes(2, 'big')):
                audio_format = self.FORMAT_AC3
                checker_priority = ["mediainfo", "eac3to"]
            elif 0xF8726FBA.to_bytes(4, 'big') in first_bytes:
                audio_format = self.FORMAT_THD
                checker_priority = ["eac3to"]
            else:
                raise sys.exit(
                    Utils.Console.cprint(f'RuntimeError: Source file must be in E-AC3 or TrueHD format', 'red'))

        checker_name = None
        # Choose the best available checker
        for name in checker_priority:
            checker_launch = getattr(self, f"{name}_launch", None)
            func = getattr(self, f"_by_{name}", None)
            if checker_launch and func and Utils.IO.is_executable_exists(checker_launch):
                self.checker_launch = checker_launch
                checker_name = name
                break

        if checker_name is not None and audio_format:
            # Parse detailed info only if checker is available and format is recognized
            self.audio_info = {
                "format": audio_format,
                "duration": None,
                "channels": None,
                "freq": None,
                "dialnorm": None,
                "checker": checker_name
            }

            getattr(self, f"_by_{checker_name}")(input_file=input_file)
            # Normalize audio format
            self.audio_info["format"] = self._normalize_audio_format(self.audio_info.get("format"))

        if self.audio_info.get("format") is None:
            raise sys.exit(
                Utils.Console.cprint(f'RuntimeError: Source file must be in E-AC3 or TrueHD format', 'red'))

        return self

    @property
    def dialnorm(self) -> Optional[int]:
        """
        Return dialnorm value or None.
        """
        return self.audio_info.get("dialnorm", None)

    @property
    def freq(self) -> Optional[int]:
        """
        Return frequency value or None.
        """
        return self.audio_info.get("freq", None)

    @property
    def format(self) -> Optional[str]:
        """
        Return normalized audio format. Valid values are AC3, THD, or None.
        """
        return self.audio_info.get("format", None)

    @property
    def checker(self) -> Optional[str]:
        """
        Return actual checker value or None.
        """
        return self.audio_info.get("checker", None)

    # -------------------------
    # Private methods for each tool
    # -------------------------
    def _by_mediainfo(self, input_file: Path) -> None:
        """
        Extract audio info using MediaInfo.
        Returns a dictionary with keys: format, channels, freq, dialnorm.
        """
        try:
            result = subprocess.run(
                [self.checker_launch.absolute(), str(input_file), '--Output=JSON'],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout)
            if "media" in data and "track" in data["media"]:
                for track in data["media"]["track"]:
                    if track.get("@type") == "Audio":
                        cn = str(Utils.Format.to_int(track.get("Channels")))
                        cn = "5.1" if cn == "6" else "7.1" if cn == "8" else cn
                        self.audio_info.update({
                            "format": track.get("Format", self.audio_info["format"]).strip(),
                            "duration": track.get("Duration", None).strip(),
                            "channels":  cn,
                            "freq": Utils.Format.to_frequency(track.get("SamplingRate")),
                            "dialnorm": Utils.Format.to_int(track.get("extra", {}).get("dialnorm"))
                        })
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyboardInterrupt):
            pass

    def _by_eac3to(self, input_file: Path) -> None:
        """
        Extract audio info using eac3to.
        Cleans control characters and parses format, channels, frequency, and dialnorm.
        """
        try:
            result = subprocess.run(
                [self.checker_launch.absolute(), str(input_file)],
                capture_output=True,
                text=True,
                check=True
            )
            output = result.stdout

            pattern = (
                r'(?P<format>[^\n,]+?),\s*'               # audio format
                r'(?P<channels>[\d\.]+)\s*channels\b'     # channels
                r'(?:.*?(?P<freq>\d+\s*(?:k?hz|hz))\b)?'  # optional frequency
                r'.*?dialnorm:\s*(?P<dialnorm>-?\d+)dB'   # dialnorm
            )

            match = re.search(pattern, output, flags=re.I | re.S)

            if match:
                # Clean control characters and trim whitespace for all groups
                self.audio_info.update({
                    k: (
                        Utils.Format.to_str(v, True)
                        if k in ["format", "channels"]
                        else Utils.Format.to_frequency(v) if k == "freq"
                        else Utils.Format.to_int(v)
                    ) if v is not None else None
                    for k, v in match.groupdict().items()
                })
        except (subprocess.CalledProcessError, ValueError, KeyboardInterrupt):
            pass

    def _normalize_audio_format(self, audio_format: Optional[str]) -> Optional[str]:
        """
        Normalize raw format names to internal constants.
        """
        return {
            "E-AC-3": self.FORMAT_AC3,
            "E-AC3": self.FORMAT_AC3,
            self.FORMAT_AC3: self.FORMAT_AC3,
            "TrueHD (Atmos)": self.FORMAT_THD,
            "MLP FBA": self.FORMAT_THD,
            self.FORMAT_THD: self.FORMAT_THD
        }.get(audio_format, None)
