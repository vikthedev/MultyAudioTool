import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Union

from Utils import Utils


# -----------------------------
# Audio format constants
# -----------------------------
class AudioFormat:
    AC3 = "AC3"  # AC-3 / E-AC-3 share same sync word
    EAC3 = "EAC3"  # kept for external reference
    TRUEHD = "THD"
    DTS = "DTS"
    AAC = "AAC"
    WAV = "WAV"
    W64 = "W64"


class AudioInfo:

    FORMAT_AC3: str = AudioFormat.AC3
    FORMAT_THD: str = AudioFormat.TRUEHD

    def __init__(self, mediainfo_launch: Union[str, Path], eac3to_launch: Union[str, Path]):
        """
        Initialize AudioInfo
        """
        self.DEFAULT_DIALNORM: int = -31

        self.mediainfo_launch: Path = Utils.IO.absolute_self(mediainfo_launch)
        self.eac3to_launch: Path = Utils.IO.absolute_self(eac3to_launch)
        self.parser_launch: Optional[Path] = None

        self.audio_info: Optional[Dict[str, Union[str, int, None]]] = None

    def parse(self, input_file: Union[str, Path]) -> "AudioInfo":
        """
        Parse audio file and populate audio_info dictionary.
        Detects audio format using magic numbers first, then extracts detailed info
        (channels, frequency, dialnorm) using the selected parser (eac3to or mediainfo).
        """

        input_file: Path = Path(input_file).absolute()

        if not input_file.is_file():
            raise sys.exit(f'FileNotFoundError: Input file {input_file} not found')

        # Detect audio format early using magic numbers from file header

        audio_format = self._detect_magic_bytes(input_file)

        if audio_format not in [self.FORMAT_AC3, self.FORMAT_THD]:
            raise sys.exit(
                Utils.Console.cprint(f'RuntimeError: Source file must be in E-AC3 or TrueHD format', 'red'))

        parser_priority = ["eac3to"] if audio_format == self.FORMAT_THD else ["mediainfo", "eac3to"]

        parser_name = None
        # Choose the best available parser
        for name in parser_priority:
            parser_launch = getattr(self, f"{name}_launch", None)
            func = getattr(self, f"_by_{name}", None)
            if parser_launch and func and Utils.IO.is_executable_exists(parser_launch):
                self.parser_launch = parser_launch
                parser_name = name
                break

        if parser_name is not None and audio_format:
            # Parse detailed info only if parser is available and format is recognized
            self.audio_info = {
                "format": audio_format,
                "duration": None,
                "channels": None,
                "bitrate": None,
                "freq": None,
                "dialnorm": None,
                "parser_used": parser_name
            }

            getattr(self, f"_by_{parser_name}")(input_file=input_file)
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
    def duration(self) -> Optional[float]:
        return self.audio_info.get("duration", None)

    @property
    def channels(self) -> Optional[str]:
        return self.audio_info.get("channels", None)

    @property
    def parser_used(self) -> Optional[str]:
        """
        Return actual parser value or None.
        """
        return self.audio_info.get("parser_used", None)

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
                [self.parser_launch.absolute(), str(input_file), '--Output=JSON'],
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
                            "duration": Utils.Format.to_float(track.get("Duration", 0).strip()),
                            "bitrate": int(Utils.Format.to_int(track.get("BitRate", 0)) / 1000),
                            "channels":  cn,
                            "freq": Utils.Format.to_frequency(track.get("SamplingRate")),
                            "dialnorm": Utils.Format.to_int(track.get("extra", {}).get("dialnorm")),
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
                [self.parser_launch.absolute(), str(input_file)],
                capture_output=True,
                text=True,
                check=True
            )
            output = result.stdout

            # TODO: parse DTS with core
            # DTS Master Audio, 5.0 channels, 24 bits, 48kHz
            # (core: DTS, 5.0 channels, 1509kbps, 48kHz)
            pattern = (
                r'^'
                r'(?P<format>[^,]+?),\s*'  # audio format
                r'(?P<channels>[\d\.]+)\s*channels\b'  # channels
                r'(?:.*?(?P<duration>\d+:\d+:\d+))?'  # duration HH:MM:SS
                r'(?:.*?(?P<bitrate>\d+)kbps\b)?'  # bitrate
                r'(?:.*?(?P<freq>\d+\s*(?:k?hz|hz))\b)?'  # optional frequency
                r'(?:.*?dialnorm:\s*(?P<dialnorm>-?\d+)dB\b)?'  # dialnorm
            )

            match = re.search(pattern, output, flags=re.I | re.S)

            if match:
                # Clean control characters and trim whitespace for all groups
                self.audio_info.update({
                    k: (
                        Utils.Format.to_str(v, True)
                        if k in ["format", "channels"]
                        else Utils.Format.to_frequency(v) if k == "freq"
                        else Utils.Format.to_seconds(v) if k == "duration"
                        else Utils.Format.to_int(v)
                    ) if v is not None else None
                    for k, v in match.groupdict().items()
                })
        except (subprocess.CalledProcessError, ValueError, KeyboardInterrupt):
            pass

    def _detect_magic_bytes(self, input_file: Path) -> Optional[str]:
        try:
            with input_file.open("rb") as fh:
                header = fh.read(32)
        except (FileNotFoundError, OSError) as exc:
            return None

        detected: Optional[str] = None

        if header.startswith(b"\x0B\x77"):
            detected = AudioFormat.AC3
        elif header.startswith(b"\xF8\x72\x6F\xBA"):
            detected = AudioFormat.TRUEHD
        elif header.startswith(b"\x7F\xFE\x80\x01"):
            detected = AudioFormat.DTS
        elif header.startswith(b"\xFF\xF1") or header.startswith(b"\xFF\xF9"):
            detected = AudioFormat.AAC
        elif len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WAVE":
            detected = AudioFormat.WAV
        else:
            # W64 GUID per spec (16 bytes)
            W64_GUID = b"\x01\xB7\x44\x0E\xB6\x7D\x11\xD1\xA1\xC0\x00\xC0\x4F\xC3\x5D\xE0"
            if header.startswith(W64_GUID):
                detected = AudioFormat.W64

        return detected

    def _normalize_audio_format(self, audio_format: Optional[str]) -> Optional[str]:
        """
        Normalize raw format names to internal constants.
        """
        return {
            # MediaInfo
            "E-AC-3": AudioFormat.AC3,
            "MLP FBA": AudioFormat.TRUEHD,
            # eac3to
            "E-AC3": AudioFormat.AC3,
            "TrueHD (Atmos)": AudioFormat.TRUEHD,
            # self
            self.FORMAT_AC3: AudioFormat.AC3,
            self.FORMAT_THD: AudioFormat.TRUEHD,
        }.get(audio_format, None)
