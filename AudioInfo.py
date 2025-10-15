import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Union, List

from Utils import Utils


class AudioFormat:
    AC3 = "AC3"  # AC-3 / E-AC-3 share same sync word
    EAC3 = "EAC3" # kept for external reference
    TRUEHD = "THD"
    DTS = "DTS"
    AAC = "AAC"
    WAV = "WAV"
    W64 = "W64"


class AudioParser:
    MEDIAINFO = "mediainfo"
    EAC3TO = "eac3to"


class AudioInfo:
    """
    Parses audio files using flexible parser configuration.
    Supports dynamic parser selection based on audio format.
    """

    def __init__(self,
                 parsers: Dict[str, Union[str, Path]],
                 parser_priority: Dict[str, List[str]]):
        """
        :param parsers: Dict with parser name -> executable path
        :param parser_priority: Dict with format -> list of parsers in priority order
        """
        self.parsers = {k: Utils.IO.absolute_self(v) for k, v in parsers.items()}
        self.parser_priority = parser_priority
        self.parser_launch: Optional[Path] = None
        self.audio_info: Optional[Dict[str, Union[str, int, float, None]]] = None

    def parse(self, input_file: Union[str, Path]) -> "AudioInfo":
        input_file = Path(input_file).absolute()
        if not input_file.is_file():
            raise FileNotFoundError(f"Input file {input_file} not found")

        audio_format = self._detect_magic_bytes(input_file)
        if audio_format is None:
            raise RuntimeError("Source file must be in E-AC3 or TrueHD format")

        parser_name = next(
            (p for p in self.parser_priority.get(audio_format, [])
             if self.parsers.get(p) and Utils.IO.is_executable_exists(self.parsers[p])),
            None
        )

        if not parser_name:
            raise RuntimeError(f"No available parser found for format {audio_format}")

        self.parser_launch = self.parsers[parser_name]
        self.audio_info = {
            "format": audio_format,
            "duration": None,
            "channels": None,
            "bitrate": None,
            "freq": None,
            "dialnorm": None,
            "parser_used": parser_name
        }

        getattr(self, f"_by_{parser_name}")(input_file)

        return self

    # ----------------- Properties -----------------
    @property
    def dialnorm(self) -> Optional[int]:
        return self.audio_info.get("dialnorm") if self.audio_info else None

    @property
    def freq(self) -> Optional[int]:
        return self.audio_info.get("freq") if self.audio_info else None

    @property
    def format(self) -> Optional[str]:
        return self.audio_info.get("format") if self.audio_info else None

    @property
    def duration(self) -> Optional[float]:
        return self.audio_info.get("duration") if self.audio_info else None

    @property
    def channels(self) -> Optional[str]:
        return self.audio_info.get("channels") if self.audio_info else None

    @property
    def parser_used(self) -> Optional[str]:
        return self.audio_info.get("parser_used") if self.audio_info else None

    # ----------------- Private parsers -----------------
    def _by_mediainfo(self, input_file: Path):
        """Parse audio info using MediaInfo."""
        try:
            result = subprocess.run(
                [self.parser_launch.absolute(), str(input_file), '--Output=JSON'],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout)
            for track in data.get("media", {}).get("track", []):
                if track.get("@type") != "Audio":
                    continue

                channels = str(Utils.Format.to_int(track.get("Channels")))
                channels = "5.1" if channels == "6" else "7.1" if channels == "8" else channels

                fields = {
                    "format": self._normalize_audio_format(
                        Utils.Format.to_str(track.get("Format", self.audio_info["format"]), True)),
                    "duration": Utils.Format.to_float(track.get("Duration", 0)),
                    "bitrate": int(Utils.Format.to_int(track.get("BitRate", 0)) / 1000),
                    "channels": channels,
                    "freq": Utils.Format.to_frequency(track.get("SamplingRate")),
                    "dialnorm": Utils.Format.to_int(track.get("extra", {}).get("dialnorm"))
                }
                self.audio_info.update(fields)
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyboardInterrupt):
            pass

    def _by_eac3to(self, input_file: Path):
        """Parse audio info using eac3to."""
        try:
            output = subprocess.run(
                [self.parser_launch.absolute(), str(input_file)],
                capture_output=True,
                text=True,
                check=True
            ).stdout

            # TODO: parse DTS with core
            # DTS Master Audio, 5.0 channels, 24 bits, 48kHz
            # (core: DTS, 5.0 channels, 1509kbps, 48kHz)

            pattern = (
                r'^(?P<format>[^,]+?),\s*'
                r'(?P<channels>[\d\.]+)\s*channels\b'
                r'(?:.*?(?P<duration>\d+:\d+:\d+))?'
                r'(?:.*?(?P<bitrate>\d+)kbps\b)?'
                r'(?:.*?(?P<freq>\d+\s*(?:k?hz|hz))\b)?'
                r'(?:.*?dialnorm:\s*(?P<dialnorm>-?\d+)dB\b)?'
            )
            if match := re.search(pattern, output, flags=re.I | re.S):
                fields = {
                    "format": self._normalize_audio_format(
                        Utils.Format.to_str(match.group("format"), True)),
                    "duration": Utils.Format.to_seconds(match.group("duration")),
                    "bitrate": Utils.Format.to_int(match.group("bitrate")),
                    "channels": Utils.Format.to_str(match.group("channels")),
                    "freq": Utils.Format.to_frequency(match.group("freq")),
                    "dialnorm": Utils.Format.to_int(match.group("dialnorm"))
                }
                self.audio_info.update(fields)
        except (subprocess.CalledProcessError, ValueError, KeyboardInterrupt):
            pass

    # ----------------- Helpers -----------------
    def _detect_magic_bytes(self, file: Path) -> Optional[str]:
        result: Optional[str] = None

        try:
            header = file.open("rb").read(32)
            if header.startswith(b"\x0B\x77"):
                result = AudioFormat.AC3
            elif header.startswith(b"\xF8\x72\x6F\xBA"):
                result = AudioFormat.TRUEHD
            elif header.startswith(b"\x7F\xFE\x80\x01"):
                result = AudioFormat.DTS
            elif header.startswith((b"\xFF\xF1", b"\xFF\xF9")):
                result = AudioFormat.AAC
            elif len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WAVE":
                result = AudioFormat.WAV
            else:
                w64_guid = b"\x01\xB7\x44\x0E\xB6\x7D\x11\xD1\xA1\xC0\x00\xC0\x4F\xC3\x5D\xE0"
                if header.startswith(w64_guid):
                    result = AudioFormat.W64
        except (FileNotFoundError, OSError):
            pass
        return result

    def _normalize_audio_format(self, fmt: Optional[str]) -> Optional[str]:
        return {
            # mediainfo
            "AC-3": AudioFormat.AC3,
            "E-AC-3": AudioFormat.EAC3,
            "MLP FBA": AudioFormat.TRUEHD,
            "PCM": AudioFormat.WAV,
            # eac3to
            "AC3": AudioFormat.AC3,
            "E-AC3": AudioFormat.EAC3,
            "TrueHD (Atmos)": AudioFormat.TRUEHD,
            "WAV": AudioFormat.WAV,

            "AAC": AudioFormat.AAC,
            AudioFormat.EAC3: AudioFormat.EAC3,
            AudioFormat.TRUEHD: AudioFormat.TRUEHD
        }.get(fmt, None)

