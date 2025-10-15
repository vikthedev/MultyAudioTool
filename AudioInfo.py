import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Union, List

from Utils import Utils


class AudioFormat:
    AC3 = "AC3"  # AC-3 / E-AC-3 share same sync word
    EAC3 = "EAC3"
    TRUEHD = "THD"
    DTS = "DTS"
    AAC = "AAC"
    WAV = "WAV"
    W64 = "W64"


class AudioInfo:
    """
    Parses audio files using flexible parser configuration.
    Each property is normalized via setters.
    """

    class Parser:
        MEDIAINFO = "mediainfo"
        EAC3TO = "eac3to"

    def __init__(self,
                 parsers: Dict[str, Union[str, Path]],
                 parser_priority: Dict[str, List[str]]) -> None:
        self.parsers = {k: Utils.IO.absolute_self(v) for k, v in parsers.items()}
        self.parser_priority = parser_priority
        self.parser_launch: Optional[Path] = None

        # internal normalized fields
        self._format: Optional[str] = None
        self._duration: Optional[float] = None
        self._channels: Optional[str] = None
        self._bitrate: Optional[int] = None
        self._freq: Optional[int] = None
        self._dialnorm: Optional[int] = None
        self._parser_used: Optional[str] = None

        self._error: Optional[str] = None

    # ------------------------------------------------------------------ #
    #                             Properties                             #
    # ------------------------------------------------------------------ #

    @property
    def error(self) -> Optional[Union[FileNotFoundError, RuntimeError]]:
        return self._error

    @error.setter
    def error(self, value: str) -> None:
        self._error = value

    @property
    def format(self) -> Optional[str]:
        return self._format

    @format.setter
    def format(self, value: Optional[str]) -> None:
        self._format = self._normalize_audio_format(Utils.Format.to_str(value, True), self._format)

    @property
    def duration(self) -> Optional[float]:
        return self._duration

    @duration.setter
    def duration(self, value: Optional[Union[str, float, int]]) -> None:
        self._duration = Utils.Format.to_float(value)

    @property
    def channels(self) -> Optional[str]:
        return self._channels

    @channels.setter
    def channels(self, value: Optional[Union[str, int]]) -> None:
        v = Utils.Format.to_str(value, True)
        self._channels = "5.1" if v == "6" else "7.1" if v == "8" else v

    @property
    def bitrate(self) -> Optional[int]:
        return self._bitrate

    @bitrate.setter
    def bitrate(self, value: Optional[Union[str, int]]) -> None:
        self._bitrate = Utils.Format.to_int(value)

    @property
    def freq(self) -> Optional[int]:
        return self._freq

    @freq.setter
    def freq(self, value: Optional[Union[str, int]]) -> None:
        self._freq = Utils.Format.to_frequency(value)

    @property
    def dialnorm(self) -> Optional[int]:
        return self._dialnorm

    @dialnorm.setter
    def dialnorm(self, value: Optional[Union[str, int]]) -> None:
        self._dialnorm = Utils.Format.to_int(value)

    @property
    def parser_used(self) -> Optional[str]:
        return self._parser_used

    @parser_used.setter
    def parser_used(self, value: Optional[str]) -> None:
        self._parser_used = Utils.Format.to_str(value, True)

    # ------------------------------------------------------------------ #
    #                           Public Methods                           #
    # ------------------------------------------------------------------ #
    def parse(self, input_file: Union[str, Path]) -> "AudioInfo":
        input_file = Path(input_file).absolute()
        if not input_file.is_file():
            self.error = FileNotFoundError(f"Input file {input_file} not found")
        else:
            audio_format = self._detect_magic_bytes(input_file)
            if audio_format is None:
                self.error = RuntimeError(f"No available parser found for format {audio_format}")
            else:
                parser_name = next(
                    (p for p in self.parser_priority.get(audio_format, [])
                     if self.parsers.get(p) and Utils.IO.is_executable_exists(self.parsers[p])),
                    None
                )
                if not parser_name:
                    self.error = RuntimeError("Unknown source file format")
                else:
                    self.parser_used = parser_name
                    self.parser_launch = self.parsers[parser_name]
                    getattr(self, f"_by_{parser_name}")(input_file)
        return self

    def as_dict(self) -> Dict[str, Optional[Union[str, int, float]]]:
        """Return normalized audio metadata as dictionary."""
        return {
            "format": self.format,
            "duration": self.duration,
            "channels": self.channels,
            "bitrate": self.bitrate,
            "freq": self.freq,
            "dialnorm": self.dialnorm,
            "parser_used": self.parser_used
        }

    # ------------------------------------------------------------------ #
    #                           Private Parsers                          #
    # ------------------------------------------------------------------ #
    def _by_mediainfo(self, input_file: Path) -> None:
        """Parse audio info using MediaInfo."""
        try:
            result = subprocess.run(
                [self.parser_launch.absolute(), str(input_file), "--Output=JSON"],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout)
            for track in data.get("media", {}).get("track", []):
                if track.get("@type") != "Audio":
                    continue

                self.format = track.get("Format", self.format)
                self.duration = track.get("Duration", 0)
                self.bitrate = int(Utils.Format.to_int(track.get("BitRate", 0)) / 1000)
                self.channels = track.get("Channels")
                self.freq = track.get("SamplingRate")
                self.dialnorm = track.get("extra", {}).get("dialnorm")
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyboardInterrupt):
            pass

    def _by_eac3to(self, input_file: Path) -> None:
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
                r"^(?P<format>[^,]+?),\s*"
                r"(?P<channels>[\d\.]+)\s*channels\b"
                r"(?:.*?(?P<duration>\d+:\d+:\d+))?"
                r"(?:.*?(?P<bitrate>\d+)kbps\b)?"
                r"(?:.*?(?P<freq>\d+\s*(?:k?hz|hz))\b)?"
                r"(?:.*?dialnorm:\s*(?P<dialnorm>-?\d+)dB\b)?"
            )

            if match := re.search(pattern, output, flags=re.I | re.S):
                self.format = match.group("format")
                self.channels = match.group("channels")
                self.duration = Utils.Format.to_seconds(match.group("duration"))
                self.bitrate = match.group("bitrate")
                self.freq = match.group("freq")
                self.dialnorm = match.group("dialnorm")

        except (subprocess.CalledProcessError, ValueError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------ #
    #                              Helpers                               #
    # ------------------------------------------------------------------ #
    def _detect_magic_bytes(self, file: Path) -> Optional[str]:
        """Detect audio format by magic bytes."""
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

    def _normalize_audio_format(self, fmt: Optional[str], default: Optional[str] = None) -> Optional[str]:
        """Normalize format names across parsers."""
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
            # common
            "AAC": AudioFormat.AAC,
            AudioFormat.EAC3: AudioFormat.EAC3,
            AudioFormat.TRUEHD: AudioFormat.TRUEHD
        }.get(fmt, default)


def main():
    # 1️⃣  Налаштування доступних парсерів
    parsers = {
        AudioInfo.Parser.MEDIAINFO: Path(r".\bin\MediaInfo.exe"),
        AudioInfo.Parser.EAC3TO: Path(r".\bin\eac3to\eac3to.exe"),
    }

    # 2️⃣  Визначення підтримуваних форматів та їх пріоритету парсерів
    parser_priority = {
        AudioFormat.TRUEHD: [AudioInfo.Parser.EAC3TO],
        AudioFormat.EAC3: [AudioInfo.Parser.EAC3TO, AudioInfo.Parser.MEDIAINFO, AudioInfo.Parser.EAC3TO],
        AudioFormat.AC3: [AudioInfo.Parser.MEDIAINFO, AudioInfo.Parser.EAC3TO],
        AudioFormat.DTS: [AudioInfo.Parser.EAC3TO],
        AudioFormat.WAV: [AudioInfo.Parser.MEDIAINFO],
    }

    # 3️⃣  Ініціалізація головного об’єкта
    ai = AudioInfo(parsers, parser_priority)

    # 4️⃣  Аналіз аудіофайлу
    file_path = Path(r"d:\Movies\Tulsa.King.S03\Tulsa.King.S03E03.eac3")
    ai.parse(file_path)

    # 5️⃣  Отримання результатів у структурованому вигляді
    info = ai.as_dict()
    print("=== Audio Metadata ===")
    for k, v in info.items():
        print(f"{k:<12}: {v}")

    # 6️⃣  Або просто для дебагу
    print("\nAs JSON:")
    import json
    print(json.dumps(info, indent=4))


if __name__ == "__main__":
    main()
